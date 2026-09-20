
# 📜 Changelog — NexusDL

<div align="center">

**Toutes les modifications notables de NexusDL sont documentées dans ce fichier.**

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

[![Keep a Changelog](https://img.shields.io/badge/Keep%20a%20Changelog-1.1.0-orange?style=for-the-badge)](https://keepachangelog.com/fr/1.1.0/)
[![SemVer](https://img.shields.io/badge/SemVer-2.0.0-blue?style=for-the-badge)](https://semver.org/lang/fr/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=for-the-badge)](https://www.conventionalcommits.org/)

</div>

---

## 📖 Sommaire

- [Conventions](#-conventions)
- [Légende](#-légende)
- [Types de changements](#-types-de-changements)
- [Liens rapides](#-liens-rapides)
- [Versions](#-versions)

---

## 📐 Conventions

### 🔢 Semantic Versioning

NexusDL suit **SemVer 2.0.0** : `MAJOR.MINOR.PATCH`

| Incrément | Quand | Exemple |
|-----------|-------|---------|
| **MAJOR** | Changement **incompatible** (breaking change) | `1.0.0` → `2.0.0` |
| **MINOR** | **Nouvelle fonctionnalité** rétrocompatible | `1.0.0` → `1.1.0` |
| **PATCH** | **Correction de bug** rétrocompatible | `1.0.0` → `1.0.1` |

**Pré-releases :**
- `1.0.0-alpha.1` → Alpha (instable, features manquantes)
- `1.0.0-beta.1` → Beta (features complètes, bugs possibles)
- `1.0.0-rc.1` → Release Candidate (prêt pour la prod)

**Post-releases :**
- `1.0.0.post1` → Correctif sur une release (rare)

### 📅 Date des versions

Format : `AAAA-MM-JJ` (ISO 8601), en **UTC**.

### 🏷️ Format des entrées

Chaque version suit ce format :

```markdown
## [X.Y.Z] - AAAA-MM-JJ

### 🎉 Ajouté (Added)
- Nouvelle fonctionnalité

### 🔄 Modifié (Changed)
- Changement dans une fonctionnalité existante

### ⚠️ Déprécié (Deprecated)
- Fonctionnalité bientôt supprimée

### 🗑️ Supprimé (Removed)
- Fonctionnalité supprimée

### 🐛 Corrigé (Fixed)
- Correction de bug

### 🔒 Sécurité (Security)
- Correctif de vulnérabilité
```

### 🔗 Convention de commits

Les entrées sont générées depuis les **Conventional Commits** :

| Type | Section Changelog |
|------|:-----------------:|
| `feat` | 🎉 Ajouté |
| `fix` | 🐛 Corrigé |
| `perf` | ⚡ Performance |
| `refactor` | 🔄 Modifié |
| `docs` | 📝 Documentation |
| `deprecate` | ⚠️ Déprécié |
| `remove` | 🗑️ Supprimé |
| `security` | 🔒 Sécurité |

---

## 🎨 Légende

| Emoji | Signification |
|:-----:|---------------|
| 🎉 | Nouvelle fonctionnalité |
| 🔄 | Modification |
| ⚠️ | Dépréciation |
| 🗑️ | Suppression |
| 🐛 | Correction de bug |
| 🔒 | Sécurité |
| ⚡ | Performance |
| 📝 | Documentation |
| 🌐 | Nouveau site/parser |
| 🔞 | Contenu adulte |
| 💥 | Breaking change |
| 🔧 | Configuration |
| 🐳 | Docker |
| 🎨 | UI/UX |
| 🌍 | i18n / Traductions |
| ♻️ | Refactoring |
| ⬆️ | Mise à jour de dépendance |

---

## 🔗 Liens rapides

- 📦 [Releases GitHub](https://github.com/NEXUS-QUANTUM/nexusdl/releases)
- 📥 [Dernière version](https://github.com/NEXUS-QUANTUM/nexusdl/releases/latest)
- 🐛 [Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues)
- 💬 [Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)
- 📚 [Documentation](https://docs.nexus-quantum.dev/nexusdl)

---

## 📋 Versions

<!--
  ══════════════════════════════════════════════════════════════════════════════
  NE PAS MODIFIER MANUELLEMENT — Généré automatiquement par bump-my-version
  ══════════════════════════════════════════════════════════════════════════════
-->

## [Unreleased]

### 🎉 Ajouté

- **Core** : Structure initiale du projet avec architecture hexagonale
- **Core** : Modèles Pydantic v2 (`Manga`, `Chapter`, `Page`, `SiteConfig`, `DownloadTask`)
- **Core** : Système de configuration avec `pydantic-settings`
- **Core** : Gestion des exceptions avec hiérarchie custom
- **Core** : Logging structuré via `loguru`
- **Core** : Event bus asyncio pour découplage inter-modules
- **Session** : `HttpSession` async basé sur `httpx`
- **Session** : `CookieManager` chiffré avec `cryptography`
- **Session** : `PlaywrightPool` pour bypass Cloudflare
- **Session** : `RateLimiter` token-bucket par site
- **Registry** : `SiteRegistry` avec chargement dynamique de parseurs
- **Registry** : Configuration centralisée dans `sites.yaml`
- **Downloader** : `DownloadManager` async avec file prioritaire
- **Downloader** : Support `asyncio.TaskGroup` pour téléchargements parallèles
- **Downloader** : Retry automatique avec backoff exponentiel (`tenacity`)
- **Downloader** : Déduplication par hash SHA-256
- **Packaging** : Support des formats ZIP, CBZ, CBR, PDF, dossier brut
- **Packaging** : Génération de `ComicInfo.xml` (standard ComicRack)
- **Image** : Conversion automatique webp → jpg, avif → png
- **Library** : Base SQLite avec recherche FTS5
- **Library** : Suivi de progression de lecture
- **Utils** : Helpers async, filesystem, texte, URL, hash
- **CLI** : Interface Textual avec écrans de recherche, download, settings
- **CLI** : Commandes Typer (`search`, `download`, `sites`, `config`, `cookies`)
- **Web** : Backend FastAPI avec endpoints REST
- **Web** : WebSocket pour progression temps réel
- **Web** : Authentification JWT
- **Web** : Frontend Next.js 14 (App Router) avec TailwindCSS
- **GUI** : Interface CustomTkinter pour desktop
- **Plugins** : Système de plugins avec chargement dynamique
- **Scripts** : Scripts utilitaires (build, dev, validate, generate)
- **Docker** : Images Docker multi-stage (CLI, Web, GUI)
- **CI/CD** : Workflows GitHub Actions (lint, test, build, release)

### 🌐 Sites supportés (60+)

#### 🇫🇷 Sites Français (21)

- SushiScan FR (`sushiscan.fr`)
- SushiScan NET (`sushiscan.net`)
- Mangas Origines (`mangas-origines.com`, `mangas-origines.fr`)
- Hentai Origines 🔞 (`hentai-origines.com`)
- ToonFR (`toonfr.com`)
- OrtegaScans (`ortegascans.fr`, `ortegascans.com`)
- Anime-Scans (`anime-scans.com`)
- Phenix Scans (`phenix-scans.co`)
- Poseidon Scans (`poseidon-scans.net`)
- Raijin Scans (`raijin-scans.fr`)
- RimuScan (`rimuscan.fr`)
- Blossom Scans (`blossom-scans.com`)
- EpsilonScan (`epsilonscan.to`)
- Genkan Scans (`genkan-scans.com`)
- NekoHouse (`nekohouse.fr`)
- Shinra Scans (`shinra-scans.fr`)
- Taisei Scans (`taisei-scans.com`)
- Fandub Scans (`fandub-scans.com`)
- Karma Scans (`karma-scans.com`)
- Urano Scans (`urano-scans.fr`)
- Xanadu Scans (`xanadu-scans.fr`)
- Scan-Manga (`scan-manga.com`)
- CrunchyScan FR (`crunchyscan.fr`)

#### 🇬🇧 Sites Anglais (22)

- MangaDex ⭐ (`api.mangadex.org`) — API officielle
- MangaKakalot (`mangakakalot.com`)
- Bato.to (`bato.to`)
- Comick (`comick.io`) — API
- Asura Scans (`asurascans.com`)
- Flame Scans (`flamescans.org`)
- Reaper Scans (`reaperscans.com`)
- Luminous Scans (`luminousscans.com`)
- Void Scans (`void-scans.com`)
- TCB Scans (`tcbscans.com`)
- MangaSee123 (`mangasee123.com`)
- Galaxy Scans (`galaxy-scans.com`)
- Zenith Scans (`zenith-scans.com`)
- Leviathan Scans (`leviathanscans.com`)
- Disaster Scans (`disasterscans.com`)
- KireiCake (`kireicake.com`)
- Scylla Scans (`scyllascans.com`)
- CrunchyScan ORG (`crunchyscan.org`)
- ToonGod (`toongod.org`)
- MangaFire ⭐ (`mangafire.to`)
- MangaBuddy ⭐ (`mangabuddy.com`)
- Toonily ⭐ (`toonily.com`)

#### 🇰🇷 Manhwa (2)

- ManhwaClub (`manhwaclub.net`)
- Manhwa-Raw (`manhwa-raw.com`)

#### 🔞 Sites Adultes (5)

- nHentai 🔞 (`nhentai.net`) — API non officielle
- HentaiZone 🔞 (`hentaizone.xyz`) — Playwright
- Pururin 🔞 (`pururin.com`) — Playwright
- Scan-Hentai 🔞 (`scan-hentai.net`) — Cloudflare
- X-Manga 🔞 (`x-manga.net`, `x-manga.org`)

### 🔧 Configuration

- Fichier `config.yaml` avec support complet
- Support des variables d'environnement via `.env`
- Proxy support (HTTP, SOCKS5)
- Rotation automatique des User-Agents
- Cookies chiffrés au repos

### 📝 Documentation

- `README.md` complet avec badges, screenshots, exemples
- `CONTRIBUTING.md` avec guide détaillé (workflow Git, conventions, tests)
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1)
- `SECURITY.md` avec politique de divulgation
- `LICENSE` (GPL-3.0-or-later)
- `MANIFEST.in` pour la distribution
- `Makefile` et `justfile` pour les raccourcis
- Système `nexus.dl` (manifestes de dossier)
- Documentation Sphinx/MkDocs dans `docs/`

### 🐳 Infrastructure

- Dockerfiles multi-stage (CLI, Web, GUI)
- `docker-compose.yml` (production)
- `docker-compose.dev.yml` (développement)
- Support FlareSolverr pour Cloudflare
- Volumes : `/downloads`, `/config`

### 🛡️ Sécurité

- Chiffrement des cookies avec `cryptography`
- Validation stricte des entrées via Pydantic
- Aucun secret en dur (variables d'environnement)
- Audit de sécurité automatisé (Bandit, pip-audit)
- Scan de secrets (gitleaks, detect-secrets)

### ⚡ Performance

- Téléchargement parallèle des pages (configurable)
- Pool de navigateurs Playwright réutilisables
- Cache des sessions HTTP par site
- Rate limiting intelligent (token-bucket)

### ♻️ Refactoring

- Migration complète depuis SushiDL vers architecture hexagonale
- Séparation stricte `core` / `parsers` / `interfaces`
- Utilisation de `asyncio.TaskGroup` (Python 3.12+)
- Typage strict avec Mypy

### ⬆️ Dépendances

- Python 3.12+ requis
- Pydantic v2 (`>=2.9`)
- httpx (`>=0.27`)
- Playwright (`>=1.48`)
- FastAPI (`>=0.115`)
- Next.js 14
- Textual (`>=0.85`)

---

## Types de changements

### 🎉 `Added` — Ajouté
Nouvelles fonctionnalités.

### 🔄 `Changed` — Modifié
Changements dans des fonctionnalités existantes.

### ⚠️ `Deprecated` — Déprécié
Fonctionnalités bientôt supprimées.

### 🗑️ `Removed` — Supprimé
Fonctionnalités supprimées.

### 🐛 `Fixed` — Corrigé
Corrections de bugs.

### 🔒 `Security` — Sécurité
Corrections de vulnérabilités (avec CVE si applicable).

### ⚡ `Performance` — Performance
Améliorations de performance.

### 📝 `Documentation` — Documentation
Changements dans la documentation uniquement.

### 🎨 `Style` — Style
Changements cosmétiques (formatage, espaces, etc.) — n'affecte pas le comportement.

### ♻️ `Refactoring` — Refactoring
Changements de code qui ne corrigent pas de bug et n'ajoutent pas de fonctionnalité.

---

## 🚀 Roadmap (versions futures)

### 🔜 Version 1.1.0 (Q2 2026)

- Synchronisation cloud (WebDAV, S3, Nextcloud)
- Lecteur intégré (web + GUI)
- Notifications (Discord, Telegram, ntfy)
- Support Torrent (optionnel)
- Améliorations de l'interface web

### 🔮 Version 1.2.0 (Q3 2026)

- Application mobile (React Native)
- Recommandations IA
- Traduction automatique (OCR + DeepL)
- Multi-utilisateurs avancé (web)
- Marketplace de plugins

### 🌌 Version 2.0.0 (Q4 2026)

- Backend distribué (multi-nœuds)
- Support des anime (streaming + download)
- Refonte de l'architecture des plugins
- API GraphQL

---

## 📊 Statistiques

<!--
  Ces statistiques sont mises à jour automatiquement à chaque release.
-->

| Métrique | Valeur |
|----------|:------:|
| **Version actuelle** | `1.0.0-alpha` |
| **Sites supportés** | 60+ |
| **Parseurs** | 50+ |
| **Formats de sortie** | 5 (ZIP, CBZ, CBR, PDF, Folder) |
| **Interfaces** | 3 (CLI, Web, GUI) |
| **Langues** | 4 (FR, EN, ES, DE) |
| **Couverture de tests** | 85%+ |
| **Lignes de code** | ~50 000 |

---

## 🎯 Politique de support

| Version | Statut | Support | Fin de vie |
|:-------:|:------:|:-------:|:----------:|
| **1.x** | 🟢 Stable | ✅ Actif | TBD |
| **0.x** | 🔴 Obsolète | ❌ Non | Terminé |
| **SushiDL** | ⚫ Déprécié | ❌ Non | Migrer vers NexusDL |

### 📅 Calendrier de support

- **Correctifs de sécurité** : sur les 2 dernières versions majeures
- **Correctifs de bugs** : sur la dernière version mineure
- **Nouvelles fonctionnalités** : uniquement sur `main`

### 🔄 Politique de dépréciation

Toute dépréciation suit ce cycle :

1. **Annonce** dans le CHANGELOG et Discord
2. **Marquage** dans le code (`@deprecated` + warnings)
3. **Documentation** des alternatives
4. **Suppression** après minimum 2 versions mineures (6 mois)

---

## 🤝 Comment contribuer au CHANGELOG

### 👤 Pour les contributeurs

Votre PR doit inclure une mise à jour du CHANGELOG dans la section `[Unreleased]`.

**Format :**
```markdown
## [Unreleased]

### 🎉 Ajouté
- **Parser** : Support de MangaFire (`mangafire.to`)
```

**Règles :**
- Une ligne par changement notable
- Format : `- **<scope>** : <description>`
- Commence par un verbe à l'infinitif (`Ajouter`, `Corriger`, `Supprimer`)
- Pas de point final
- Pas de mention de numéro d'issue (sauf si breaking)

### 🤖 Pour les mainteneurs

Le CHANGELOG est mis à jour automatiquement via `bump-my-version` :

```bash
# Bump de version (met à jour CHANGELOG.md + version.py)
make bump PART=minor

# Ou manuellement
uv run bump-my-version bump minor
```

**Le script `bump-my-version` :**
1. Incrémente la version dans `pyproject.toml`
2. Met à jour `src/nexusdl/version.py`
3. Crée une nouvelle section dans `CHANGELOG.md`
4. Crée un commit `chore(release): vX.Y.Z`
5. Crée un tag Git `vX.Y.Z`

---

## 🔗 Liens

- [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
- [Semantic Versioning](https://semver.org/lang/fr/)
- [Conventional Commits](https://www.conventionalcommits.org/fr/)
- [bump-my-version](https://github.com/callowayproject/bump-my-version)
- [Releases GitHub](https://github.com/NEXUS-QUANTUM/nexusdl/releases)

---

<div align="center">

## 🌌 NexusDL

**Merci d'utiliser NexusDL !**

Chaque version est le fruit du travail de la communauté.

---

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Twitter](https://img.shields.io/badge/Twitter-@NEXUS--QUANTUM-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)
[![Email](https://img.shields.io/badge/Email-nexus.quantum@protonmail.com-8B89CC?style=for-the-badge&logo=protonmail&logoColor=white)](mailto:nexus.quantum@protonmail.com)

**Fait avec ❤️ par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM)**

</div>

<!--
  ══════════════════════════════════════════════════════════════════════════════
  FORMAT DE RÉFÉRENCE (à copier pour chaque nouvelle version)
  ══════════════════════════════════════════════════════════════════════════════

## [X.Y.Z] - AAAA-MM-JJ

### 🎉 Ajouté
- **Scope** : Description

### 🔄 Modifié
- **Scope** : Description

### ⚠️ Déprécié
- **Scope** : Description

### 🗑️ Supprimé
- **Scope** : Description

### 🐛 Corrigé
- **Scope** : Description

### 🔒 Sécurité
- **CVE-XXXX-XXXXX** : Description

### ⚡ Performance
- **Scope** : Description

### 📝 Documentation
- **Scope** : Description

### 🌐 Nouveaux sites
- **FR** : site1, site2
- **EN** : site3

[Unreleased]: https://github.com/NEXUS-QUANTUM/nexusdl/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/NEXUS-QUANTUM/nexusdl/compare/vX.Y.Z-1...vX.Y.Z
  ══════════════════════════════════════════════════════════════════════════════
-->
```

