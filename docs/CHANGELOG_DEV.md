
# 🛠️ Changelog Développeur — NexusDL

<div align="center">

**Journal de développement interne de NexusDL.**

*Destiné aux contributeurs, mainteneurs et développeurs de plugins/parsers.*

[![Audience](https://img.shields.io/badge/Audience-Developers-purple?style=for-the-badge)](docs/CHANGELOG_DEV.md)
[![Format](https://img.shields.io/badge/Format-Keep%20a%20Changelog-orange?style=for-the-badge)](https://keepachangelog.com/)
[![Internal](https://img.shields.io/badge/Scope-Internal-red?style=for-the-badge)](docs/CHANGELOG_DEV.md)
[![SemVer](https://img.shields.io/badge/SemVer-2.0.0-blue?style=for-the-badge)](https://semver.org/)

[📖 Changelog public](../CHANGELOG.md) •
[📚 Architecture](source/development/architecture.md) •
[🤝 Contributing](../CONTRIBUTING.md)

</div>

---

## 📖 À propos de ce fichier

### 🎯 Objectif

Ce fichier est **distinct** du [`CHANGELOG.md`](../CHANGELOG.md) public :

| Aspect | `CHANGELOG.md` | `CHANGELOG_DEV.md` (ce fichier) |
|--------|:--------------:|:-------------------------------:|
| **Audience** | Utilisateurs finaux | Développeurs, contributeurs |
| **Niveau** | Fonctionnalités visibles | Détails d'implémentation |
| **Fréquence** | À chaque release | En continu |
| **Contenu** | Features, bugs utilisateurs | Refactorings, migrations, archi |
| **Breaking changes** | ⚠️ Mentionnés brièvement | 🔴 Détaillés exhaustivement |
| **Guidage** | Comment utiliser | Comment migrer le code |
| **Format** | Keep a Changelog | Keep a Changelog + détails tech |

### 🎓 Qui doit le lire ?

- 👨‍💻 **Contributeurs** : avant de soumettre une PR
- 🔧 **Développeurs de plugins** : pour suivre les changements d'API
- 🕷️ **Développeurs de parsers** : pour suivre les changements de `BaseParser`
- 🏗️ **Mainteneurs** : pour comprendre l'historique des décisions
- 🔍 **Auditeurs** : pour tracer les changements de sécurité

### ✍️ Quand le mettre à jour ?

À chaque PR qui touche à :

- ✅ **API publique** (`nexusdl.*` exposé aux plugins)
- ✅ **`BaseParser`** (interface des parsers)
- ✅ **`BasePackager`** (interface des formats)
- ✅ **Schémas Pydantic** (`Manga`, `Chapter`, `SiteConfig`, etc.)
- ✅ **Configuration** (`config.yaml`, `.env`, `sites.yaml`)
- ✅ **Structure de la BDD** (migrations SQL)
- ✅ **Architecture** (nouvelles couches, dépendances)
- ✅ **Dépendances critiques** (Pydantic, httpx, Playwright)
- ✅ **Système de plugins** (hooks, API)
- ✅ **Décisions techniques** majeures (ADR)

### ❌ Quand NE PAS le mettre à jour

- ❌ Correction de typo dans un commentaire
- ❌ Reformattage (ruff)
- ❌ Tests ajoutés sans changement d'API
- ❌ Documentation utilisateur pure

---

## 📐 Conventions

### 🔢 Versioning du projet

NexusDL suit **SemVer 2.0.0** :

```
MAJOR.MINOR.PATCH[-prerelease][+build]
```

| Incrément | Déclencheur | Exemple |
|-----------|-------------|---------|
| **MAJOR** | Breaking change de l'API | `1.0.0` → `2.0.0` |
| **MINOR** | Ajout rétrocompatible | `1.0.0` → `1.1.0` |
| **PATCH** | Correctif rétrocompatible | `1.0.0` → `1.0.1` |

### 🏷️ Format des entrées

Chaque version suit ce format :

```markdown
## [X.Y.Z] - AAAA-MM-JJ

### 💥 BREAKING CHANGES
- Description + **Migration** : comment migrer

### 🏗️ Architecture
- Changements structurels

### 🔧 API publique
- Signatures modifiées/ajoutées

### 📦 Schémas de données
- Modèles Pydantic, migrations BDD

### 🌐 Parsers
- Changements dans l'interface BaseParser

### 📦 Packaging
- Changements dans BasePackager

### 🔌 Plugins
- Changements dans l'API des plugins

### 🔒 Sécurité
- Correctifs internes

### ⚡ Performance
- Optimisations

### ♻️ Refactoring
- Réorganisations internes

### 🐛 Corrections
- Bugs internes

### 📝 Documentation
- Doc dev, docstrings, ADR

### 🧪 Tests
- Couverture, nouveaux tests

### ⬆️ Dépendances
- Mises à jour importantes

### 🛠️ Outillage
- CI, pre-commit, build
```

### 🎯 Marqueurs de criticité

| Emoji | Signification | Action requise |
|:-----:|--------------|----------------|
| 💥 | **Breaking change** | Migration obligatoire |
| 🔴 | **Critique** | Action immédiate |
| 🟠 | **Important** | Action sous 1 semaine |
| 🟡 | **Modéré** | Noter pour plus tard |
| 🟢 | **Mineur** | Informatif |
| ℹ️ | **Info** | Aucune action |

---

## 🔗 Table des versions

- [Unreleased](#unreleased)
- [1.0.0-alpha.1](#100-alpha1---2026-01-15)
- [Pré-1.0 (historique)](#pré-10)

---

## [Unreleased]

### 🏗️ Architecture

#### ADR-001 : Choix de l'architecture hexagonale
- **Date** : 2026-01-10
- **Statut** : ✅ Accepté
- **Contexte** : Refonte complète de SushiDL
- **Décision** : Adopter une architecture hexagonale stricte
  - `core/` ne dépend de rien
  - `parsers/` dépend uniquement de `core/`
  - `interfaces/` dépend de tout
- **Conséquences** :
  - ✅ Testabilité accrue
  - ✅ Découplage fort
  - ⚠️ Plus de code boilerplate

#### ADR-002 : Choix de Pydantic v2
- **Date** : 2026-01-11
- **Statut** : ✅ Accepté
- **Contexte** : Besoin de validation stricte des données
- **Décision** : Utiliser Pydantic v2 partout
  - `BaseModel` pour tous les DTOs
  - `BaseSettings` pour la config
  - `model_config = ConfigDict(frozen=True)` pour les modèles immuables
- **Conséquences** :
  - ✅ Performance (v2 = 5-20x plus rapide)
  - ✅ Typage strict
  - ⚠️ Rupture avec v1

#### ADR-003 : Choix de `uv` sur pip/poetry/pdm
- **Date** : 2026-01-12
- **Statut** : ✅ Accepté
- **Décision** : Utiliser `uv` comme gestionnaire principal
- **Raisons** :
  - Vitesse (10-100x plus rapide que pip)
  - Lockfile reproductible (`uv.lock`)
  - PEP 621 natif
  - Support des groupes de dépendances
- **Fallback** : `requirements*.txt` maintenus pour compatibilité

#### ADR-004 : Choix de Textual pour la TUI
- **Date** : 2026-01-13
- **Statut** : ✅ Accepté
- **Décision** : Utiliser Textual au lieu de `rich` seul
- **Raisons** :
  - Composants interactifs (boutons, listes, inputs)
  - CSS-like styling (`.tcss`)
  - Layouts responsifs
  - Support des événements
- **Fallback** : Commandes Typer pour scripting

#### ADR-005 : Choix de FastAPI + Next.js pour le web
- **Date** : 2026-01-14
- **Statut** : ✅ Accepté
- **Décision** : Séparer backend (FastAPI) et frontend (Next.js)
- **Raisons** :
  - Découplage total
  - Support WebSocket natif
  - OpenAPI auto-généré
  - Frontend moderne (React 18)
- **Alternative rejetée** : Flask + Jinja2 (moins scalable)

#### ADR-006 : Système de mixins pour les parsers
- **Date** : 2026-01-15
- **Statut** : ✅ Accepté
- **Décision** : Utiliser des mixins pour les templates de sites courants
  - `MadaraMixin` (WordPress)
  - `MangaThemesiaMixin`
  - `FoolSlideMixin`
  - `ApiBasedMixin`
  - `CloudflareMixin`
- **Raisons** :
  - Éviter la duplication de code
  - Ajouter un site en 5 minutes si template connu
  - Maintenir la cohérence entre sites similaires

---

### 🔧 API publique

#### Ajout de la classe `NexusDL`
- Fichier : `src/nexusdl/__init__.py`
- API haut niveau exposée aux utilisateurs
- Méthodes : `search()`, `get_manga()`, `download()`, `get_sites()`
- Pattern : contexte asynchrone (`async with`)

```python
from nexusdl import NexusDL

async with NexusDL() as nexus:
    results = await nexus.search("one piece")
```

#### Exposition des modèles Pydantic
- Fichier : `src/nexusdl/core/models/__init__.py`
- Réexport de tous les modèles publics :

```python
from nexusdl.core.models import (
    Manga,
    Chapter,
    Page,
    SiteConfig,
    DownloadTask,
    DownloadResult,
    SearchResult,
    Language,
    ContentRating,
    PackagingFormat,
)
```

#### Ajout de `BaseParser` (interface stable)
- Fichier : `src/nexusdl/parsers/base.py`
- **API stable** : toute modification = breaking change
- Méthodes abstraites :

```python
class BaseParser(ABC):
    site_id: ClassVar[str]
    language: ClassVar[Language]
    adult: ClassVar[bool] = False

    @abstractmethod
    async def search(self, query: str, *, page: int = 1) -> list[SearchResult]: ...

    @abstractmethod
    async def get_manga(self, url_or_id: str) -> Manga: ...

    @abstractmethod
    async def get_chapters(self, manga: Manga) -> list[Chapter]: ...

    @abstractmethod
    async def get_pages(self, chapter: Chapter) -> list[Page]: ...
```

#### Ajout de `BasePackager` (interface stable)
- Fichier : `src/nexusdl/core/packaging/base.py`
- **API stable** : toute modification = breaking change
- Méthodes abstraites :

```python
class BasePackager(ABC):
    format: ClassVar[PackagingFormat]

    @abstractmethod
    async def package(
        self,
        pages: list[Path],
        output: Path,
        *,
        metadata: ComicInfo | None = None,
    ) -> Path: ...
```

---

### 📦 Schémas de données

#### Modèle `Manga`
```python
class Manga(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    site: str
    title: str
    alternative_titles: list[str] = []
    description: str | None = None
    author: str | None = None
    artist: str | None = None
    genres: list[str] = []
    status: MangaStatus = MangaStatus.UNKNOWN
    year: int | None = None
    cover_url: HttpUrl | None = None
    language: Language
    content_rating: ContentRating = ContentRating.SAFE
    chapters: list[Chapter] = []
    url: HttpUrl
    updated_at: datetime | None = None
```

#### Modèle `Chapter`
```python
class Chapter(BaseModel):
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
    pages: list[Page] = []
```

#### Modèle `SiteConfig`
```python
class SiteConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    domains: list[HttpUrl]
    parser_class: str
    language: Language
    adult: bool = False
    capabilities: SiteCapabilities
    default_headers: dict[str, str] = {}
    cookies_required: list[str] = []
```

#### Migrations BDD (SQLite)

| Version | Fichier | Changements |
|---------|---------|-------------|
| `001` | `001_initial.sql` | Tables `manga`, `chapter`, `download`, `user` |
| `002` | `002_add_reading_progress.sql` | Table `reading_progress` + index |
| `003` | `003_add_fts5.sql` | Index FTS5 sur `manga.title`, `manga.description` |

---

### 🌐 Parsers

#### Ajout des mixins
- `MadaraMixin` : pour sites WordPress Madara (30% des sites FR)
- `MangaThemesiaMixin` : pour sites MangaThemesia (Asura, Flame, etc.)
- `FoolSlideMixin` : pour sites FoolSlide (Scan-Manga)
- `ApiBasedMixin` : pour sites API REST (MangaDex, Comick)
- `CloudflareMixin` : ajoute le bypass Cloudflare via Playwright

#### Convention de nommage des parsers
- Fichier : `src/nexusdl/parsers/<lang>/<site_id>.py`
- Classe : `<SiteId>Parser` (PascalCase)
- `site_id` : snake_case (ex: `sushiscan_net`)

#### Exemple de parser minimal
```python
class SushiScanNetParser(MadaraMixin, BaseParser):
    site_id = "sushiscan_net"
    language = Language.FR
    adult = False
    base_url = "https://sushiscan.net"
    rate_limit = 2.0
```

---

### 📦 Packaging

#### Formats supportés
- `ZIP` : archive standard
- `CBZ` : ZIP renommé, standard comics
- `CBR` : RAR renommé (nécessite `rarfile`)
- `PDF` : via `img2pdf` + `pypdf`
- `FOLDER` : images brutes

#### Génération de `ComicInfo.xml`
- Standard : ComicRack
- Champs supportés : `Title`, `Series`, `Number`, `Volume`, `Writer`, `Penciller`, `Summary`, `Year`, `Genre`, `LanguageISO`, `PageCount`
- Emplacement : racine du CBZ

---

### 🔌 Plugins

#### API des plugins
- Fichier : `src/nexusdl/plugins/api.py`
- Classe de base : `BasePlugin`
- Décorateur : `@hook("event_name")`
- Hooks disponibles :
  - `on_load`
  - `on_unload`
  - `on_download_start`
  - `on_download_complete`
  - `on_download_failed`
  - `on_library_scan`
  - `on_library_update`
  - `on_search`
  - `on_manga_fetch`

#### Permissions déclaratives
Chaque plugin doit déclarer ses permissions :

```yaml
permissions:
  - network
  - filesystem_read
  - filesystem_write
```

- `network` : accès HTTP
- `filesystem_read` : lecture de fichiers
- `filesystem_write` : écriture de fichiers
- `execute` : exécution de commandes (rare, déconseillé)
- `admin` : accès total (réservé aux plugins officiels)

---

### 🔒 Sécurité

#### Chiffrement des cookies
- Algorithme : **Fernet** (AES-128-CBC + HMAC-SHA256)
- Clé : dérivée de `NEXUSDL_COOKIE_ENCRYPTION_KEY`
- Stockage : `.nexusdl/cookies.enc`
- Rotation : à chaque refresh Cloudflare

#### Validation stricte des entrées
- Tous les DTOs : Pydantic v2 avec `extra="forbid"`
- URLs : `HttpUrl` de Pydantic
- Chemins : validation `Path.resolve()` + `is_relative_to()`
- Noms de fichiers : `clean_filename()` + `slugify()`

#### Protection Zip Slip
```python
def _safe_extract(self, archive: ZipFile, dest: Path) -> None:
    """Empêche les attaques Zip Slip."""
    for member in archive.namelist():
        member_path = (dest / member).resolve()
        if not member_path.is_relative_to(dest.resolve()):
            raise PackagingError(f"Zip Slip détecté : {member}")
```

---

### ⚡ Performance

#### Async-first partout
- Toutes les I/O sont async (`asyncio`)
- Utilisation de `asyncio.TaskGroup` (Python 3.11+)
- Sémaphores pour limiter la concurrence
- Timeouts systématiques

#### Pool Playwright partagé
- Un seul navigateur Chromium réutilisé
- Contexts isolés par site
- Réduction de 80% de la mémoire vs. un navigateur par tâche

#### Cache HTTP
- Cache LRU en mémoire
- TTL configurable (défaut : 1h)
- Persistant entre sessions via SQLite (optionnel)

---

### ♻️ Refactoring

#### Migration depuis SushiDL
| Ancien (SushiDL) | Nouveau (NexusDL) | Raison |
|------------------|-------------------|--------|
| `sushidl.py` monolithique | `core/` + `parsers/` + `interfaces/` | Séparation des responsabilités |
| `requests` sync | `httpx` async | Performance |
| `BeautifulSoup` seul | `selectolax` + `bs4` + `lxml` | Performance (selectolax = 10x plus rapide) |
| Config JSON | Config YAML + Pydantic | Validation stricte |
| Pas de tests | Tests complets | Robustesse |
| Pas de typage | Mypy strict | Fiabilité |
| GUI Tkinter | CLI Textual + Web + GUI CTk | Multi-plateforme |

#### Réorganisation des dossiers
- `src/nexusdl/` : package principal (PEP 621)
- `src/nexusdl/core/` : domaine
- `src/nexusdl/parsers/` : adaptateurs secondaires
- `src/nexusdl/interfaces/` : adaptateurs primaires

---

### 🐛 Corrections internes

- Fix : `CookieManager.load()` retournait 5 valeurs au lieu de 6
- Fix : race condition dans `PlaywrightPool.acquire()`
- Fix : fuite mémoire dans `DownloadManager` (tasks non nettoyées)
- Fix : `SiteRegistry.load()` ne gérait pas les parseurs manquants
- Fix : `RateLimiter` ne respectait pas le `max_delay`

---

### 📝 Documentation dev

#### ADR (Architecture Decision Records)
- Format : Markdown dans `docs/adr/`
- Numérotation : `ADR-XXX-titre.md`
- Contenu : Contexte, Décision, Conséquences, Alternatives

#### Docstrings Google-style
- Toutes les fonctions publiques
- Args, Returns, Raises, Example

#### Types stricts
- Mypy strict sur tout `src/nexusdl/`
- `from __future__ import annotations` en tête
- Pas de `Any` sauf justification

---

### 🧪 Tests

#### Structure
```
tests/
├── unit/              # Tests unitaires (rapides, isolés)
├── integration/       # Tests avec I/O mockée (respx)
├── e2e/               # Tests avec réseau réel
└── fixtures/          # HTML/JSON de test
```

#### Couverture cible
| Composant | Cible |
|-----------|:-----:|
| `core/` | 90% |
| `parsers/` | 80% |
| `interfaces/` | 70% |
| **Global** | **85%** |

#### Marqueurs pytest
- `@pytest.mark.unit`
- `@pytest.mark.integration`
- `@pytest.mark.e2e`
- `@pytest.mark.slow`
- `@pytest.mark.adult`
- `@pytest.mark.cloudflare`

---

### ⬆️ Dépendances

#### Versions minimales
| Dépendance | Version | Raison |
|-----------|:-------:|--------|
| Python | 3.12 | `asyncio.TaskGroup`, perf |
| Pydantic | 2.9 | Validation, perf |
| httpx | 0.27 | HTTP/2, async |
| Playwright | 1.48 | Bypass Cloudflare |
| Textual | 0.85 | TUI moderne |
| FastAPI | 0.115 | Web backend |
| Next.js | 14 | Frontend App Router |

#### Politique de mise à jour
- **Patch** : automatique (Dependabot)
- **Minor** : revue manuelle
- **Major** : ADR + tests + migration guide

---

### 🛠️ Outillage

#### Ajout de `nexus.dl`
- Manifeste par dossier
- Validation automatique via pre-commit
- Génération via `scripts/generate_nexus_manifests.py`

#### Ajout de `pre-commit`
- Hooks de qualité (ruff, mypy)
- Hooks de sécurité (bandit, gitleaks)
- Hooks NexusDL (nexus.dl, sites.yaml, SPDX)

#### Ajout de la CI GitHub Actions
- Workflow `ci.yml` : lint + test + build
- Workflow `codeql.yml` : analyse sécurité
- Workflow `release.yml` : publication PyPI

---

## [1.0.0-alpha.1] - 2026-01-15

### 💥 BREAKING CHANGES

#### 🔴 Renommage du projet : SushiDL → NexusDL
- **Raison** : Refonte complète, portée universelle
- **Migration** :
  - Package Python : `import sushidl` → `import nexusdl`
  - Commande CLI : `sushidl` → `nexusdl`
  - Config : `~/.config/sushidl/` → `~/.config/nexusdl/`
  - Cookies : `cookies.json` → `cookies.enc` (chiffré)
  - Base de données : schéma entièrement nouveau

#### 🔴 Changement d'architecture
- **Avant** : script monolithique `sushidl.py`
- **Après** : architecture hexagonale `src/nexusdl/`
- **Migration** : réécriture complète nécessaire pour tout code intégrant l'ancien SushiDL

#### 🔴 Migration vers Python 3.12+
- **Avant** : Python 3.9+
- **Après** : Python 3.12+ (requis pour `TaskGroup`)
- **Migration** : mettre à jour votre environnement Python

#### 🔴 Configuration : JSON → YAML + Pydantic
- **Avant** : `config.json` non validé
- **Après** : `config.yaml` validé par Pydantic Settings
- **Migration** :
  ```bash
  nexusdl config migrate --from sushidl
  ```

#### 🔴 Formats de sortie : `.zip` → `.cbz` par défaut
- **Avant** : archives `.zip` classiques
- **Après** : `.cbz` (standard comics) par défaut
- **Migration** : configurer `download.format: zip` pour l'ancien comportement

---

### 🏗️ Architecture

- **Nouveau** : architecture hexagonale stricte
- **Nouveau** : séparation `core/` / `parsers/` / `interfaces/`
- **Nouveau** : `plugins/` (extensions communautaires)
- **Nouveau** : event bus asyncio (`core/events.py`)
- **Nouveau** : registre des sites centralisé (`sites.yaml`)

---

### 🔧 API publique

- **Nouveau** : classe `NexusDL` (API haut niveau)
- **Nouveau** : `BaseParser` (interface stable)
- **Nouveau** : `BasePackager` (interface stable)
- **Nouveau** : `BasePlugin` (interface stable)
- **Nouveau** : réexport des modèles dans `nexusdl.core.models`

---

### 📦 Schémas de données

- **Nouveau** : tous les modèles sont Pydantic v2
- **Nouveau** : `model_config = ConfigDict(frozen=True, extra="forbid")` par défaut
- **Nouveau** : validation stricte des URLs (`HttpUrl`)
- **Nouveau** : enums typés (`Language`, `ContentRating`, `MangaStatus`, `PackagingFormat`)

---

### 🌐 Parsers

- **Nouveau** : 60+ parsers (FR, EN, KR, Adult)
- **Nouveau** : système de mixins
- **Nouveau** : `CloudflareMixin` pour bypass Cloudflare
- **Nouveau** : `ApiBasedMixin` pour sites API
- **Nouveau** : **MangaDex** (API officielle)
- **Nouveau** : **nHentai** (API non officielle)
- **Nouveau** : **HentaiZone** (Playwright)
- **Nouveau** : **MangaFire**, **MangaBuddy**, **Toonily** (recommandés)

---

### 📦 Packaging

- **Nouveau** : formats ZIP, CBZ, CBR, PDF, Folder
- **Nouveau** : `ComicInfo.xml` (standard ComicRack)
- **Nouveau** : conversion d'images (webp → jpg, avif → png)
- **Nouveau** : protection Zip Slip

---

### 🔌 Plugins

- **Nouveau** : système de plugins complet
- **Nouveau** : chargement dynamique
- **Nouveau** : système de hooks (`@hook("event")`)
- **Nouveau** : permissions déclaratives
- **Nouveau** : validation des plugins

---

### 🔒 Sécurité

- **Nouveau** : chiffrement des cookies (Fernet)
- **Nouveau** : validation stricte des entrées (Pydantic)
- **Nouveau** : protection Zip Slip
- **Nouveau** : audit de sécurité (bandit, pip-audit)
- **Nouveau** : scan de secrets (gitleaks)
- **Nouveau** : `SECURITY.md` + `SECURITY_HALL_OF_FAME.md`

---

### ⚡ Performance

- **Amélioration** : 10x plus rapide (async + selectolax)
- **Amélioration** : pool Playwright partagé (-80% mémoire)
- **Nouveau** : cache HTTP (LRU)
- **Nouveau** : rate limiting intelligent (token-bucket)
- **Nouveau** : téléchargement parallèle configurable

---

### ♻️ Refactoring

Voir la section [Refactoring](#-refactoring) ci-dessus pour la table complète de migration SushiDL → NexusDL.

---

### 🐛 Corrections

- **Nouveau** : tous les bugs connus de SushiDL n'existent plus (réécriture)
- **Nouveau** : gestion propre des erreurs async
- **Nouveau** : annulation propre des tâches

---

### 📝 Documentation

- **Nouveau** : `README.md` complet
- **Nouveau** : `CONTRIBUTING.md` exhaustif
- **Nouveau** : `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1)
- **Nouveau** : `SECURITY.md` + `SECURITY_HALL_OF_FAME.md`
- **Nouveau** : `docs/API.md`
- **Nouveau** : `docs/CHANGELOG_DEV.md` (ce fichier)
- **Nouveau** : système `nexus.dl`
- **Nouveau** : docstrings Google-style partout

---

### 🧪 Tests

- **Nouveau** : structure `unit/`, `integration/`, `e2e/`
- **Nouveau** : fixtures HTML/JSON
- **Nouveau** : couverture cible 85%
- **Nouveau** : marqueurs pytest

---

### ⬆️ Dépendances

Voir la section [Dépendances](#-dépendances-1) ci-dessus.

---

### 🛠️ Outillage

- **Nouveau** : `pyproject.toml` (PEP 621)
- **Nouveau** : `Makefile` + `justfile`
- **Nouveau** : `tox.ini` (multi-env)
- **Nouveau** : `.pre-commit-config.yaml`
- **Nouveau** : CI/CD GitHub Actions
- **Nouveau** : Docker (CLI, Web, GUI)

---

## Pré-1.0 (historique)

### [SushiDL 11.x] - Projet d'origine

Le projet **SushiDL** original n'a pas de changelog dev structuré. Voir [SushiDL](https://github.com/NEXUS-QUANTUM/sushidl-legacy) pour l'historique complet.

**Résumé SushiDL** :
- Développé pour supporter quelques sites FR
- Script monolithique Python
- `requests` sync + BeautifulSoup
- Config JSON
- GUI Tkinter basique
- Pas de tests, pas de typage, pas de CI

**Raison du fork** :
- Support de plus en plus de sites demandé
- Maintenance difficile du code monolithique
- Besoin de plus de robustesse (async, typage, tests)
- Volonté d'une communauté open-source active

---

## 📋 Guide de migration SushiDL → NexusDL

### 🔄 Étape 1 : Sauvegarder l'ancienne config

```bash
cp ~/.config/sushidl/config.json ~/sushidl-config-backup.json
```

### 🔄 Étape 2 : Installer NexusDL

```bash
# Désinstaller SushiDL (optionnel)
pip uninstall sushidl

# Installer NexusDL
uv tool install nexusdl
playwright install chromium
```

### 🔄 Étape 3 : Migrer la config

```bash
nexusdl config migrate --from ~/sushidl-config-backup.json
```

**Ce que fait la commande** :
1. Lit l'ancienne config JSON
2. Convertit au nouveau schéma YAML
3. Valide avec Pydantic
4. Écrit dans `~/.config/nexusdl/config.yaml`
5. Avertit sur les incompatibilités

### 🔄 Étape 4 : Migrer la bibliothèque (optionnel)

Si vous aviez des mangas téléchargés :

```bash
nexusdl library import --from /chemin/vers/anciens/mangas --scan
```

### 🔄 Étape 5 : Vérifier

```bash
# Valider la config
nexusdl config validate

# Voir les sites disponibles
nexusdl sites

# Tester un download
nexusdl download "https://sushiscan.net/..." --format cbz
```

### 🚨 Incompatibilités connues

| Fonctionnalité SushiDL | Statut NexusDL | Alternative |
|------------------------|:--------------:|-------------|
| Config JSON | 🔴 Obsolète | YAML (`config.yaml`) |
| Formats `.zip` par défaut | 🟡 Changé | CBZ par défaut (`download.format: zip` pour revert) |
| Ancien schéma BDD | 🔴 Obsolète | Migration auto via `nexusdl db migrate` |
| Plugins SushiDL | 🔴 Obsolète | Réécriture nécessaire |
| API Python | 🔴 Obsolète | Nouvelle API `NexusDL` |

---

## 📚 Ressources pour développeurs

### Documentation interne

- 📖 **[Architecture](source/development/architecture.md)** — Vue d'ensemble
- 📖 **[Ajouter un site](source/development/adding_sites.md)** — Guide parser
- 📖 **[Plugins](source/development/plugins.md)** — Guide plugin
- 📖 **[API Reference](API.md)** — API REST + Python
- 📖 **[CHANGELOG](../CHANGELOG.md)** — Changelog public

### ADR (Architecture Decision Records)

Les ADR sont dans `docs/adr/` :

| # | Titre | Statut |
|:-:|-------|:------:|
| [ADR-001](#adr-001--choix-de-larchitecture-hexagonale) | Architecture hexagonale | ✅ |
| [ADR-002](#adr-002--choix-de-pydantic-v2) | Pydantic v2 | ✅ |
| [ADR-003](#adr-003--choix-de-uv-sur-pippoetrypdm) | uv | ✅ |
| [ADR-004](#adr-004--choix-de-textual-pour-la-tui) | Textual | ✅ |
| [ADR-005](#adr-005--choix-de-fastapi--nextjs-pour-le-web) | FastAPI + Next.js | ✅ |
| [ADR-006](#adr-006--système-de-mixins-pour-les-parsers) | Mixins parsers | ✅ |

### Outils de développement

| Outil | Commande | Usage |
|-------|----------|-------|
| **Tests** | `make test` | Lancer les tests |
| **Lint** | `make lint` | Ruff + auto-fix |
| **Types** | `make type-check` | Mypy strict |
| **Sécurité** | `make security` | Bandit + pip-audit |
| **Build** | `make build` | Wheel + sdist |
| **Docs** | `make docs` | Build Sphinx |
| **Nexus.dl** | `make validate-manifests` | Valider les manifestes |
| **Tox** | `make tox` | Matrice complète |

### Contributeurs réguliers

Voir **[CONTRIBUTORS.md](../CONTRIBUTORS.md)** (à venir).

---

## 🎓 Comment utiliser ce fichier

### 👨‍💻 En tant que contributeur

**Avant de soumettre une PR** :

1. ✅ Lire les entrées `[Unreleased]`
2. ✅ Vérifier si votre changement est un breaking change
3. ✅ Ajouter une entrée dans `[Unreleased]` si nécessaire
4. ✅ Suivre les conventions de format

**Format d'ajout** :

```markdown
### 🔧 API publique

- **Ajout** : `NexusDL.new_method()` pour ...
- **Modification** : `NexusDL.search()` accepte maintenant `param`
- **Dépréciation** : `NexusDL.old_method()` → utiliser `NexusDL.new_method()`
```

### 🔧 En tant que développeur de plugins

**À chaque mise à jour de NexusDL** :

1. ✅ Lire les sections `💥 BREAKING CHANGES` et `🔌 Plugins`
2. ✅ Adapter votre plugin si nécessaire
3. ✅ Tester avec la nouvelle version

**S'abonner aux changements** :

- 🔔 Watch le repo GitHub (releases)
- 🔔 Suivre le canal `#dev-announcements` sur Discord
- 🔔 S'abonner à `docs/CHANGELOG_DEV.md` (RSS)

### 🕷️ En tant que développeur de parsers

**À chaque mise à jour de NexusDL** :

1. ✅ Lire la section `🌐 Parsers`
2. ✅ Vérifier que `BaseParser` n'a pas changé
3. ✅ Vérifier les nouveaux mixins disponibles
4. ✅ Adapter si nécessaire

---

## 🔔 S'abonner aux changements

| Canal | Format | Fréquence |
|-------|--------|-----------|
| **[GitHub Releases](https://github.com/NEXUS-QUANTUM/nexusdl/releases)** | Notes de version | À chaque release |
| **[Discord #announcements](https://discord.gg/NEXUS-QUANTUM)** | Message | À chaque release |
| **[Twitter @NEXUS-QUANTUM](https://x.com/NEXUS-QUANTUM)** | Tweet | À chaque release |
| **[RSS/Atom](https://github.com/NEXUS-QUANTUM/nexusdl/releases.atom)** | Flux | Temps réel |
| **[Watch GitHub](https://github.com/NEXUS-QUANTUM/nexusdl)** | Notifications | Configurable |

---

## 📞 Support développeur

| Canal | Usage | Lien |
|-------|-------|------|
| 💬 **Discord #dev** | Questions techniques | [Rejoindre](https://discord.gg/NEXUS-QUANTUM) |
| 💡 **GitHub Discussions** | Débats, idées | [Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions) |
| 🐛 **GitHub Issues** | Bugs, features | [Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues) |
| 📧 **Email dev** | Contact privé | `dev@nexus-quantum.dev` |
| 🔒 **Sécurité** | Vulnérabilités | `security@nexus-quantum.dev` |

---

## 📜 Convention de nommage des entrées

### Types de changements internes

| Emoji | Section | Quand |
|:-----:|---------|-------|
| 💥 | BREAKING CHANGES | Toute modification cassante |
| 🏗️ | Architecture | Structure, couches, patterns |
| 🔧 | API publique | Classes, méthodes, signatures |
| 📦 | Schémas de données | Pydantic, migrations SQL |
| 🌐 | Parsers | `BaseParser`, mixins |
| 📦 | Packaging | `BasePackager`, formats |
| 🔌 | Plugins | API plugins, hooks |
| 🔒 | Sécurité | Correctifs internes |
| ⚡ | Performance | Optimisations |
| ♻️ | Refactoring | Réorganisations |
| 🐛 | Corrections | Bugs internes |
| 📝 | Documentation | Doc dev, ADR |
| 🧪 | Tests | Couverture, marqueurs |
| ⬆️ | Dépendances | Mises à jour critiques |
| 🛠️ | Outillage | CI, pre-commit, build |

### Format des lignes

```markdown
- **Action** : Description courte + **Migration** : étapes si breaking
```

Exemples :
```markdown
- **Ajout** : `NexusDL.export_library()` pour exporter en JSON
- **Modification** : `BaseParser.search()` accepte `sort` en paramètre
- **Dépréciation** : `Manga.legacy_id` → utiliser `Manga.id`
- **Suppression** : `NexusDL.old_api()` (remplacé par `new_api()`)
```

---

## 📅 Historique des versions

| Version | Date | Type | Impact |
|:-------:|:----:|:----:|:------:|
| [Unreleased](#unreleased) | — | Dev | — |
| [1.0.0-alpha.1](#100-alpha1---2026-01-15) | 2026-01-15 | Alpha | 💥 |
| [SushiDL 11.x](#sushidl-11x---projet-dorigine) | ≤2025 | Legacy | ⚫ |

---

## 🎯 Politique de dépréciation

Toute dépréciation suit ce cycle :

```
┌──────────────────────────────────────────────────────┐
│ 1. Annonce dans CHANGELOG_DEV.md                     │
│    + warning dans les logs (DeprecationWarning)      │
│    + docstring @deprecated                           │
├──────────────────────────────────────────────────────┤
│ 2. Période de dépréciation                           │
│    - 6 mois minimum                                  │
│    - 2 versions mineures minimum                     │
│    - Documentation de l'alternative                  │
├──────────────────────────────────────────────────────┤
│ 3. Suppression                                       │
│    - Nouvelle version MAJOR                          │
│    - Migration guide dans CHANGELOG_DEV.md           │
│    - Codemod si possible                             │
└──────────────────────────────────────────────────────┘
```

### Exemple de dépréciation

```python
import warnings
from typing import deprecated  # Python 3.13+

@deprecated("Utiliser NexusDL.new_api() à la place. Sera supprimé en 2.0.0")
async def old_api(self) -> None:
    """Méthode obsolète.

    .. deprecated:: 1.1.0
        Utiliser :meth:`new_api` à la place.
    """
    warnings.warn(
        "old_api() est dépréciée, utiliser new_api()",
        DeprecationWarning,
        stacklevel=2,
    )
    return await self.new_api()
```

---

## 🚨 Alertes critiques

Les entrées suivantes nécessitent une **action immédiate** :

| Version | Alerte | Action |
|:-------:|--------|--------|
| — | Aucune alerte critique en cours | — |

*Cette section sera mise à jour en cas de CVE, faille critique ou breaking change urgent.*

---

## 🤝 Contribuer à ce fichier

### Règles

- ✅ **Ne pas supprimer** les entrées passées (historique)
- ✅ **Ajouter** dans `[Unreleased]`
- ✅ **Suivre** les conventions de format
- ✅ **Mentionner** les breaking changes explicitement
- ✅ **Fournir** des guides de migration
- ❌ **Ne pas** ajouter d'entrées cosmétiques (typos, formatage)
- ❌ **Ne pas** dupliquer le `CHANGELOG.md` public

### Processus

```bash
# 1. Créer une branche
git checkout -b docs/changelog-dev-xyz

# 2. Éditer CHANGELOG_DEV.md
# Ajouter l'entrée dans [Unreleased] > section appropriée

# 3. Commit
git commit -s -m "docs(changelog-dev): add entry for xyz"

# 4. PR
gh pr create --title "docs(changelog-dev): add entry for xyz"
```

### Template d'ajout

```markdown
### 🏗️ Architecture

- **ADR-XXX** : Titre de la décision
  - **Date** : AAAA-MM-JJ
  - **Statut** : ✅ Accepté / ❌ Rejeté / ⏳ En cours
  - **Décision** : ...
  - **Conséquences** : ...

### 🔧 API publique

- **Ajout** : `NexusDL.new_method()` pour ...
- **Modification** : `NexusDL.existing_method()` accepte maintenant `new_param`
- **Dépréciation** : `NexusDL.old_method()` → utiliser `new_method()`
- **Suppression** : `NexusDL.removed_method()` (remplacée par `replacement()`)
```

---

<div align="center">

## 🌌 NexusDL — Dev Changelog

**Ce fichier est maintenu par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM) et la communauté.**

*Dernière mise à jour : 2026-01-15*

---

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)
[![Email](https://img.shields.io/badge/Email-dev@nexus--quantum.dev-8B89CC?style=for-the-badge&logo=protonmail&logoColor=white)](mailto:dev@nexus-quantum.dev)

**Fait avec ❤️ pour les développeurs.**

</div>
```
