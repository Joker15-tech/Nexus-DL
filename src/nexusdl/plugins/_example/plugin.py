"""Plugin d'exemple NexusDL — référence pour développeurs.

Ce plugin est **volontairement complet** : il implémente tous les hooks
disponibles et démontre chaque accesseur de l'API (`notify`, `library`,
`http`, `fs`, `config`). Il est fonctionnel et peut servir de base à un
vrai plugin.

Il est chargé par défaut par NexusDL (dossier `_example`) mais reste
inoffensif :
    - Aucune écriture dans la bibliothèque sans permission explicite.
    - Aucune requête HTTP sans permission `network_http`.
    - Toutes les erreurs sont capturées et loggées (jamais propagées).

Désactiver ce plugin en production ::

    # config/config.yaml
    plugins:
      disabled:
        - _example

Pour créer votre propre plugin à partir de cet exemple ::

    cp -r src/nexusdl/plugins/_example plugins/my_plugin
    # Éditer nexus.plugin.yaml (nom, version, description, hooks, permissions)
    # Modifier plugin.py selon vos besoins

Documentation complète : docs/source/development/plugins.md
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from nexusdl.plugins.api import (
    MangaInfo,
    NotificationResult,
    PluginAPI,
    PluginContext,
)
from nexusdl.plugins.hooks import (
    ChapterPayload,
    DownloadPayload,
    ErrorPayload,
    LibraryScanPayload,
    LifecyclePayload,
    MangaPayload,
    PackagingPayload,
    SearchPayload,
)

# ============================================================================
#  Configuration du plugin (constantes locales)
# ============================================================================

#: Durée de vie du cache HTTP (secondes). Au-delà, la réponse est refetchée.
CACHE_TTL_SECONDS: int = 3600  # 1 heure

#: Nombre minimum de résultats pour considérer une recherche utile.
MIN_SEARCH_RESULTS: int = 1

#: Taille maximale du cache (nombre d'entrées). LRU simple au-delà.
MAX_CACHE_ENTRIES: int = 100

#: Regex pour détecter les chapitres "spéciaux" (Bonus, Extra, Omake, etc.).
SPECIAL_CHAPTER_PATTERN: re.Pattern[str] = re.compile(
    r"\b(bonus|extra|omake|sp[ée]cial|hs|hors[- ]s[ée]rie|side[- ]story)\b",
    re.IGNORECASE,
)

#: URL du service de monitoring externe utilisé par `on_library_scan`.
#: À remplacer par votre propre endpoint en production.
MONITORING_ENDPOINT: str = "https://httpbin.org/post"

#: Préfixe des tags auto-ajoutés (pour les retrouver/remplacer facilement).
AUTO_TAG_PREFIX: str = "auto:"


# ============================================================================
#  Plugin
# ============================================================================


class ExamplePlugin:
    """Plugin d'exemple — référence pour développeurs NexusDL.

    **Ordre de lecture recommandé** :

        1. ``__init__`` — comment le loader injecte `api` et `context`.
        2. ``on_load`` / ``on_unload`` — cycle de vie.
        3. ``pre_search`` / ``post_search`` — chaînage de payloads.
        4. ``pre_download`` / ``post_download`` — accès aux stats.
        5. ``on_new_chapter`` — notification utilisateur.
        6. ``on_manga_update`` — réaction aux changements de bibliothèque.
        7. ``on_library_scan`` — intégration avec un service externe.
        8. ``on_error`` — gestion d'erreur conditionnelle.

    Le plugin maintient un **cache en mémoire** (dict) des recherches
    récentes, synchronisé avec un cache disque (JSON) dans le sandbox
    filesystem. Cela démontre les deux approches :

        - Cache mémoire : rapide, perdu au redémarrage.
        - Cache disque : persistant entre sessions, plus lent.

    Attributes:
        api: Façade d'accès aux ressources NexusDL (scopée aux permissions).
        context: Métadonnées du plugin (nom, version, logger).
        logger: Alias de ``context.logger`` pour un accès direct.
        _search_cache: Cache mémoire ``{query_hash: (timestamp, payload_data)}``.
        _initialized: Flag d'initialisation (protège contre un on_load double).
    """

    #: Version du plugin — doit matcher le manifest, mais utile en debug.
    VERSION: ClassVar[str] = "1.0.0"

    def __init__(self, api: PluginAPI, context: PluginContext) -> None:
        """Initialise le plugin.

        Le loader inspecte la signature de ``__init__`` et injecte
        automatiquement ``api`` et ``context`` s'ils sont attendus.
        Un plugin minimal peut avoir ``__init__(self)`` sans arguments.

        Args:
            api: Façade API scopée aux permissions déclarées dans le manifest.
                Toute opération sur un accesseur vérifie la permission
                correspondante et lève ``PermissionDenied`` sinon.
            context: Métadonnées du plugin (nom, version, chemin, logger
                préconfiguré avec ``plugin=<name>`` en `extra`).
        """
        self.api = api
        self.context = context
        self.logger = context.logger

        # Cache mémoire : {query_hash: (timestamp_epoch, payload_dict)}
        self._search_cache: dict[str, tuple[float, dict[str, Any]]] = {}

        # Flag pour éviter un double on_load (le loader protège déjà, mais
        # c'est une bonne défense en profondeur).
        self._initialized = False

        self.logger.debug(
            "ExamplePlugin instancié (permissions={}, api_version={})",
            len(self.api.permissions),
            self.context.api_version,
        )

    # ------------------------------------------------------------------------
    #  Cycle de vie
    # ------------------------------------------------------------------------

    async def on_load(self, payload: LifecyclePayload) -> None:
        """Initialise le plugin au chargement.

        C'est le bon endroit pour :
            - Créer les dossiers du sandbox (``cache/``, ``data/``).
            - Charger un état persistant.
            - Vérifier la configuration.
            - Logger un message de bienvenue.

        Args:
            payload: Contient ``plugin_name`` et ``plugin_path``.
        """
        if self._initialized:
            self.logger.warning("on_load appelé deux fois — ignoré")
            return

        self.logger.info(
            "Démarrage de {} v{} (chemin: {})",
            payload.plugin_name,
            self.VERSION,
            payload.plugin_path,
        )

        # Prépare les dossiers du sandbox (idempotent)
        self.api.fs.ensure_sandbox_dirs()

        # Vide le dossier tmp (fichiers éphémères d'une session précédente)
        cleared = self.api.fs.clear_tmp()
        if cleared > 0:
            self.logger.debug("Nettoyage de {} fichier(s) temporaire(s)", cleared)

        # Charge le cache depuis le disque si présent
        await self._load_cache_from_disk()

        # Notification de démarrage (si permission)
        if self.api.has_permission("notifications"):
            try:
                await self.api.notify.info(
                    "Plugin chargé",
                    f"{payload.plugin_name} v{self.VERSION} est prêt.",
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                self.logger.warning("Échec de la notification de démarrage : {}", exc)

        self._initialized = True

    async def on_unload(self, payload: LifecyclePayload) -> None:
        """Nettoie les ressources au déchargement.

        C'est le bon endroit pour :
            - Sauvegarder un état persistant.
            - Fermer des connexions.
            - Libérer des ressources.

        **Un échec ici n'empêche pas le déchargement** — le loader capture
        et logue l'erreur mais continue.

        Args:
            payload: Contient ``plugin_name`` et ``plugin_path``.
        """
        self.logger.info("Arrêt de {}", payload.plugin_name)

        # Sauvegarde le cache sur disque (best-effort)
        try:
            await self._save_cache_to_disk()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Échec de la sauvegarde du cache : {}", exc)

        # Vide le cache mémoire
        self._search_cache.clear()
        self._initialized = False

    # ------------------------------------------------------------------------
    #  Hooks de recherche
    # ------------------------------------------------------------------------

    async def pre_search(self, payload: SearchPayload) -> SearchPayload:
        """Normalise la requête avant la recherche multi-sites.

        Ce hook est **cancellable** : lever une exception annule la
        recherche. Un plugin peut donc :
            - Modifier ``payload.query``.
            - Ajouter/retirer des langues dans ``payload.languages``.
            - Forcer ``payload.include_adult``.
            - Annuler en levant une exception.

        Args:
            payload: Payload de recherche (query, sites, langues...).

        Returns:
            Payload modifié (ou le même objet muté).

        Raises:
            ValueError: Si la query est vide après normalisation — annule
                la recherche (comportement voulu).
        """
        original_query = payload.query

        # --- Normalisation ---
        payload.query = payload.query.strip()
        # Collapse les espaces multiples : "one    piece" → "one piece"
        payload.query = re.sub(r"\s+", " ", payload.query)

        # --- Validation ---
        if not payload.query:
            self.logger.warning("Query vide après normalisation — annulation de la recherche")
            msg = "Query vide"
            raise ValueError(msg)

        # --- Enrichissement des métadonnées (traçabilité) ---
        payload.metadata["normalized_by"] = self.context.name
        payload.metadata["original_query"] = original_query

        # --- Vérifie le cache (log seulement, on ne bloque pas la recherche) ---
        cached = self._get_cached_search(payload.query)
        if cached is not None:
            age = time.time() - cached[0]
            self.logger.debug(
                "Recherche similaire en cache (âge: {:.0f}s, {} résultats)",
                age,
                cached[1].get("total_results", 0),
            )
            payload.metadata["cache_hit"] = True
            payload.metadata["cache_age_seconds"] = int(age)
        else:
            payload.metadata["cache_hit"] = False

        if original_query != payload.query:
            self.logger.debug("Query normalisée : {!r} → {!r}", original_query, payload.query)

        return payload

    async def post_search(self, payload: SearchPayload) -> SearchPayload:
        """Filtre et enrichit les résultats après la recherche.

        Ce hook est **non-cancellable** : lever une exception annule
        seulement la contribution de ce plugin, pas la recherche globale.

        Args:
            payload: Payload de recherche avec ``results`` peuplé.

        Returns:
            Payload avec résultats filtrés et annotés.
        """
        initial_count = len(payload.results)

        # --- Filtre 1 : retire les résultats sans couverture ---
        payload.results = [
            r for r in payload.results
            if r.get("cover_url") or r.get("cover")
        ]

        # --- Filtre 2 : déduplique par URL (au cas où un même manga apparaît
        #     sur plusieurs pages) ---
        seen_urls: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for result in payload.results:
            url = result.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            deduped.append(result)
        payload.results = deduped

        # --- Enrichissement : marque les résultats avec un titre "suspect" ---
        #     (très long = probablement du bruit publicitaire)
        for result in payload.results:
            title = result.get("title", "")
            if len(title) > 200:  # noqa: PLR2004
                result["_suspicious"] = True

        # --- Mise à jour du compte total ---
        payload.total_results = len(payload.results)

        filtered = initial_count - payload.total_results
        if filtered > 0:
            self.logger.debug(
                "Résultats filtrés : {} → {} ({} retirés)",
                initial_count,
                payload.total_results,
                filtered,
            )

        # --- Met en cache cette recherche (pour pre_search de la prochaine) ---
        if payload.total_results >= MIN_SEARCH_RESULTS:
            self._store_cached_search(payload)

        payload.metadata["filtered_by"] = self.context.name
        payload.metadata["filtered_count"] = filtered

        return payload

    # ------------------------------------------------------------------------
    #  Hooks de téléchargement
    # ------------------------------------------------------------------------

    async def pre_download(self, payload: DownloadPayload) -> DownloadPayload:
        """Valide et enrichit un téléchargement avant son démarrage.

        Peut :
            - Refuser un téléchargement (lever une exception).
            - Modifier la destination (``payload.dest``).
            - Changer le format (``payload.format``).
            - Filtrer les chapitres (``payload.chapter_ids``).

        Args:
            payload: Payload de téléchargement.

        Returns:
            Payload potentiellement modifié.

        Raises:
            ValueError: Si la destination n'est pas absolue — annule le
                téléchargement (sécurité).
        """
        dest_path = Path(payload.dest)
        if not dest_path.is_absolute():
            self.logger.error("Destination non absolue : {}", payload.dest)
            msg = f"Destination doit être absolue : {payload.dest}"
            raise ValueError(msg)

        self.logger.info(
            "Démarrage téléchargement '{}' ({} chapitres, format={})",
            payload.manga_title or payload.manga_id,
            len(payload.chapter_ids),
            payload.format,
        )

        payload.metadata["download_started_at"] = datetime.now(UTC).isoformat()
        payload.metadata["started_by"] = self.context.name

        return payload

    async def post_download(self, payload: DownloadPayload) -> DownloadPayload:
        """Notifie la fin d'un téléchargement avec un résumé.

        Args:
            payload: Payload avec statistiques finales.

        Returns:
            Payload inchangé (hook d'observation).
        """
        success = payload.chapters_downloaded > 0
        total_bytes_mb = payload.bytes_downloaded / (1024 * 1024)

        if success:
            summary = (
                f"{payload.chapters_downloaded} chapitre(s) téléchargé(s) "
                f"({total_bytes_mb:.1f} MiB en {payload.duration_seconds:.0f}s)"
            )
            self.logger.info(summary)

            if self.api.has_permission("notifications"):
                try:
                    await self.api.notify.success(
                        title=f"✓ {payload.manga_title or payload.manga_id}",
                        message=summary,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug("Notification échouée : {}", exc)
        elif payload.chapters_failed > 0:
            self.logger.warning(
                "Téléchargement échoué : {} chapitre(s), erreurs: {}",
                payload.chapters_failed,
                "; ".join(payload.errors[:3]),
            )

        # Enregistre la durée dans les métadonnées pour analyse
        started_str = payload.metadata.get("download_started_at")
        if isinstance(started_str, str):
            try:
                started = datetime.fromisoformat(started_str)
                elapsed = (datetime.now(UTC) - started).total_seconds()
                payload.metadata["total_elapsed_seconds"] = elapsed
            except (ValueError, TypeError):
                pass

        return payload

    # ------------------------------------------------------------------------
    #  Hooks d'empaquetage
    # ------------------------------------------------------------------------

    async def pre_package(self, payload: PackagingPayload) -> PackagingPayload:
        """Prépare l'empaquetage d'un chapitre.

        Peut modifier la liste de pages (retirer des scans publicitaires)
        ou changer le format cible.

        Args:
            payload: Payload d'empaquetage.

        Returns:
            Payload potentiellement modifié.
        """
        self.logger.debug(
            "Empaquetage chapitre {} (format={}, {} pages)",
            payload.chapter_number or payload.chapter_id,
            payload.format,
            len(payload.pages),
        )

        # Enrichit les métadonnées ComicInfo avec un tag "processed_by"
        payload.comic_info.setdefault("tags", [])
        if isinstance(payload.comic_info["tags"], list):
            payload.comic_info["tags"].append(f"packaged-by:{self.context.name}")

        return payload

    async def post_package(self, payload: PackagingPayload) -> PackagingPayload:
        """Observe la fin d'un empaquetage.

        Args:
            payload: Payload avec ``output_path`` et ``output_size_bytes``.

        Returns:
            Payload inchangé.
        """
        if payload.output_path:
            size_mb = payload.output_size_bytes / (1024 * 1024)
            self.logger.debug(
                "Empaqueté : {} ({:.2f} MiB)",
                Path(payload.output_path).name,
                size_mb,
            )
        return payload

    # ------------------------------------------------------------------------
    #  Hooks d'événements
    # ------------------------------------------------------------------------

    async def on_new_chapter(self, payload: ChapterPayload) -> None:
        """Notifie l'utilisateur d'un nouveau chapitre détecté.

        Args:
            payload: Payload du nouveau chapitre.
        """
        # Ignore les chapitres spéciaux (Bonus, Extra) sauf si explicitement
        # activé par la config (permission config_read).
        is_special = bool(SPECIAL_CHAPTER_PATTERN.search(payload.chapter_title or ""))

        notify_special = self.api.config.get(
            "plugins.example.notify_special_chapters",
            default=False,
        )

        if is_special and not notify_special:
            self.logger.debug(
                "Chapitre spécial ignoré : {} ch.{}",
                payload.manga_title,
                payload.chapter_number,
            )
            return

        if not self.api.has_permission("notifications"):
            return

        title = f"Nouveau chapitre : {payload.manga_title or payload.manga_id}"
        if is_special:
            title = f"⭐ {title}"

        message = (
            f"Chapitre {payload.chapter_number} "
            f"({payload.chapter_title or 'sans titre'}) "
            f"disponible sur {payload.site_id}."
        )

        try:
            await self.api.notify.success(title, message)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Échec de notification : {}", exc)

    async def on_manga_update(self, payload: MangaPayload) -> None:
        """Réagit aux mises à jour de la bibliothèque locale.

        Démonstration : ajoute automatiquement un tag basé sur les genres
        du manga (via l'accesseur library, si permission ``write_library``).

        Args:
            payload: Payload avec les changements appliqués.
        """
        self.logger.debug(
            "Manga mis à jour : {} (source={}, {} changement(s))",
            payload.title or payload.manga_id,
            payload.source,
            len(payload.changes),
        )

        # Ajout automatique de tag si le titre contient un mot-clé
        # et que la permission write_library est accordée.
        if not self.api.has_permission("write_library"):
            return
        if payload.source == "manual":
            # Ne touche pas aux modifications manuelles
            return

        # Récupère le manga pour voir ses genres actuels
        manga: MangaInfo | None = await self.api.library.get(payload.manga_id)
        if manga is None:
            return

        # Règle simple : tag "auto:long" si plus de 500 chapitres
        new_tags = list(manga.genres)
        if manga.chapters_count > 500:  # noqa: PLR2004
            auto_tag = f"{AUTO_TAG_PREFIX}long"
            if auto_tag not in new_tags:
                new_tags.append(auto_tag)
                try:
                    await self.api.library.set_tags(payload.manga_id, new_tags)
                    self.logger.debug("Tag '{}' ajouté à {}", auto_tag, manga.title)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("Échec d'ajout de tag : {}", exc)

    async def on_library_scan(self, payload: LibraryScanPayload) -> None:
        """Envoie un rapport de scan vers un service externe.

        Démonstration de l'accesseur ``http`` : le plugin effectue une
        requête POST vers un endpoint de monitoring. Le backend HTTP
        applique automatiquement le rate limiting, les retries et le
        User-Agent rotation — le plugin n'a rien à gérer.

        Args:
            payload: Statistiques du scan.
        """
        if not self.api.has_permission("network_http"):
            self.logger.debug("Permission network_http absente — rapport ignoré")
            return

        report = {
            "plugin": self.context.name,
            "library_path": payload.library_path,
            "total_mangas": payload.total_mangas,
            "new_mangas": payload.new_mangas,
            "removed_mangas": payload.removed_mangas,
            "updated_mangas": payload.updated_mangas,
            "duration_seconds": round(payload.duration_seconds, 2),
            "errors": payload.errors[:10],  # limite à 10 pour éviter les gros payloads
        }

        try:
            response = await self.api.http.post(
                MONITORING_ENDPOINT,
                json_body=report,
                timeout=15.0,
            )
            if response.status_code == 200:  # noqa: PLR2004
                self.logger.debug("Rapport de scan envoyé avec succès")
            else:
                self.logger.warning(
                    "Rapport de scan : HTTP {} (attendu 200)",
                    response.status_code,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, on ne bloque pas
            self.logger.warning("Échec de l'envoi du rapport de scan : {}", exc)

    async def on_error(self, payload: ErrorPayload) -> None:
        """Gère une erreur remontée par un composant NexusDL.

        Démonstration : notification conditionnelle + log enrichi, avec
        récupération automatique pour les erreurs récupérables.

        Args:
            payload: Détails de l'erreur.
        """
        # Log enrichi (toujours)
        self.logger.error(
            "[{}] {}: {}{}",
            payload.site_id or "N/A",
            payload.error_type,
            payload.error_message,
            f" (récupérable)" if payload.recoverable else "",
        )

        # Notification utilisateur (uniquement pour les erreurs critiques)
        if not self.api.has_permission("notifications"):
            return

        is_critical = payload.error_type in {
            "ChapterDownloadError",
            "PackagingError",
            "AuthenticationError",
            "PermissionDenied",
        }

        if is_critical or not payload.recoverable:
            title = f"Erreur : {payload.error_type}"
            message = payload.error_message
            if payload.site_id:
                message = f"[{payload.site_id}] {message}"

            try:
                await self.api.notify.error(title, message[:500])
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Notification d'erreur échouée : {}", exc)

    # ------------------------------------------------------------------------
    #  Cache interne — helpers privés
    # ------------------------------------------------------------------------

    def _query_hash(self, query: str) -> str:
        """Calcule un hash stable d'une query (normalisée).

        Args:
            query: Terme de recherche.

        Returns:
            Hash SHA256 tronqué à 16 caractères.
        """
        normalized = query.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _get_cached_search(
        self,
        query: str,
    ) -> tuple[float, dict[str, Any]] | None:
        """Lit une entrée du cache mémoire si non expirée.

        Args:
            query: Terme de recherche.

        Returns:
            Tuple ``(timestamp, payload_data)`` ou None.
        """
        key = self._query_hash(query)
        entry = self._search_cache.get(key)
        if entry is None:
            return None

        timestamp, _ = entry
        if time.time() - timestamp > CACHE_TTL_SECONDS:
            # Expiré — nettoie à la volée
            del self._search_cache[key]
            return None

        return entry

    def _store_cached_search(self, payload: SearchPayload) -> None:
        """Stocke une recherche en cache (mémoire + disque en arrière-plan).

        Args:
            payload: Payload de recherche à mettre en cache.
        """
        key = self._query_hash(payload.query)
        data = {
            "query": payload.query,
            "total_results": payload.total_results,
            "top_titles": [r.get("title", "") for r in payload.results[:5]],
            "sites": list({r.get("site_id", "") for r in payload.results}),
        }
        self._search_cache[key] = (time.time(), data)

        # LRU simple : si le cache dépasse la limite, retire l'entrée la
        # plus ancienne.
        if len(self._search_cache) > MAX_CACHE_ENTRIES:
            oldest_key = min(
                self._search_cache,
                key=lambda k: self._search_cache[k][0],
            )
            del self._search_cache[oldest_key]

    async def _load_cache_from_disk(self) -> None:
        """Charge le cache depuis le fichier JSON du sandbox."""
        cache_file = self.api.fs.data_dir / "search_cache.json"
        try:
            if not await self.api.fs.exists(cache_file):
                return
            data = await self.api.fs.read_json(cache_file)
            if not isinstance(data, dict):
                return

            loaded = 0
            now = time.time()
            for key, entry in data.items():
                if not isinstance(entry, list) or len(entry) != 2:  # noqa: PLR2004
                    continue
                timestamp, payload_data = entry
                if not isinstance(timestamp, (int, float)):
                    continue
                # Ne charge que les entrées non expirées
                if now - timestamp < CACHE_TTL_SECONDS:
                    self._search_cache[key] = (float(timestamp), payload_data)
                    loaded += 1

            if loaded > 0:
                self.logger.debug("{} entrée(s) de cache rechargée(s) depuis le disque", loaded)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Cache disque illisible (première exécution ?) : {}", exc)

    async def _save_cache_to_disk(self) -> None:
        """Sauvegarde le cache dans le fichier JSON du sandbox."""
        cache_file = self.api.fs.data_dir / "search_cache.json"
        try:
            await self.api.fs.write_json(cache_file, self._search_cache)
            self.logger.debug("Cache sauvegardé ({} entrée(s))", len(self._search_cache))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Échec de la sauvegarde du cache : {}", exc)


# ============================================================================
#  Point d'entrée alternatif : factory
# ============================================================================
# Le loader supporte aussi bien une classe qu'une fonction factory. Une
# factory est utile quand on veut initialiser un état partagé entre
# plusieurs instances (singleton) ou retourner une sous-classe selon
# la configuration.
#
# Exemple :
#
# def create_plugin(api: PluginAPI, context: PluginContext) -> ExamplePlugin:
#     """Factory alternative — décommenter et changer `entrypoint` dans le manifest."""
#     if api.config.get("plugins.example.mode") == "aggressive":
#         return ExamplePlugin(api, context)
#     return ExamplePlugin(api, context)
#
# Pour utiliser la factory, changer dans nexus.plugin.yaml :
#     entrypoint: "plugin:create_plugin"


# ============================================================================
#  Exports
# ============================================================================

__all__ = ["ExamplePlugin"]
