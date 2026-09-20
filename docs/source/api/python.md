
# 🐍 API Python — NexusDL

<div align="center">

**Utilisez NexusDL comme bibliothèque Python dans vos scripts.**

*API haut niveau, typée, async-first, prête pour la production.*

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Async](https://img.shields.io/badge/Async-asyncio-00f0ff?style=for-the-badge)](https://docs.python.org/3/library/asyncio.html)
[![Typed](https://img.shields.io/badge/Typed-mypy%20strict-ff00e5?style=for-the-badge)](https://mypy-lang.org/)
[![Stable](https://img.shields.io/badge/API-Stable-00ff88?style=for-the-badge)](https://semver.org/)

[🚀 Quick Start](#-quick-start) •
[📦 Classe NexusDL](#-classe-nexusdl) •
[🔧 Modèles](#-modèles) •
[🎣 Callbacks](#-callbacks) •
[🧩 Plugins](#-plugins) •
[🕷️ Parsers](#-parsers) •
[🚨 Exceptions](#-exceptions)

</div>

---

## 📖 Introduction

NexusDL expose une **API Python de haut niveau** permettant d'intégrer facilement ses fonctionnalités dans vos propres scripts, applications ou services.

### 🎯 Cas d'usage

- 📜 **Scripts d'automatisation** — télécharger régulièrement vos mangas préférés
- 🤖 **Bots Discord/Telegram** — créer un bot qui télécharge sur demande
- 🌐 **Applications web** — intégrer NexusDL dans un service existant
- 🔬 **Recherche** — analyser les métadonnées de mangas
- 🏗️ **CI/CD** — vérifier qu'un site fonctionne toujours

### ✨ Caractéristiques

| Caractéristique | Détail |
|-----------------|--------|
| **Async-first** | Toutes les méthodes I/O sont `async` |
| **Typé** | 100% typé, vérifié avec mypy strict |
| **Pydantic v2** | Validation stricte des modèles |
| **Context manager** | `async with NexusDL() as nexus:` |
| **Callbacks** | Progression temps réel |
| **Extensible** | Ajout de parsers custom |
| **Compatible** | Python 3.12, 3.13, 3.14 |

### 📋 Prérequis

```bash
# Installer NexusDL
pip install nexusdl

# Installer les navigateurs Playwright (pour Cloudflare)
playwright install chromium
```

---

## 🚀 Quick Start

### 📦 Import basique

```python
from nexusdl import NexusDL
from nexusdl.core.models import Manga, Chapter, PackagingFormat
```

### ⚡ Exemple minimal

```python
import asyncio
from pathlib import Path

from nexusdl import NexusDL


async def main() -> None:
    """Exemple minimal : recherche et téléchargement."""
    async with NexusDL() as nexus:
        # Rechercher
        results = await nexus.search("one piece", languages=["en"])
        print(f"Trouvé {len(results)} résultats")

        # Récupérer le premier manga
        manga = await nexus.get_manga(results[0].url)
        print(f"Manga : {manga.title} ({len(manga.chapters)} chapitres)")

        # Télécharger les 3 premiers chapitres en CBZ
        async for result in nexus.download(
            manga,
            dest=Path("~/Manga").expanduser(),
            fmt=PackagingFormat.CBZ,
            chapters=manga.chapters[:3],
        ):
            print(f"✅ {result.chapter.title} → {result.output_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 🎯 Exemple complet avec progression

```python
import asyncio
from pathlib import Path

from nexusdl import NexusDL
from nexusdl.core.models import DownloadProgress, PackagingFormat


async def on_progress(progress: DownloadProgress) -> None:
    """Affiche la progression à chaque étape."""
    bar_length = 30
    filled = int(bar_length * progress.progress)
    bar = "█" * filled + "░" * (bar_length - filled)

    print(
        f"\r[{bar}] {progress.progress * 100:5.1f}% "
        f"({progress.current_chapter.title})",
        end="",
        flush=True,
    )


async def main() -> None:
    """Télécharge un manga complet avec suivi."""
    async with NexusDL() as nexus:
        # Rechercher sur plusieurs sites
        results = await nexus.search(
            "solo leveling",
            sites=["mangadex", "asurascans"],
            languages=["fr", "en"],
            include_adult=False,
        )

        if not results:
            print("Aucun résultat")
            return

        # Prendre le premier résultat
        manga = await nexus.get_manga(results[0].url)
        print(f"\n📖 {manga.title} — {len(manga.chapters)} chapitres\n")

        # Télécharger avec progression
        async for result in nexus.download(
            manga,
            dest=Path("~/Manga").expanduser(),
            fmt=PackagingFormat.CBZ,
            on_progress=on_progress,
        ):
            print(f"\n✅ {result.output_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 📦 Classe `NexusDL`

La classe principale qui expose toute l'API publique.

### 🏗️ Constructeur

```python
class NexusDL:
    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        config: NexusConfig | None = None,
        registry: SiteRegistry | None = None,
        session_factory: SessionFactory | None = None,
        auto_start: bool = False,
    ) -> None:
        """Initialise NexusDL.

        Args:
            config_path: Chemin vers le fichier `config.yaml`.
                Si `None`, utilise les chemins XDG par défaut.
            config: Instance de configuration directe (alternatif à `config_path`).
            registry: Registre de sites custom. Si `None`, utilise le registre par défaut.
            session_factory: Factory de sessions HTTP (avancé).
            auto_start: Si `True`, démarre immédiatement les ressources
                (pool Playwright, base de données, etc.).

        Raises:
            ConfigurationError: Si la configuration est invalide.

        Example:
            >>> async with NexusDL() as nexus:
            ...     results = await nexus.search("one piece")
        """
```

### 🔄 Context Manager

`NexusDL` implémente le protocole de contexte asynchrone :

```python
async with NexusDL() as nexus:
    # Utiliser nexus ici
    ...

# Les ressources sont automatiquement libérées
```

**Ce qui est géré automatiquement :**
- ✅ Ouverture/fermeture des sessions HTTP
- ✅ Démarrage/arrêt du pool Playwright
- ✅ Connexion/déconnexion à la base de données
- ✅ Nettoyage des tâches en cours

**Alternative manuelle :**

```python
nexus = NexusDL()
await nexus.start()
try:
    # ...
finally:
    await nexus.stop()
```

---

### 🔍 `search()`

Recherche des mangas sur un ou plusieurs sites.

```python
async def search(
    self,
    query: str,
    *,
    sites: list[str] | None = None,
    languages: list[Language] | None = None,
    include_adult: bool = False,
    page: int = 1,
    per_site: int = 20,
    timeout: float = 10.0,
    max_concurrent: int = 8,
) -> list[SearchResult]:
    """Recherche multi-sites en parallèle.

    Args:
        query: Terme de recherche (titre, auteur, etc.).
        sites: Sites à interroger. Si `None`, tous les sites activés
            (sauf adultes si `include_adult=False`).
        languages: Filtrer par langues. Si `None`, toutes.
        include_adult: Inclure le contenu 18+.
        page: Page de résultats.
        per_site: Nombre maximum de résultats par site.
        timeout: Timeout par site (secondes).
        max_concurrent: Nombre de sites interrogés en parallèle.

    Returns:
        Liste de `SearchResult` triés par pertinence.

    Raises:
        SearchError: Si aucun site n'a pu être interrogé.
        NexusDLError: En cas d'erreur générale.

    Example:
        >>> results = await nexus.search(
        ...     "one piece",
        ...     sites=["mangadex", "sushiscan_net"],
        ...     languages=["fr", "en"],
        ... )
        >>> for r in results:
        ...     print(r.title, r.site)
    """
```

**Exemple complet :**

```python
async with NexusDL() as nexus:
    results = await nexus.search(
        "attack on titan",
        sites=["mangadex", "mangafire", "sushiscan_net"],
        languages=["fr", "en"],
        per_site=15,
        timeout=15.0,
    )

    for result in results:
        print(f"[{result.site:15}] {result.title} ({result.year})")
```

**Recherche avec pagination :**

```python
async with NexusDL() as nexus:
    page = 1
    all_results = []

    while True:
        results = await nexus.search("one piece", page=page)
        if not results:
            break
        all_results.extend(results)
        page += 1

    print(f"Total : {len(all_results)} résultats")
```

---

### 📖 `get_manga()`

Récupère les métadonnées complètes d'un manga.

```python
async def get_manga(
    self,
    url_or_id: str,
    *,
    site: str | None = None,
    refresh: bool = False,
) -> Manga:
    """Récupère un manga depuis son URL ou son ID.

    Args:
        url_or_id: URL complète ou ID du manga.
        site: Identifiant du site source. Si `None`, auto-détection
            depuis l'URL.
        refresh: Forcer le rechargement depuis le site (ignore le cache).

    Returns:
        Manga avec toutes les métadonnées (titre, auteur, chapitres, etc.).

    Raises:
        MangaNotFoundError: Si le manga est introuvable.
        ParseError: Si le parsing échoue.
        SiteUnavailableError: Si le site est inaccessible.

    Example:
        >>> manga = await nexus.get_manga(
        ...     "https://mangadex.org/title/a1c2e3f4-...",
        ... )
        >>> print(manga.title, len(manga.chapters))
    """
```

**Exemple avec métadonnées :**

```python
async with NexusDL() as nexus:
    manga = await nexus.get_manga("https://mangadex.org/title/...")

    print(f"📖 {manga.title}")
    print(f"✍️  Auteur : {manga.author}")
    print(f"🎨 Artiste : {manga.artist}")
    print(f"📅 Année : {manga.year}")
    print(f"📊 Statut : {manga.status}")
    print(f"🌍 Langue : {manga.language}")
    print(f"🎭 Genres : {', '.join(manga.genres)}")
    print(f"📚 Chapitres : {len(manga.chapters)}")
    print(f"\n{manga.description}")
```

---

### ⬇️ `download()`

Télécharge un manga ou une sélection de chapitres.

```python
async def download(
    self,
    manga: Manga,
    *,
    dest: Path,
    fmt: PackagingFormat = PackagingFormat.CBZ,
    chapters: list[Chapter] | None = None,
    chapter_range: str | None = None,
    overwrite: bool = False,
    on_progress: ProgressCallback | None = None,
    max_concurrent_pages: int = 8,
) -> AsyncIterator[DownloadResult]:
    """Télécharge un manga ou une sélection de chapitres.

    Args:
        manga: Manga à télécharger (avec ses chapitres).
        dest: Dossier de destination.
        fmt: Format d'empaquetage (ZIP, CBZ, CBR, PDF, FOLDER).
        chapters: Chapitres spécifiques. Si `None`, tous les chapitres.
        chapter_range: Plage au format `"1-10"`, `"5"`, `"all"`.
            Ignoré si `chapters` est fourni.
        overwrite: Écraser les fichiers existants.
        on_progress: Callback appelé à chaque progression.
        max_concurrent_pages: Nombre maximum de pages téléchargées
            en parallèle par chapitre.

    Yields:
        DownloadResult pour chaque chapitre téléchargé (ordre chronologique).

    Raises:
        DownloadError: Si un chapitre échoue définitivement.
        PackagingError: Si l'empaquetage échoue.
        PermissionError: Si le dossier `dest` n'est pas accessible.

    Example:
        >>> async for result in nexus.download(
        ...     manga,
        ...     dest=Path("/manga"),
        ...     fmt=PackagingFormat.CBZ,
        ...     chapter_range="1-10",
        ... ):
        ...     print(result.output_path)
    """
```

**Télécharger tous les chapitres :**

```python
async with NexusDL() as nexus:
    manga = await nexus.get_manga("https://...")

    async for result in nexus.download(
        manga,
        dest=Path("/manga"),
    ):
        print(f"✅ {result.chapter.title} → {result.output_path}")
```

**Télécharger une plage :**

```python
async with NexusDL() as nexus:
    manga = await nexus.get_manga("https://...")

    async for result in nexus.download(
        manga,
        dest=Path("/manga"),
        chapter_range="1-50",
    ):
        print(f"✅ {result.chapter.title}")
```

**Télécharger des chapitres spécifiques :**

```python
async with NexusDL() as nexus:
    manga = await nexus.get_manga("https://...")

    # Sélection fine
    chapters = [
        manga.chapters[0],   # Premier
        manga.chapters[9],   # 10ème
        manga.chapters[-1],  # Dernier
    ]

    async for result in nexus.download(
        manga,
        dest=Path("/manga"),
        chapters=chapters,
    ):
        print(f"✅ {result.chapter.title}")
```

**Format PDF :**

```python
async for result in nexus.download(
    manga,
    dest=Path("/manga"),
    fmt=PackagingFormat.PDF,
):
    print(f"📄 {result.output_path}")
```

---

### 🌐 `get_sites()`

Liste les sites supportés.

```python
async def get_sites(
    self,
    *,
    language: Language | None = None,
    include_adult: bool = False,
    enabled_only: bool = True,
) -> list[SiteConfig]:
    """Liste les sites supportés.

    Args:
        language: Filtrer par langue.
        include_adult: Inclure les sites adultes.
        enabled_only: Ne retourner que les sites activés dans la config.

    Returns:
        Liste de `SiteConfig`.

    Example:
        >>> sites = await nexus.get_sites(language=Language.FR)
        >>> for site in sites:
        ...     print(site.name, site.domains[0])
    """
```

**Exemple :**

```python
async with NexusDL() as nexus:
    sites = await nexus.get_sites(language=Language.FR)

    print(f"Sites FR disponibles ({len(sites)}) :\n")
    for site in sites:
        print(f"  [{site.id:20}] {site.name:25} {site.domains[0]}")
```

---

### 🍪 `refresh_cookies()`

Rafraîchit les cookies d'un site (utile pour Cloudflare).

```python
async def refresh_cookies(self, site_id: str) -> CookieInfo:
    """Rafraîchit les cookies d'un site via Playwright.

    Args:
        site_id: Identifiant du site.

    Returns:
        Informations sur les cookies rafraîchis.

    Raises:
        SiteNotFoundError: Si le site n'existe pas.
        CloudflareError: Si le bypass Cloudflare échoue.

    Example:
        >>> info = await nexus.refresh_cookies("sushiscan_net")
        >>> print(f"Cookies rafraîchis, expirent {info.expires_at}")
    """
```

---

### 🔧 `get_config()`

Accès à la configuration chargée.

```python
def get_config(self) -> NexusConfig:
    """Retourne la configuration actuelle.

    Returns:
        La configuration Pydantic de NexusDL.
    """
```

---

### 🎣 `register_parser()`

Enregistre un parser custom.

```python
def register_parser(
    self,
    parser_class: type[BaseParser],
    *,
    site_config: SiteConfig | None = None,
) -> None:
    """Enregistre un parser custom.

    Args:
        parser_class: Classe héritant de `BaseParser`.
        site_config: Configuration du site. Si `None`, utilise les
            attributs de classe du parser.

    Example:
        >>> nexus.register_parser(MySiteParser)
    """
```

---

### 📊 `get_stats()`

Retourne les statistiques globales.

```python
async def get_stats(self) -> Stats:
    """Retourne les statistiques de NexusDL.

    Returns:
        Statistiques (nombre de mangas, chapitres, taille totale, etc.).
    """
```

---

## 🔧 Modèles

NexusDL expose ses modèles Pydantic pour un typage strict.

### 📦 Import

```python
from nexusdl.core.models import (
    # Manga
    Manga,
    Chapter,
    Page,
    SearchResult,
    MangaStatus,
    ContentRating,

    # Site
    SiteConfig,
    SiteCapabilities,

    # Download
    DownloadTask,
    DownloadResult,
    DownloadProgress,
    DownloadStatus,

    # Enums
    Language,
    PackagingFormat,

    # Library
    LibraryEntry,
    ReadingProgress,

    # Config
    NexusConfig,
)
```

### 📖 `Manga`

Métadonnées complètes d'un manga.

```python
class Manga(BaseModel):
    """Métadonnées d'un manga.

    Attributes:
        id: Identifiant interne NexusDL (format : `{site}:{source_id}`).
        source_id: Identifiant sur le site source.
        site: Identifiant du site (ex: `mangadex`).
        title: Titre principal.
        alternative_titles: Titres alternatifs (langues, abréviations).
        description: Synopsis.
        author: Auteur du scénario.
        artist: Artiste du dessin.
        genres: Liste des genres.
        status: Statut de publication.
        year: Année de première publication.
        cover_url: URL de la couverture.
        language: Langue principale.
        content_rating: Classification de contenu.
        chapters: Liste des chapitres.
        url: URL canonique du manga.
        updated_at: Date de dernière mise à jour.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    site: str
    title: str
    alternative_titles: list[str] = Field(default_factory=list)
    description: str | None = None
    author: str | None = None
    artist: str | None = None
    genres: list[str] = Field(default_factory=list)
    status: MangaStatus = MangaStatus.UNKNOWN
    year: int | None = None
    cover_url: HttpUrl | None = None
    language: Language
    content_rating: ContentRating = ContentRating.SAFE
    chapters: list[Chapter] = Field(default_factory=list)
    url: HttpUrl
    updated_at: datetime | None = None
```

**Exemple :**

```python
manga = await nexus.get_manga("https://...")

# Accès direct
print(manga.title)
print(manga.author)
print(manga.year)

# Méthodes utilitaires
print(manga.slug)  # "one-piece"
print(manga.is_completed)  # bool
print(manga.is_adult)  # bool
```

### 📄 `Chapter`

Un chapitre d'un manga.

```python
class Chapter(BaseModel):
    """Un chapitre.

    Attributes:
        id: Identifiant interne NexusDL.
        source_id: Identifiant sur le site source.
        title: Titre du chapitre.
        number: Numéro (peut être float pour les .5).
        volume: Numéro de tome.
        language: Langue du chapitre.
        pages_count: Nombre de pages (si connu).
        published_at: Date de publication.
        url: URL canonique.
        pages: Liste des pages (rempli à la demande).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    title: str
    number: float | str
    volume: int | None = None
    language: Language
    pages_count: int | None = None
    published_at: datetime | None = None
    url: HttpUrl
    pages: list[Page] = Field(default_factory=list)
```

### 📄 `Page`

Une page d'un chapitre.

```python
class Page(BaseModel):
    """Une page (image).

    Attributes:
        index: Index de la page (0-based).
        url: URL de l'image.
        filename: Nom de fichier suggéré.
        checksum: Hash SHA-256 (déduplication).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    url: HttpUrl
    filename: str
    checksum: str | None = None
```

### 🔍 `SearchResult`

Résultat d'une recherche.

```python
class SearchResult(BaseModel):
    """Résultat d'une recherche.

    Attributes:
        id: Identifiant interne.
        title: Titre du manga.
        author: Auteur.
        cover_url: URL de la couverture.
        year: Année.
        status: Statut.
        genres: Genres.
        language: Langue.
        site: Site source.
        url: URL du manga.
        chapters_count: Nombre de chapitres.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    author: str | None = None
    cover_url: HttpUrl | None = None
    year: int | None = None
    status: MangaStatus = MangaStatus.UNKNOWN
    genres: list[str] = Field(default_factory=list)
    language: Language
    site: str
    url: HttpUrl
    chapters_count: int | None = None
```

### 📥 `DownloadResult`

Résultat d'un téléchargement de chapitre.

```python
class DownloadResult(BaseModel):
    """Résultat d'un téléchargement.

    Attributes:
        task_id: UUID de la tâche parente.
        chapter: Chapitre téléchargé.
        output_path: Chemin du fichier généré.
        bytes_downloaded: Taille totale téléchargée.
        duration_seconds: Durée du téléchargement.
        pages_failed: Pages ayant échoué (si applicable).
        success: Indique si le téléchargement a réussi.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    chapter: Chapter
    output_path: Path
    bytes_downloaded: int
    duration_seconds: float
    pages_failed: list[int] = Field(default_factory=list)
    success: bool = True
```

### 📊 `DownloadProgress`

Progression d'un téléchargement (utilisé dans les callbacks).

```python
class DownloadProgress(BaseModel):
    """Progression d'un téléchargement.

    Attributes:
        task_id: UUID de la tâche.
        manga_title: Titre du manga.
        current_chapter: Chapitre en cours.
        chapters_done: Nombre de chapitres terminés.
        chapters_total: Nombre total de chapitres.
        pages_done: Pages terminées dans le chapitre actuel.
        pages_total: Pages totales du chapitre actuel.
        progress: Progression globale (0.0 à 1.0).
        bytes_downloaded: Octets téléchargés.
        bytes_total: Octets totaux estimés.
        speed_bps: Vitesse en octets/seconde.
        eta_seconds: Temps restant estimé.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    manga_title: str
    current_chapter: Chapter
    chapters_done: int
    chapters_total: int
    pages_done: int
    pages_total: int
    progress: float = Field(ge=0.0, le=1.0)
    bytes_downloaded: int
    bytes_total: int
    speed_bps: float
    eta_seconds: float | None = None
```

### 📦 Enums

```python
from enum import StrEnum


class Language(StrEnum):
    """Langue d'un manga."""
    FR = "fr"
    EN = "en"
    ES = "es"
    DE = "de"
    IT = "it"
    PT = "pt"
    JA = "ja"
    KO = "ko"
    ZH = "zh"
    RU = "ru"


class MangaStatus(StrEnum):
    """Statut de publication."""
    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ContentRating(StrEnum):
    """Classification de contenu."""
    SAFE = "safe"
    SUGGESTIVE = "suggestive"
    EROTICA = "erotica"
    PORNOGRAPHIC = "pornographic"


class PackagingFormat(StrEnum):
    """Format d'empaquetage."""
    ZIP = "zip"
    CBZ = "cbz"
    CBR = "cbr"
    PDF = "pdf"
    FOLDER = "folder"


class DownloadStatus(StrEnum):
    """Statut d'un téléchargement."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

---

## 🎣 Callbacks

NexusDL supporte plusieurs types de callbacks pour suivre l'exécution.

### 📊 `ProgressCallback`

Appelé à chaque mise à jour de progression.

```python
from nexusdl.core.models import DownloadProgress
from typing import Awaitable, Callable


ProgressCallback = Callable[[DownloadProgress], Awaitable[None]]
```

**Exemple :**

```python
from nexusdl.core.models import DownloadProgress


async def on_progress(progress: DownloadProgress) -> None:
    """Affiche la progression."""
    bar_len = 40
    filled = int(bar_len * progress.progress)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(
        f"\r[{bar}] {progress.progress * 100:5.1f}% "
        f"| {progress.speed_bps / 1024 / 1024:.1f} MB/s "
        f"| ETA {progress.eta_seconds:.0f}s",
        end="",
        flush=True,
    )


async for result in nexus.download(
    manga,
    dest=Path("/manga"),
    on_progress=on_progress,
):
    pass
```

### 🎨 Callback riche (Rich)

```python
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from nexusdl.core.models import DownloadProgress


async def main() -> None:
    """Téléchargement avec barre Rich."""
    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress_bar:
        task_id = None

        async def on_progress(p: DownloadProgress) -> None:
            nonlocal task_id
            if task_id is None:
                task_id = progress_bar.add_task(
                    f"📥 {p.manga_title}",
                    total=p.bytes_total,
                )
            progress_bar.update(
                task_id,
                completed=p.bytes_downloaded,
                description=f"📥 {p.current_chapter.title}",
            )

        async with NexusDL() as nexus:
            manga = await nexus.get_manga("https://...")
            async for _ in nexus.download(
                manga,
                dest=Path("/manga"),
                on_progress=on_progress,
            ):
                pass
```

### 🔔 Callback d'événements

Pour recevoir tous les événements (start, progress, complete, error) :

```python
from nexusdl.core.events import Event, EventType


async def on_event(event: Event) -> None:
    """Gère tous les événements de téléchargement."""
    match event.type:
        case EventType.DOWNLOAD_STARTED:
            print(f"🚀 Démarré : {event.data['manga_title']}")
        case EventType.CHAPTER_COMPLETED:
            print(f"✅ {event.data['chapter_title']}")
        case EventType.DOWNLOAD_COMPLETED:
            print(f"🎉 Terminé : {event.data['output_path']}")
        case EventType.DOWNLOAD_FAILED:
            print(f"❌ Échec : {event.data['error']}")


# Utilisation
nexus.on(EventType.DOWNLOAD_STARTED, on_event)
```

---

## 🧩 Plugins

Enregistrez vos propres plugins pour étendre NexusDL.

### 📦 Structure d'un plugin

```python
from nexusdl.plugins.api import BasePlugin, hook


class MyPlugin(BasePlugin):
    """Plugin qui log les téléchargements."""

    name = "my-plugin"
    version = "1.0.0"

    async def on_load(self) -> None:
        self.logger.info("Plugin chargé")

    @hook("on_download_complete")
    async def log_download(self, result: DownloadResult) -> None:
        self.logger.info(f"Téléchargé : {result.output_path}")
```

### 🎣 Hooks disponibles

| Hook | Signature | Description |
|------|-----------|-------------|
| `on_load` | `() -> None` | Chargement du plugin |
| `on_unload` | `() -> None` | Déchargement |
| `on_search` | `(query, results) -> None` | Après recherche |
| `on_manga_fetch` | `(manga) -> None` | Après récupération manga |
| `on_download_start` | `(task) -> None` | Début téléchargement |
| `on_download_progress` | `(progress) -> None` | Progression |
| `on_download_complete` | `(result) -> None` | Fin téléchargement |
| `on_download_failed` | `(error) -> None` | Échec |
| `on_library_scan` | `(manga) -> None` | Scan bibliothèque |
| `on_library_update` | `(entry) -> None` | Mise à jour |

### 🔌 Enregistrement

```python
async with NexusDL() as nexus:
    nexus.register_plugin(MyPlugin())
    # Le plugin est actif pour cette session
```

---

## 🕷️ Parsers

Créez vos propres parsers pour supporter de nouveaux sites.

### 📦 Parser minimal

```python
from typing import ClassVar

from nexusdl.core.models import Chapter, Language, Manga, Page, SearchResult
from nexusdl.parsers.base import BaseParser


class MySiteParser(BaseParser):
    """Parser pour mon-site.fr."""

    site_id: ClassVar[str] = "my_site"
    language: ClassVar[Language] = Language.FR
    adult: ClassVar[bool] = False
    base_url: ClassVar[str] = "https://mon-site.fr"
    rate_limit: ClassVar[float] = 2.0

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> list[SearchResult]:
        """Recherche des mangas."""
        html = await self.session.get_html(
            f"{self.base_url}/search",
            params={"q": query, "page": page},
        )
        soup = self.parse_html(html)

        results: list[SearchResult] = []
        for card in soup.select(".manga-card"):
            link = card.select_one("a.manga-link")
            if not link:
                continue

            results.append(
                SearchResult(
                    id=self._generate_id(link["href"]),
                    title=link.get_text(strip=True),
                    url=self.normalize_url(link["href"]),
                    site=self.site_id,
                    language=self.language,
                )
            )
        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées."""
        url = self.normalize_url(url_or_id)
        html = await self.session.get_html(url)
        soup = self.parse_html(html)

        title = soup.select_one("h1.manga-title")
        if not title:
            raise ParseError(f"Titre introuvable sur {url}")

        return Manga(
            id=self._generate_id(url),
            source_id=url.rsplit("/", 1)[-1],
            site=self.site_id,
            title=title.get_text(strip=True),
            url=url,
            language=self.language,
        )

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère les chapitres."""
        html = await self.session.get_html(manga.url)
        soup = self.parse_html(html)

        chapters: list[Chapter] = []
        for idx, link in enumerate(soup.select(".chapter-list a")):
            chapters.append(
                Chapter(
                    id=f"{manga.id}-ch-{idx + 1}",
                    source_id=link.get("data-id", str(idx)),
                    title=link.get_text(strip=True),
                    number=idx + 1,
                    url=self.normalize_url(link["href"]),
                    language=self.language,
                )
            )
        return chapters

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages."""
        html = await self.session.get_html(chapter.url)
        soup = self.parse_html(html)

        pages: list[Page] = []
        for idx, img in enumerate(soup.select(".chapter-content img")):
            url = img.get("data-src") or img.get("src")
            if not url:
                continue
            pages.append(
                Page(
                    index=idx,
                    url=self.normalize_url(url),
                    filename=f"{idx + 1:03d}.jpg",
                )
            )
        return pages
```

### 🔌 Enregistrement

```python
from nexusdl.core.models import SiteConfig, SiteCapabilities


async with NexusDL() as nexus:
    nexus.register_parser(
        MySiteParser,
        site_config=SiteConfig(
            id="my_site",
            name="Mon Site",
            domains=["https://mon-site.fr"],
            parser_class="mon_module:MySiteParser",
            language=Language.FR,
            capabilities=SiteCapabilities(
                supports_search=True,
                supports_manga_info=True,
                supports_chapters=True,
                supports_pages=True,
                supports_download=True,
            ),
        ),
    )

    results = await nexus.search("one piece", sites=["my_site"])
```

### 🎨 Avec mixins

```python
from nexusdl.parsers.mixins import MadaraMixin


class MySiteParser(MadaraMixin, BaseParser):
    """Parser basé sur le thème WordPress Madara."""

    site_id = "my_site"
    language = Language.FR
    adult = False
    base_url = "https://mon-site.fr"
```

**Mixins disponibles :**

| Mixin | Sites concernés |
|-------|-----------------|
| `MadaraMixin` | Thème WordPress Madara (SushiScan, etc.) |
| `MangaThemesiaMixin` | Asura, Flame, Reaper, etc. |
| `FoolSlideMixin` | Scan-Manga |
| `ApiBasedMixin` | MangaDex, Comick |
| `CloudflareMixin` | Sites protégés Cloudflare |

---

## 🚨 Exceptions

NexusDL expose une hiérarchie d'exceptions claire.

### 📋 Hiérarchie complète

```
NexusDLError                          # Classe de base
├── ConfigurationError                # Config invalide
├── RegistryError                     # Registre sites
│   ├── SiteNotFoundError
│   └── ParserLoadError
├── SessionError                      # Session HTTP
│   ├── HttpError
│   │   ├── HttpTimeoutError
│   │   ├── HttpStatusError
│   │   └── CloudflareError
│   └── CookieError
├── SearchError                       # Recherche
├── ParserError                       # Parsers
│   ├── ParseError
│   ├── MangaNotFoundError
│   └── ChapterNotFoundError
├── DownloadError                     # Téléchargement
│   ├── PageDownloadError
│   ├── RetryExhaustedError
│   └── CancelledError
├── PackagingError                    # Empaquetage
│   ├── ZipSlipError
│   └── ComicInfoError
├── LibraryError                      # Bibliothèque
│   └── DatabaseError
├── AuthError                         # Auth
│   ├── InvalidCredentialsError
│   └── TokenExpiredError
└── PluginError                       # Plugins
    ├── PluginLoadError
    └── PluginPermissionError
```

### 🎯 Gestion d'erreurs robuste

```python
import logging
from nexusdl.core.exceptions import (
    NexusDLError,
    MangaNotFoundError,
    SiteUnavailableError,
    CloudflareError,
    DownloadError,
    PackagingError,
)

logger = logging.getLogger(__name__)


async def safe_download(url: str, dest: Path) -> bool:
    """Télécharge avec gestion complète des erreurs."""
    try:
        async with NexusDL() as nexus:
            manga = await nexus.get_manga(url)
            async for result in nexus.download(manga, dest=dest):
                logger.info(f"✅ {result.chapter.title}")
            return True

    except MangaNotFoundError as e:
        logger.error(f"Manga introuvable : {e}")
    except SiteUnavailableError as e:
        logger.error(f"Site indisponible : {e}")
    except CloudflareError as e:
        logger.warning(f"Cloudflare challenge : {e}")
        # Tentative de refresh cookies
        # ...
    except DownloadError as e:
        logger.error(f"Échec téléchargement : {e}")
    except PackagingError as e:
        logger.error(f"Échec empaquetage : {e}")
    except NexusDLError as e:
        logger.error(f"Erreur NexusDL : {e}")
    except Exception as e:
        logger.exception(f"Erreur inattendue : {e}")

    return False
```

### 📝 Attributs d'exception

Toutes les exceptions NexusDL exposent :

```python
try:
    await nexus.get_manga("https://invalid-url")
except NexusDLError as e:
    print(e.code)          # "MANGA_NOT_FOUND"
    print(e.message)       # Message lisible
    print(e.details)       # Dict de contexte
    print(e.cause)         # Exception originale (si wrappée)
```

---

## 📚 Exemples complets

### 🎯 Exemple 1 : Bot Discord

```python
"""Bot Discord qui télécharge des mangas sur demande."""
from __future__ import annotations

import asyncio
from pathlib import Path

import discord

from nexusdl import NexusDL


class NexusBot(discord.Client):
    """Bot Discord pour NexusDL."""

    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"✅ Bot connecté : {self.user}")


bot = NexusBot()


@bot.tree.command(name="manga", description="Rechercher un manga")
async def manga_cmd(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer()

    async with NexusDL() as nexus:
        results = await nexus.search(query, per_site=5)

    if not results:
        await interaction.followup.send("❌ Aucun résultat")
        return

    embed = discord.Embed(
        title=f"🔍 Résultats pour '{query}'",
        description=f"{len(results)} mangas trouvés",
        color=0x7C3AED,
    )

    for r in results[:10]:
        embed.add_field(
            name=r.title,
            value=f"[{r.site}]({r.url}) — {r.year or 'N/A'}",
            inline=False,
        )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="download", description="Télécharger un manga")
async def download_cmd(
    interaction: discord.Interaction,
    url: str,
    chapters: str = "1-5",
) -> None:
    await interaction.response.defer()

    async with NexusDL() as nexus:
        try:
            manga = await nexus.get_manga(url)

            async for result in nexus.download(
                manga,
                dest=Path("/downloads"),
                chapter_range=chapters,
            ):
                await interaction.followup.send(
                    f"✅ {result.chapter.title} téléchargé"
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur : {e}")


if __name__ == "__main__":
    bot.run("YOUR_DISCORD_TOKEN")
```

### 🎯 Exemple 2 : Script de synchronisation

```python
"""Synchronise une bibliothèque locale avec un site."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from nexusdl import NexusDL


STATE_FILE = Path("~/.nexusdl/sync-state.json").expanduser()
MANGA_DIR = Path("~/Manga").expanduser()


def load_state() -> dict:
    """Charge l'état de synchronisation."""
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    """Sauvegarde l'état."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


async def sync_manga(nexus: NexusDL, url: str, state: dict) -> None:
    """Synchronise un manga (télécharge les nouveaux chapitres)."""
    manga = await nexus.get_manga(url)

    downloaded = set(state.get(manga.id, []))
    to_download = [
        ch for ch in manga.chapters
        if ch.id not in downloaded
    ]

    if not to_download:
        print(f"✓ {manga.title} à jour")
        return

    print(f"📥 {manga.title} : {len(to_download)} nouveaux chapitres")

    async for result in nexus.download(
        manga,
        dest=MANGA_DIR / manga.title,
        chapters=to_download,
    ):
        downloaded.add(result.chapter.id)
        print(f"  ✅ {result.chapter.title}")

    state[manga.id] = list(downloaded)


async def main() -> None:
    """Boucle principale de synchronisation."""
    state = load_state()

    urls = [
        "https://mangadex.org/title/...",
        "https://sushiscan.net/manga/...",
    ]

    async with NexusDL() as nexus:
        for url in urls:
            try:
                await sync_manga(nexus, url, state)
            except Exception as e:
                print(f"❌ Erreur pour {url} : {e}")

    save_state(state)
    print("✨ Synchronisation terminée")


if __name__ == "__main__":
    asyncio.run(main())
```

### 🎯 Exemple 3 : API REST custom

```python
"""Expose NexusDL via une API REST Flask."""
from __future__ import annotations

import asyncio
from pathlib import Path

from flask import Flask, jsonify, request

from nexusdl import NexusDL


app = Flask(__name__)


def run_async(coro):
    """Exécute une coroutine dans un event loop Flask."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@app.route("/search")
def search():
    """Recherche un manga."""
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "Missing query"}), 400

    async def do_search():
        async with NexusDL() as nexus:
            return await nexus.search(query)

    results = run_async(do_search())
    return jsonify([r.model_dump() for r in results])


@app.route("/manga/<path:url>")
def get_manga(url: str):
    """Récupère un manga."""
    async def do_get():
        async with NexusDL() as nexus:
            return await nexus.get_manga(url)

    manga = run_async(do_get())
    return jsonify(manga.model_dump())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
```

### 🎯 Exemple 4 : Script avec retry intelligent

```python
"""Télécharge avec retry intelligent et fallback."""
from __future__ import annotations

import asyncio
from pathlib import Path

from nexusdl import NexusDL
from nexusdl.core.exceptions import (
    CloudflareError,
    SiteUnavailableError,
    DownloadError,
)


async def download_with_fallback(
    query: str,
    dest: Path,
    *,
    max_sites: int = 3,
) -> bool:
    """Essaie plusieurs sites jusqu'à ce que ça marche."""
    async with NexusDL() as nexus:
        results = await nexus.search(query, per_site=5)

        # Grouper par manga (dédupliqué par titre)
        seen_titles: set[str] = set()
        unique_results = []
        for r in results:
            if r.title not in seen_titles:
                seen_titles.add(r.title)
                unique_results.append(r)

        # Essayer chaque résultat
        for result in unique_results[:max_sites]:
            try:
                print(f"🔍 Essai : {result.site} — {result.title}")
                manga = await nexus.get_manga(result.url)

                async for _ in nexus.download(
                    manga,
                    dest=dest / manga.title,
                ):
                    pass

                print(f"✅ Succès via {result.site}")
                return True

            except CloudflareError:
                print(f"⚠️  Cloudflare sur {result.site}, essai suivant")
                continue
            except SiteUnavailableError:
                print(f"⚠️  {result.site} indisponible, essai suivant")
                continue
            except DownloadError as e:
                print(f"⚠️  Erreur : {e}, essai suivant")
                continue

        print("❌ Aucun site n'a fonctionné")
        return False


if __name__ == "__main__":
    asyncio.run(download_with_fallback(
        "one piece",
        Path("~/Manga").expanduser(),
    ))
```

---

## 🎯 Bonnes pratiques

### ✅ À faire

- ✅ **Toujours** utiliser `async with NexusDL()`
- ✅ **Gérer** les exceptions spécifiques
- ✅ **Utiliser** `on_progress` pour les longs téléchargements
- ✅ **Typer** vos fonctions avec les modèles NexusDL
- ✅ **Limiter** la concurrence avec des sémaphores
- ✅ **Logger** avec `loguru` ou `logging`

### ❌ À éviter

- ❌ **Créer** plusieurs instances `NexusDL()` simultanées
- ❌ **Bloquer** l'event loop avec du code sync
- ❌ **Avaler** les exceptions (`except: pass`)
- ❌ **Oublier** `await` sur les coroutines
- ❌ **Utiliser** `asyncio.run()` dans une coroutine

### 🎯 Pattern recommandé

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from nexusdl import NexusDL


@asynccontextmanager
async def get_nexus() -> AsyncIterator[NexusDL]:
    """Fournit une instance NexusDL avec gestion propre."""
    async with NexusDL() as nexus:
        try:
            yield nexus
        except Exception:
            # Log + cleanup
            raise
```

---

## 📖 Références

### 📚 Documentation connexe

- 📖 **[API REST](rest.md)** — Endpoints HTTP
- 📖 **[WebSocket](websocket.md)** — Progression temps réel
- 📖 **[Architecture](../development/architecture.md)** — Vue interne
- 📖 **[Ajouter un parser](../development/adding_parsers.md)** — Guide parser
- 📖 **[Plugins](../development/plugins.md)** — Guide plugins

### 🌐 Ressources externes

- 📚 **[Python asyncio](https://docs.python.org/3/library/asyncio.html)**
- 📚 **[Pydantic v2](https://docs.pydantic.dev/)**
- 📚 **[httpx](https://www.python-httpx.org/)**
- 📚 **[Playwright Python](https://playwright.dev/python/)**

### 💬 Support

- 💬 **[Discord](https://discord.gg/NEXUS-QUANTUM)**
- 🐛 **[GitHub Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues)**
- 💡 **[GitHub Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)**

---

<div align="center">

## 🐍 NexusDL Python API

**Version 1.0.0 — Stable**

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)

**Fait avec ❤️ par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM)**

*Dernière mise à jour : 2026-01-15*

</div>
````
