# 🤝 Contributing to NexusDL

<div align="center">

**Merci de vouloir contribuer à NexusDL !**

*Chaque contribution — code, documentation, traduction, rapport de bug, idée — rend ce projet meilleur.*

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge)](CONTRIBUTING.md)
[![Good First Issue](https://img.shields.io/badge/Good%20First%20Issue-✨-blueviolet?style=for-the-badge)](https://github.com/NEXUS-QUANTUM/nexusdl/labels/good%20first%20issue)
[![Help Wanted](https://img.shields.io/badge/Help%20Wanted-🙋-orange?style=for-the-badge)](https://github.com/NEXUS-QUANTUM/nexusdl/labels/help%20wanted)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa?style=for-the-badge)](CODE_OF_CONDUCT.md)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue?style=for-the-badge)](LICENSE)

</div>

---

## 📖 Table des matières

- [Code de conduite](#-code-de-conduite)
- [Premiers pas](#-premiers-pas)
- [Types de contributions](#-types-de-contributions)
- [Environnement de développement](#-environnement-de-développement)
- [Workflow Git](#-workflow-git)
- [Conventions de code](#-conventions-de-code)
- [Ajouter un nouveau site](#-ajouter-un-nouveau-site)
- [Développer un plugin](#-développer-un-plugin)
- [Tests](#-tests)
- [Documentation](#-documentation)
- [Traductions](#-traductions)
- [Système `nexus.dl`](#-système-nexusdl)
- [Processus de Pull Request](#-processus-de-pull-request)
- [Processus de review](#-processus-de-review)
- [Signaler un bug](#-signaler-un-bug)
- [Proposer une fonctionnalité](#-proposer-une-fonctionnalité)
- [Signaler une vulnérabilité](#-signaler-une-vulnérabilité)
- [Reconnaissance](#-reconnaissance)
- [Questions ?](#-questions-)

---

## 📜 Code de conduite

En participant à ce projet, vous vous engagez à respecter notre **[Code de Conduite](CODE_OF_CONDUCT.md)** (Contributor Covenant 2.1).

**En résumé :**
- ✅ Soyez respectueux et bienveillant
- ✅ Acceptez les critiques constructives
- ✅ Focalisez sur ce qui est meilleur pour la communauté
- ❌ Pas de harcèlement, discrimination, ou comportement toxique
- ❌ Pas de spam, troll, ou provocations

Les violations peuvent être signalées à **conduct@nexus-quantum.dev** (confidentiel).

---

## 🚀 Premiers pas

### 🎯 Par où commencer ?

**Vous débutez ?** Cherchez les issues avec :
- 🟢 [`good first issue`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/good%20first%20issue) — Idéal pour débuter
- 🟡 [`help wanted`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/help%20wanted) — Besoin d'aide
- 🔵 [`documentation`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/documentation) — Améliorer la doc
- 🟣 [`translation`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/translation) — Traductions

**Vous êtes expérimenté ?** Explorez :
- 🔴 [`bug`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/bug) — Bugs à corriger
- 🟠 [`enhancement`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/enhancement) — Nouvelles fonctionnalités
- 🟤 [`new site`](https://github.com/NEXUS-QUANTUM/nexusdl/labels/new%20site) — Nouveaux sites à supporter

### 📋 Checklist avant de contribuer

- [ ] J'ai lu le [Code de Conduite](CODE_OF_CONDUCT.md)
- [ ] J'ai lu ce guide en entier
- [ ] J'ai lu le [README](README.md)
- [ ] J'ai vérifié que ma contribution n'existe pas déjà (issues/PR)
- [ ] J'ai cherché dans les [Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)
- [ ] J'ai rejoint le [Discord](https://discord.gg/NEXUS-QUANTUM) (optionnel, recommandé)

---

## 🎯 Types de contributions

NexusDL accepte **toutes les formes** de contributions. Voici les principales :

| Type | Effort | Impact | Exemple |
|------|:------:|:------:|---------|
| 🐛 **Bug fix** | ⭐⭐ | 🔥🔥🔥 | Corriger un parser cassé |
| ✨ **Nouveau parser** | ⭐⭐⭐ | 🔥🔥🔥 | Ajouter MangaFire |
| 📝 **Documentation** | ⭐ | 🔥🔥 | Améliorer le README |
| 🌍 **Traduction** | ⭐⭐ | 🔥🔥 | Ajouter l'espagnol |
| 🎨 **UI/UX** | ⭐⭐⭐ | 🔥🔥 | Améliorer l'interface web |
| 🧪 **Tests** | ⭐⭐ | 🔥🔥🔥 | Augmenter la couverture |
| ⚡ **Performance** | ⭐⭐⭐ | 🔥🔥 | Optimiser le downloader |
| 🔒 **Sécurité** | ⭐⭐⭐⭐ | 🔥🔥🔥🔥 | Corriger une vulnérabilité |
| 🔌 **Plugin** | ⭐⭐⭐ | 🔥 | Créer un plugin d'export |
| 💡 **Idée / Feedback** | ⭐ | 🔥 | Ouvrir une discussion |

**Aucune contribution n'est trop petite.** Un typo corrigé, une phrase reformulée, un test ajouté — tout compte.

---

## 🛠️ Environnement de développement

### 📦 Prérequis

| Outil | Version minimale | Recommandé | Installation |
|-------|:----------------:|:----------:|--------------|
| **Python** | 3.12 | 3.12.x | [python.org](https://www.python.org/downloads/) |
| **uv** | 0.5.0 | Dernière | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Git** | 2.40 | Dernière | [git-scm.com](https://git-scm.com/) |
| **just** (optionnel) | 1.36 | Dernière | `cargo install just` |
| **Node.js** (web) | 20 | 22 LTS | [nodejs.org](https://nodejs.org/) |
| **pnpm** (web) | 9 | Dernière | `npm i -g pnpm` |
| **Docker** (optionnel) | 24 | Dernière | [docker.com](https://www.docker.com/) |

### ⚡ Installation en 3 commandes

```bash
# 1. Fork + clone
git clone https://github.com/VOTRE_USERNAME/nexusdl.git
cd nexusdl

# 2. Bootstrap complet (uv sync + playwright + hooks)
make bootstrap    # ou : just bootstrap

# 3. Vérification
make test         # ou : just test
```

### 🔧 Installation manuelle (si `make`/`just` indisponibles)

```bash
# Fork + clone
git clone https://github.com/VOTRE_USERNAME/nexusdl.git
cd nexusdl

# Créer un venv isolé
uv venv
source .venv/bin/activate  # Windows : .venv\Scripts\activate

# Installer toutes les dépendances (core + extras + dev)
uv sync --all-extras --dev

# Installer les navigateurs Playwright
uv run playwright install chromium

# Installer les hooks pre-commit
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg

# Test rapide
uv run nexusdl --version
uv run pytest
```

### 🐳 Alternative Docker

```bash
# Stack de développement complète
docker compose -f docker/docker-compose.dev.yml up -d

# Shell dans le conteneur
docker compose -f docker/docker-compose.dev.yml exec nexusdl bash
```

### ✅ Vérifier l'installation

```bash
# Toutes les vérifications
make check

# Résultat attendu :
# ✅ Ruff check .................. PASS
# ✅ Ruff format ................. PASS
# ✅ Mypy strict ................. PASS
# ✅ Bandit ...................... PASS
# ✅ pip-audit ................... PASS
```

### 📁 Structure du projet

```
nexusdl/
├── src/nexusdl/              # 📦 Code source principal
│   ├── core/                 # ⚙️  Domaine (ne dépend de rien)
│   ├── parsers/              # 🕷️  Scrapers (dépend de core)
│   ├── interfaces/           # 🎨 CLI, Web, GUI (dépend de tout)
│   ├── plugins/              # 🔌 Système de plugins
│   └── data/                 # 📁 Ressources embarquées
├── scripts/                  # 🛠️  Scripts utilitaires
├── docs/                     # 📚 Documentation Sphinx
├── docker/                   # 🐳 Dockerfiles + compose
├── config/                   # ⚙️  Configs exemple
└── .github/                  # 🤖 CI/CD + templates
```

> 💡 **Chaque dossier contient un `nexus.dl`** qui documente sa fonction, son contenu et ses règles. Consultez-le avant de modifier quoi que ce soit.

---

## 🔀 Workflow Git

### 🌿 Branches

| Type | Format | Exemple | Usage |
|------|--------|---------|-------|
| **main** | `main` | `main` | Production stable |
| **develop** | `develop` | `develop` | Intégration continue |
| **feature** | `feature/<nom>` | `feature/mangafire-parser` | Nouvelle fonctionnalité |
| **fix** | `fix/<nom>` | `fix/cloudflare-cookie` | Correction de bug |
| **hotfix** | `hotfix/<nom>` | `hotfix/cve-2026-1234` | Correctif urgent |
| **docs** | `docs/<nom>` | `docs/installation-guide` | Documentation |
| **refactor** | `refactor/<nom>` | `refactor/session-pool` | Refactoring |
| **chore** | `chore/<nom>` | `chore/update-deps` | Tâches diverses |
| **parser** | `parser/<site>` | `parser/mangafire` | Nouveau parser |

### 📝 Convention de commits (Conventional Commits)

Nous utilisons **[Conventional Commits 1.0.0](https://www.conventionalcommits.org/)** :

```
<type>(<scope>): <description>

[corps optionnel]

[footer(s) optionnel(s)]
```

### Types autorisés

| Type | Description | Exemple |
|------|-------------|---------|
| `feat` | Nouvelle fonctionnalité | `feat(parsers): add mangafire support` |
| `fix` | Correction de bug | `fix(session): handle cloudflare 503` |
| `docs` | Documentation | `docs: update installation guide` |
| `style` | Formatage (pas de changement logique) | `style: reformat parsers` |
| `refactor` | Refactoring | `refactor(core): extract session pool` |
| `perf` | Performance | `perf(downloader): use uvloop` |
| `test` | Tests | `test(parsers): add nhentai tests` |
| `chore` | Tâches (deps, config, CI) | `chore(deps): bump pydantic to 2.10` |
| `ci` | CI/CD | `ci: add codeql workflow` |
| `build` | Build | `build: switch to hatchling` |
| `revert` | Revert | `revert: feat(parsers): add xyz` |

### Scopes recommandés

- `core` — Domaine métier
- `parsers` — Scrapers
- `parsers/fr` — Parsers FR
- `parsers/en` — Parsers EN
- `parsers/adult` — Parsers adultes
- `session` — Gestion sessions HTTP
- `downloader` — Moteur de téléchargement
- `packaging` — Empaquetage
- `library` — Bibliothèque
- `cli` — Interface CLI
- `web` — Interface web
- `web/backend` — Backend FastAPI
- `web/frontend` — Frontend Next.js
- `gui` — Interface desktop
- `plugins` — Système de plugins
- `docs` — Documentation
- `ci` — CI/CD
- `deps` — Dépendances
- `docker` — Docker
- `security` — Sécurité

### ✍️ Exemples de bons commits

```
feat(parsers/fr): add sushiscan.net support

Ajoute le parser pour sushiscan.net qui utilise le thème
WordPress Madara. Réutilise le mixin MadaraMixin existant.

Closes #123
```

```
fix(session): correct cookie cache bug on refresh

Le cache des cookies retournait 5 valeurs au lieu de 6
lors du refresh, causant un crash sur les sites Cloudflare.

Fixes #456
```

```
docs(contributing): add parser development guide

Ajoute une section complète sur la création de nouveaux
parsers, avec exemples et bonnes pratiques.

Signed-off-by: Jean Dupont <jean@example.com>
```

### 🚫 Mauvais exemples

```
❌ update                          # Pas de type
❌ fix bug                         # Pas de scope
❌ feat: Add MangaFire Parser      # Majuscules
❌ FEAT(parsers): add mangafire    # Type en majuscules
❌ feat(parsers): add mangafire.   # Point final
❌ added mangafire parser          # Mauvais temps (utiliser "add")
```

### 🔧 Commit avec signature

Nous encourageons la **signature DCO** (Developer Certificate of Origin) :

```bash
git commit -s -m "feat(parsers): add mangafire"
```

Cela ajoute automatiquement :
```
Signed-off-by: Votre Nom <votre@email.com>
```

### 🔄 Workflow complet

```bash
# 1. Synchroniser votre fork
git checkout main
git pull upstream main
git push origin main

# 2. Créer une branche
git checkout -b feature/mangafire-parser

# 3. Coder...

# 4. Vérifier avant de commit
make check        # Lint + type + sécurité
make test         # Tests

# 5. Commit (avec signature DCO)
git add .
git commit -s -m "feat(parsers): add mangafire support"

# 6. Push vers votre fork
git push origin feature/mangafire-parser

# 7. Ouvrir une PR sur GitHub
```

---

## 🎨 Conventions de code

NexusDL applique des standards **stricts** pour garantir la qualité, la lisibilité et la maintenabilité.

### 📏 Standards globaux

| Aspect | Standard | Vérifié par |
|--------|----------|-------------|
| **Style** | Ruff format (Black-compatible) | `ruff format --check` |
| **Lint** | Ruff (~40 règles) | `ruff check` |
| **Typage** | Mypy **strict** | `mypy --strict` |
| **Line length** | 100 caractères | Ruff |
| **Quotes** | Doubles `"` | Ruff |
| **Indentation** | 4 espaces | Ruff |
| **Line ending** | LF (`\n`) | EditorConfig |
| **Encoding** | UTF-8 | EditorConfig |
| **Docstrings** | Google-style | Ruff (pydocstyle) |

### 🐍 Style Python

#### En-tête de fichier obligatoire

```python
# SPDX-FileCopyrightText: 2026 NEXUS-QUANTUM <nexus.quantum@protonmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of NexusDL.

"""Description courte du module en une ligne.

Description plus longue si nécessaire, sur plusieurs lignes.
Explique le rôle du module, son contexte, ses dépendances
notables, et tout ce qui est utile pour comprendre le code.
"""
from __future__ import annotations

# Imports stdlib
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

# Imports third-party
import httpx
from pydantic import BaseModel

# Imports locaux
from nexusdl.core.exceptions import NexusDLError

if TYPE_CHECKING:
    from nexusdl.core.models import Manga

# ... code
```

#### Typage strict partout

```python
# ✅ BIEN
async def download_chapter(
    chapter: Chapter,
    dest: Path,
    *,
    fmt: PackagingFormat = PackagingFormat.CBZ,
    overwrite: bool = False,
) -> DownloadResult:
    """Télécharge un chapitre et l'empaquette.

    Args:
        chapter: Chapitre à télécharger.
        dest: Dossier de destination.
        fmt: Format d'empaquetage.
        overwrite: Écrase si existe déjà.

    Returns:
        Le résultat du téléchargement.

    Raises:
        ChapterDownloadError: Si une page échoue.
    """
    ...

# ❌ MAL
def download_chapter(chapter, dest, fmt=None):
    ...
```

#### Docstrings Google-style

```python
def calculate_checksum(data: bytes, algorithm: str = "sha256") -> str:
    """Calcule le checksum d'un blob de données.

    Args:
        data: Les données brutes à hacher.
        algorithm: Algorithme de hachage (`sha256`, `sha1`, `md5`).

    Returns:
        Le checksum encodé en hexadécimal.

    Raises:
        ValueError: Si l'algorithme n'est pas supporté.

    Example:
        >>> calculate_checksum(b"hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e...'

    Note:
        Le checksum est calculé sur les données brutes,
        pas sur une représentation encodée.
    """
    ...
```

#### Async/await

```python
# ✅ BIEN — Utiliser TaskGroup (Python 3.11+)
async def download_all(urls: list[str]) -> list[bytes]:
    """Télécharge plusieurs URLs en parallèle."""
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(self.download(u)) for u in urls]
    return [t.result() for t in tasks]

# ✅ BIEN — Semaphore pour limiter la concurrence
async def download_all_limited(urls: list[str], max_concurrent: int = 8) -> list[bytes]:
    """Télécharge avec limite de concurrence."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(url: str) -> bytes:
        async with sem:
            return await self.download(url)

    return await asyncio.gather(*(_one(u) for u in urls))

# ❌ MAL — Pas de gestion des erreurs
async def bad():
    asyncio.gather(*tasks)  # Résultats perdus
```

#### Gestion d'erreurs

```python
# ✅ BIEN
try:
    result = await self.session.get(url)
except httpx.TimeoutException as e:
    logger.warning("Timeout sur {} après {}s", url, e.request.extensions)
    raise DownloadTimeoutError(url) from e
except httpx.HTTPStatusError as e:
    if e.response.status_code == 404:
        raise ChapterNotFoundError(chapter.id) from e
    raise

# ❌ MAL
try:
    result = await self.session.get(url)
except:  # noqa: E722
    pass  # Exception avalée
```

#### Logging structuré

```python
from loguru import logger

# ✅ BIEN — Contexte + format lazy
logger.bind(site=self.site_id, chapter=chapter.id).info(
    "Téléchargement démarré : {} pages", len(chapter.pages)
)

# ❌ MAL — f-string (évaluation immédiate) + print
print(f"Downloading {len(chapter.pages)} pages")  # noqa: T201
```

### 📁 Conventions de nommage

| Élément | Convention | Exemple |
|---------|------------|---------|
| Module | `snake_case` | `http_session.py` |
| Package | `snake_case` | `parsers/` |
| Classe | `PascalCase` | `HttpSession` |
| Fonction | `snake_case` | `download_chapter` |
| Méthode privée | `_snake_case` | `_internal_helper` |
| Méthode mangling | `__snake_case` | `__very_private` |
| Constante | `SCREAMING_SNAKE_CASE` | `MAX_RETRIES` |
| Variable globale | `SCREAMING_SNAKE_CASE` | `DEFAULT_TIMEOUT` |
| Type alias | `PascalCase` | `ProgressCallback = ...` |
| TypeVar | `PascalCase` | `T = TypeVar("T")` |

### 🏗️ Patterns obligatoires

#### ABC pour les interfaces

```python
from abc import ABC, abstractmethod

class BasePackager(ABC):
    """Interface abstraite pour tous les packagers."""

    format: ClassVar[PackagingFormat]

    @abstractmethod
    async def package(self, pages: list[Path], output: Path) -> Path:
        """Empaquette les pages dans le format cible."""
        ...
```

#### Pydantic v2 pour les modèles

```python
from pydantic import BaseModel, Field, HttpUrl, ConfigDict

class Manga(BaseModel):
    """Métadonnées d'un manga."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1)
    cover_url: HttpUrl | None = None
    chapters: list[Chapter] = Field(default_factory=list)
```

#### Dependency Injection

```python
# ✅ BIEN — Injection par constructeur
class DownloadManager:
    def __init__(
        self,
        registry: SiteRegistry,
        session_factory: SessionFactory,
        packager_factory: PackagerFactory,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._packager_factory = packager_factory

# ❌ MAL — Instanciation en dur
class BadDownloadManager:
    def __init__(self) -> None:
        self.registry = SiteRegistry()  # Couplage fort
```

### 🚫 Anti-patterns interdits

| Anti-pattern | Interdit | Alternative |
|--------------|:--------:|-------------|
| `except: pass` | 🚫 | Log + re-raise une exception custom |
| `eval()` / `exec()` | 🚫 | `ast.literal_eval()` ou parsing |
| `pickle.loads()` sur données user | 🚫 | JSON + Pydantic |
| `yaml.load()` sans Loader | 🚫 | `yaml.safe_load()` |
| `subprocess` avec `shell=True` | 🚫 | `shell=False` + liste d'args |
| `os.system()` | 🚫 | `asyncio.create_subprocess_exec()` |
| Secrets en dur | 🚫 | `pydantic-settings` + `.env` |
| SQL par concaténation | 🚫 | Paramètres ou ORM |
| `time.sleep()` en async | 🚫 | `asyncio.sleep()` |
| `requests` sync dans async | 🚫 | `httpx.AsyncClient` |
| `print()` en prod | 🚫 | `loguru.logger` |
| `# type: ignore` non justifié | 🚫 | Typer correctement |

### 🔧 Commandes de vérification

```bash
# Vérifier le format
make format-check          # Dry-run
make format                # Corrige

# Vérifier le lint
make lint-check            # Dry-run
make lint                  # Corrige

# Vérifier les types
make type-check

# Vérifier la sécurité
make security

# Tout en une fois
make check
```

---

## 🌐 Ajouter un nouveau site

C'est **la contribution la plus précieuse** ! Voici le processus complet.

### 📋 Étape 1 : Vérifier que le site n'existe pas

```bash
# Chercher dans les sites existants
grep -ri "monsite" src/nexusdl/core/registry/sites.yaml
grep -ri "monsite" src/nexusdl/parsers/

# Chercher dans les issues GitHub
# https://github.com/NEXUS-QUANTUM/nexusdl/issues?q=monsite
```

### 🔍 Étape 2 : Analyser le site

Ouvrez le site dans votre navigateur et identifiez :

| Question | Outil |
|----------|-------|
| Le site utilise-t-il Cloudflare ? | DevTools → Network → `cf_clearance` |
| Le contenu est-il rendu en JS ? | Voir si le HTML est vide dans `curl` |
| Y a-t-il une API REST ? | DevTools → Network → XHR |
| Quel template utilise-t-il ? | Classes CSS : `madara`, `mangathemesia`, `foolslide` |
| Y a-t-il des protections ? | 403, captcha, rate limit |

```bash
# Test rapide en CLI
curl -I https://mon-site.com
curl -s https://mon-site.com/manga/one-piece | head -100
```

### 🎯 Étape 3 : Choisir la stratégie

| Situation | Approche | Fichier de référence |
|-----------|----------|----------------------|
| Site **Madara** (WordPress) | Hériter de `MadaraMixin` | `src/nexusdl/parsers/mixins/wordpress_madara.py` |
| Site **MangaThemesia** | Hériter de `MangaThemesiaMixin` | `src/nexusdl/parsers/mixins/mangathemesia.py` |
| Site **FoolSlide** | Hériter de `FoolSlideMixin` | `src/nexusdl/parsers/mixins/foolslide.py` |
| Site avec **API REST** | Hériter de `ApiBasedMixin` | `src/nexusdl/parsers/mixins/api_based.py` |
| Site avec **Cloudflare** | Ajouter `CloudflareMixin` | `src/nexusdl/parsers/mixins/cloudflare.py` |
| Site **custom** | Implémenter `BaseParser` | `src/nexusdl/parsers/base.py` |

### 📝 Étape 4 : Générer le squelette

```bash
# Génère automatiquement le fichier + l'entrée sites.yaml
make new-parser SITE=monsite LANG=fr
```

Cela crée :
- `src/nexusdl/parsers/fr/monsite.py`
- Une entrée dans `sites.yaml`
- Un `nexus.dl` à jour

### 💻 Étape 5 : Implémenter le parser

#### Exemple complet : site Madara (le plus simple)

```python
# SPDX-FileCopyrightText: 2026 NEXUS-QUANTUM <nexus.quantum@protonmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser pour mon-site.fr (thème WordPress Madara)."""
from __future__ import annotations

from typing import ClassVar

from nexusdl.core.models import Language, Page, Chapter
from nexusdl.parsers.base import BaseParser
from nexusdl.parsers.mixins import MadaraMixin


class MonSiteParser(MadaraMixin, BaseParser):
    """Parser pour mon-site.fr.

    Ce site utilise le thème WordPress Madara, donc la plupart
    des méthodes sont fournies par MadaraMixin. Seules les
    spécificités (sélecteurs custom, protections) doivent être
    redéfinies.
    """

    site_id: ClassVar[str] = "mon_site"
    language: ClassVar[Language] = Language.FR
    adult: ClassVar[bool] = False

    # URL de base (utilisée pour les URL relatives)
    base_url: ClassVar[str] = "https://mon-site.fr"

    # Sélecteurs CSS spécifiques si différents de Madara par défaut
    SELECTORS: ClassVar[dict[str, str]] = {
        "search_results": ".c-tabs-item__content",
        "manga_title": "h1",
        "chapter_list": ".wp-manga-chapter a",
        "page_image": ".reading-content img",
    }

    # Rate limiting spécifique
    rate_limit: ClassVar[float] = 2.0        # req/sec
    max_concurrent: ClassVar[int] = 4        # downloads parallèles

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        """Récupère les URLs des pages d'un chapitre.

        Override de MadaraMixin si les images utilisent un
        pattern différent (ex: `data-src` au lieu de `src`).
        """
        html = await self.session.get_html(chapter.url)
        soup = self.parse_html(html)

        pages: list[Page] = []
        for idx, img in enumerate(soup.select(self.SELECTORS["page_image"])):
            # Madara utilise souvent data-src pour lazy loading
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

#### Exemple : site custom (from scratch)

```python
# SPDX-FileCopyrightText: 2026 NEXUS-QUANTUM <nexus.quantum@protonmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser pour custom-site.com (architecture propriétaire)."""
from __future__ import annotations

from typing import ClassVar

from nexusdl.core.exceptions import ParseError
from nexusdl.core.models import Chapter, Language, Manga, Page, SearchResult
from nexusdl.parsers.base import BaseParser


class CustomSiteParser(BaseParser):
    """Parser pour custom-site.com.

    Ce site n'utilise aucun template connu, donc toutes les
    méthodes doivent être implémentées.
    """

    site_id: ClassVar[str] = "custom_site"
    language: ClassVar[Language] = Language.EN
    adult: ClassVar[bool] = False
    base_url: ClassVar[str] = "https://custom-site.com"
    rate_limit: ClassVar[float] = 1.0
    max_concurrent: ClassVar[int] = 2

    async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        """Recherche des mangas par titre."""
        url = f"{self.base_url}/search"
        response = await self.session.get(
            url, params={"q": query, "page": page}
        )
        soup = self.parse_html(response.text)

        results: list[SearchResult] = []
        for card in soup.select(".manga-card"):
            link = card.select_one("a.manga-link")
            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get("href")
            cover = card.select_one("img.cover")

            results.append(
                SearchResult(
                    title=title,
                    url=self.normalize_url(href),
                    cover_url=self.normalize_url(cover["src"]) if cover else None,
                    site=self.site_id,
                    language=self.language,
                )
            )

        return results

    async def get_manga(self, url_or_id: str) -> Manga:
        """Récupère les métadonnées complètes d'un manga."""
        url = self.normalize_url(url_or_id) if not url_or_id.startswith("http") else url_or_id
        html = await self.session.get_html(url)
        soup = self.parse_html(html)

        title_el = soup.select_one("h1.manga-title")
        if not title_el:
            raise ParseError(f"Titre introuvable sur {url}")

        return Manga(
            id=self._generate_id(url),
            source_id=url.rsplit("/", 1)[-1],
            site=self.site_id,
            title=title_el.get_text(strip=True),
            url=url,
            language=self.language,
            description=self._extract_description(soup),
            author=self._extract_author(soup),
            genres=self._extract_genres(soup),
            cover_url=self._extract_cover(soup),
            chapters=[],  # rempli par get_chapters()
        )

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        """Récupère la liste des chapitres d'un manga."""
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
        """Récupère les URLs des pages d'un chapitre."""
        html = await self.session.get_html(chapter.url)
        soup = self.parse_html(html)

        # Certains sites stockent les URLs dans un script JS
        script = soup.select_one("script:contains('var pages')")
        if script:
            return self._parse_pages_from_js(script.string or "")

        # Sinon, extraction depuis le DOM
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

### 📝 Étape 6 : Enregistrer dans `sites.yaml`

```yaml
# src/nexusdl/core/registry/sites.yaml
sites:
  mon_site:
    name: "Mon Site"
    domains:
      - "https://mon-site.fr"
    parser_class: "nexusdl.parsers.fr.monsite:MonSiteParser"
    language: "fr"
    adult: false
    capabilities:
      supports_search: true
      supports_manga_info: true
      supports_chapters: true
      supports_pages: true
      supports_download: true
      requires_auth: false
      requires_cloudflare_bypass: false
      max_concurrent_downloads: 4
      rate_limit_per_second: 2.0
    default_headers:
      Referer: "https://mon-site.fr/"
    cookies_required: []
    notes: "Thème WordPress Madara"
```

### 🧪 Étape 7 : Tester

```bash
# Test rapide du parser
make test-site SITE=mon_site

# Vérifier dans la liste
make list-sites

# Test interactif via CLI
make run ARGS="search 'one piece' --sites mon_site"
```

### ✅ Étape 8 : Ajouter des tests

Créez `tests/integration/test_parsers/test_mon_site.py` :

```python
# SPDX-FileCopyrightText: 2026 NEXUS-QUANTUM
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests d'intégration pour le parser MonSite."""
from __future__ import annotations

import pytest

from nexusdl.parsers.fr.monsite import MonSiteParser


@pytest.fixture
def parser() -> MonSiteParser:
    """Fixture du parser MonSite."""
    return MonSiteParser(...)


class TestMonSiteSearch:
    async def test_search_returns_results(self, parser: MonSiteParser) -> None:
        """La recherche doit retourner au moins un résultat."""
        results = await parser.search("one piece")
        assert len(results) > 0
        assert all(r.site == "mon_site" for r in results)

    async def test_search_empty_query(self, parser: MonSiteParser) -> None:
        """Une recherche vide ne doit pas crash."""
        results = await parser.search("")
        assert isinstance(results, list)


class TestMonSiteManga:
    async def test_get_manga_parses_metadata(self, parser: MonSiteParser) -> None:
        """Les métadonnées doivent être correctement extraites."""
        manga = await parser.get_manga("https://mon-site.fr/manga/one-piece")
        assert manga.title
        assert manga.url
        assert manga.site == "mon_site"
```

### 🎁 Étape 9 : Commit + PR

```bash
git checkout -b parser/mon-site
git add .
git commit -s -m "feat(parsers/fr): add mon-site support

- Parser basé sur MadaraMixin
- Support complet (search, manga, chapters, pages)
- 5 tests d'intégration
- Enregistré dans sites.yaml

Closes #789"
git push origin parser/mon-site
```

### 📋 Checklist parser

- [ ] Fichier créé au bon endroit (`parsers/<lang>/<site>.py`)
- [ ] Hérite de `BaseParser` (+ mixins si applicable)
- [ ] `site_id`, `language`, `adult` définis
- [ ] `search()`, `get_manga()`, `get_chapters()`, `get_pages()` implémentés
- [ ] Enregistré dans `sites.yaml`
- [ ] Tests d'intégration ajoutés
- [ ] `nexus.dl` du dossier mis à jour
- [ ] Docstring complète (Google-style)
- [ ] SPDX headers en tête
- [ ] Rate limit raisonnable (2 req/s par défaut)
- [ ] Gestion Cloudflare si nécessaire (`CloudflareMixin`)
- [ ] `make check` passe
- [ ] `make test` passe

---

## 🔌 Développer un plugin

Les plugins permettent d'**étendre NexusDL** sans modifier le core.

### 📁 Structure d'un plugin

```
mon-plugin/
├── __init__.py
├── plugin.py                # Logique du plugin
├── nexus.plugin.yaml        # Manifeste du plugin
├── requirements.txt         # Dépendances (optionnel)
├── README.md
└── nexus.dl
```

### 📝 `nexus.plugin.yaml`

```yaml
name: "mon-plugin"
version: "1.0.0"
author: "Votre Nom"
email: "vous@example.com"
description: "Plugin d'export vers mon service"
license: "GPL-3.0-or-later"
homepage: "https://github.com/vous/mon-plugin"

# Version minimale de NexusDL requise
nexusdl_requires: ">=1.0.0"

# Point d'entrée
entrypoint: "plugin:MonPlugin"

# Hooks utilisés
hooks:
  - on_download_complete
  - on_library_scan

# Permissions demandées (sécurité)
permissions:
  - network
  - filesystem_read
  - filesystem_write
```

### 💻 `plugin.py`

```python
# SPDX-FileCopyrightText: 2026 Votre Nom
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin d'export vers mon service."""
from __future__ import annotations

from pathlib import Path

from nexusdl.plugins.api import BasePlugin, hook
from nexusdl.core.models import DownloadResult


class MonPlugin(BasePlugin):
    """Plugin qui upload les CBZ vers mon service cloud."""

    name = "mon-plugin"
    version = "1.0.0"

    async def on_load(self) -> None:
        """Appelé au chargement du plugin."""
        self.logger.info("Plugin chargé")

    async def on_unload(self) -> None:
        """Appelé au déchargement."""
        self.logger.info("Plugin déchargé")

    @hook("on_download_complete")
    async def upload_to_cloud(self, result: DownloadResult) -> None:
        """Upload le fichier téléchargé vers mon service."""
        if not result.success:
            return

        self.logger.info("Upload de {} vers mon cloud", result.output_path)
        # ... logique d'upload
```

### 🧪 Test d'un plugin

```python
from nexusdl.plugins.loader import PluginLoader

loader = PluginLoader()
plugin = await loader.load("./mon-plugin")
await plugin.on_load()
```

### 📋 Checklist plugin

- [ ] `nexus.plugin.yaml` complet et valide
- [ ] Hérite de `BasePlugin`
- [ ] `on_load` et `on_unload` implémentés
- [ ] Hooks décorés avec `@hook("...")`
- [ ] Permissions déclarées (principe du moindre privilège)
- [ ] Tests unitaires
- [ ] README avec installation et usage
- [ ] Licence compatible GPL-3.0

---

## 🧪 Tests

Les tests sont **obligatoires** pour toute PR touchant au code.

### 📊 Objectifs de couverture

| Composant | Couverture minimale |
|-----------|:-------------------:|
| `core/` | **90 %** |
| `parsers/` | **80 %** |
| `interfaces/` | **70 %** |
| **Global** | **85 %** |

### 📁 Structure des tests

```
tests/
├── conftest.py              # Fixtures partagées
├── unit/                    # Tests unitaires (rapides, isolés)
│   ├── test_core/
│   ├── test_packaging/
│   └── test_utils/
├── integration/             # Tests d'intégration (mock réseau)
│   ├── test_parsers/
│   └── test_downloader/
├── e2e/                     # Tests end-to-end (réseau réel)
│   └── test_full_workflow.py
└── fixtures/
    ├── html_samples/        # HTML de test
    └── api_responses/       # JSON mock
```

### ✍️ Écrire un test

```python
# SPDX-FileCopyrightText: 2026 NEXUS-QUANTUM
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour le module nexusdl.core.utils.text."""
from __future__ import annotations

import pytest

from nexusdl.core.utils.text import clean_filename, slugify


class TestCleanFilename:
    """Tests de clean_filename()."""

    def test_removes_forbidden_chars(self) -> None:
        """Les caractères interdits doivent être supprimés."""
        assert clean_filename('foo/bar:baz*qux') == "foo_bar_baz_qux"

    def test_truncates_long_names(self) -> None:
        """Les noms trop longs sont tronqués."""
        long_name = "a" * 500
        result = clean_filename(long_name, max_length=255)
        assert len(result) <= 255

    def test_preserves_unicode(self) -> None:
        """Les caractères Unicode sont préservés."""
        assert clean_filename("ワンピース") == "ワンピース"

    @pytest.mark.parametrize(
        ("input_", "expected"),
        [
            ("One Piece", "one-piece"),
            ("Attack on Titan", "attack-on-titan"),
            ("L'Attaque des Titans", "l-attaque-des-titans"),
        ],
    )
    def test_slugify(self, input_: str, expected: str) -> None:
        """slugify() doit produire un slug valide."""
        assert slugify(input_) == expected
```

### 🏃 Exécuter les tests

```bash
# Tous les tests
make test

# Tests unitaires uniquement
make test-unit

# Tests d'intégration
make test-integration

# Tests E2E (réseau réel)
make test-e2e

# Tests d'un module spécifique
make test-module MODULE=tests/unit/test_utils.py

# Avec couverture
make test-cov
make coverage-open

# En parallèle (plus rapide)
make test-fast

# En watch mode
make test-watch
```

### 🎭 Fixtures partagées (`conftest.py`)

```python
"""Fixtures pytest partagées."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
import respx

from nexusdl.core.session import HttpSession
from nexusdl.core.registry import SiteRegistry


@pytest.fixture
def sample_html() -> str:
    """HTML d'exemple pour les tests."""
    return Path("tests/fixtures/html_samples/manga_page.html").read_text()


@pytest_asyncio.fixture
async def http_session() -> AsyncIterator[HttpSession]:
    """Session HTTP mockée."""
    with respx.mock:
        async with HttpSession(...) as session:
            yield session


@pytest.fixture
def registry() -> SiteRegistry:
    """Registre de sites pour les tests."""
    return SiteRegistry(config_path=Path("src/nexusdl/core/registry/sites.yaml"))
```

### 🏷️ Marqueurs pytest

| Marqueur | Usage |
|----------|-------|
| `@pytest.mark.unit` | Test unitaire rapide |
| `@pytest.mark.integration` | Test avec I/O mockée |
| `@pytest.mark.e2e` | Test avec réseau réel |
| `@pytest.mark.slow` | Test > 5s |
| `@pytest.mark.network` | Nécessite Internet |
| `@pytest.mark.adult` | Test sur site adulte |
| `@pytest.mark.cloudflare` | Nécessite bypass CF |
| `@pytest.mark.windows` / `linux` / `macos` | OS-spécifique |

### ✅ Checklist tests

- [ ] Chaque fonction publique a au moins 1 test
- [ ] Cas nominaux + cas limites testés
- [ ] Erreurs attendues testées (`pytest.raises`)
- [ ] Mocks pour les I/O externes (respx, mocker)
- [ ] Fixtures pour la réutilisation
- [ ] Marqueurs appropriés
- [ ] Couverture ≥ 85 % (`make test-cov`)
- [ ] Tests rapides (< 5s au total pour `unit`)

---

## 📚 Documentation

La documentation est **aussi importante** que le code.

### 📁 Structure

```
docs/
├── source/
│   ├── index.rst
│   ├── installation/
│   ├── usage/
│   ├── development/
│   ├── api/
│   └── conf.py
```

### ✍️ Écrire de la doc

Utilisez **Markdown** (via MyST) ou **reStructuredText** :

````markdown
# Titre

## Section

Texte normal avec **gras**, *italique*, `code inline`.

```python
# Bloc de code
def example():
    pass
```

::: note
Ceci est une note importante.
:::

::: warning
Ceci est un avertissement.
:::
````

### 🔧 Build local

```bash
# Sphinx
make docs

# MkDocs avec hot-reload
make docs-serve
# → http://localhost:8000

# Ouvre dans le navigateur
make docs-open
```

### ✅ Checklist doc

- [ ] Docstrings Google sur toutes les fonctions publiques
- [ ] Exemples d'utilisation dans les docstrings
- [ ] README à jour si la fonctionnalité change
- [ ] Doc dans `docs/source/` si nouvelle fonctionnalité majeure
- [ ] CHANGELOG.md mis à jour
- [ ] `nexus.dl` à jour

---

## 🌍 Traductions

NexusDL supporte **français, anglais, espagnol, allemand**.

### 📁 Fichiers de traduction

```
src/nexusdl/data/translations/
├── fr.json
├── en.json
├── es.json
└── de.json
```

### ✍️ Ajouter une langue

```bash
# Copier le template
cp src/nexusdl/data/translations/en.json src/nexusdl/data/translations/it.json
# Éditer it.json
```

### 📝 Format d'un fichier

```json
{
  "app": {
    "name": "NexusDL",
    "description": "Téléchargeur universel de mangas"
  },
  "menu": {
    "search": "Rechercher",
    "download": "Télécharger",
    "library": "Bibliothèque",
    "settings": "Paramètres"
  },
  "errors": {
    "not_found": "Ressource introuvable",
    "network_error": "Erreur réseau : {url}"
  }
}
```

### ✅ Checklist traduction

- [ ] Fichier JSON valide (`python -m json.tool fr.json`)
- [ ] Toutes les clés présentes (comparer avec `en.json`)
- [ ] Pas de clés orphelines
- [ ] Pluriels gérés (`one` / `other`)
- [ ] Variables `{var}` préservées
- [ ] Test : `make run ARGS="--lang it"`

---

## 📄 Système `nexus.dl`

Chaque dossier du projet contient un fichier **`nexus.dl`** qui le documente.

### 📋 Contenu

```yaml
folder:
  path: "src/nexusdl/core"
  name: "core"
  purpose: "Cœur métier, indépendant de toute interface"
  layer: "core"

contents:
  files:
    - name: "config.py"
      purpose: "Chargement de la configuration"

dependencies:
  allowed: ["stdlib", "pydantic", "httpx"]
  forbidden: ["nexusdl.interfaces"]

rules:
  - "Aucun import depuis interfaces/"
```

### 🔧 Maintenance

```bash
# Valider tous les nexus.dl
make validate-manifests

# Régénérer après ajout/suppression de fichiers
make generate-manifests
```

### ✅ Règle importante

**À chaque fois que vous ajoutez, renommez ou supprimez un fichier, mettez à jour le `nexus.dl` du dossier correspondant.**

---

## 🚀 Processus de Pull Request

### 📝 Avant d'ouvrir une PR

```bash
# 1. Synchroniser avec upstream
git checkout main
git pull upstream main

# 2. Rebaser votre branche
git checkout feature/ma-feature
git rebase main

# 3. Toutes les vérifications
make check        # Lint + type + sécurité
make test         # Tests
make test-cov     # Couverture
make docs         # Doc build

# 4. Résoudre les conflits
# ...
```

### 📋 Checklist avant PR

- [ ] J'ai lu ce guide en entier
- [ ] Ma branche est à jour avec `main`
- [ ] Les commits suivent Conventional Commits
- [ ] Chaque commit est signé DCO (`-s`)
- [ ] `make check` passe ✅
- [ ] `make test` passe ✅
- [ ] La couverture n'a pas baissé
- [ ] Les nouveaux fichiers ont des tests
- [ ] Les docstrings sont à jour
- [ ] Le `CHANGELOG.md` est mis à jour (si feature/fix)
- [ ] Les `nexus.dl` sont à jour
- [ ] La doc est à jour
- [ ] Pas de secret commité
- [ ] Pas de fichier inutile (`.DS_Store`, etc.)
- [ ] La PR cible bien `main` (ou `develop` si demandé)

### 📤 Ouvrir la PR

Utilisez le **template de PR** fourni :

```markdown
## 📝 Description

Brève description de ce que fait cette PR.

## 🎯 Type de changement

- [ ] 🐛 Bug fix
- [ ] ✨ Nouvelle fonctionnalité
- [ ] 🌐 Nouveau parser
- [ ] 🔌 Nouveau plugin
- [ ] 📝 Documentation
- [ ] 🌍 Traduction
- [ ] 🎨 UI/UX
- [ ] ⚡ Performance
- [ ] ♻️ Refactoring
- [ ] 🔒 Sécurité

## 🔗 Issue(s) liée(s)

Closes #123
Fixes #456

## 🧪 Comment tester

1. `make install`
2. `make run ARGS="..."`
3. Vérifier que...

## 📸 Screenshots (si UI)

[Avant / Après]

## ✅ Checklist

- [ ] Tests passent
- [ ] Lint passe
- [ ] Types passent
- [ ] Doc mise à jour
- [ ] CHANGELOG mis à jour
- [ ] nexus.dl mis à jour

## 📎 Contexte additionnel

Tout ce qui peut aider le reviewer.
```

### 🏷️ Titre de PR

Format : `<type>(<scope>): <description>`

Exemples :
- `feat(parsers): add mangafire support`
- `fix(session): handle cloudflare challenge`
- `docs: improve installation guide`

---

## 👀 Processus de review

### 🎯 Ce que nous recherchons

| Critère | Poids |
|---------|:-----:|
| **Correctness** (le code fait ce qu'il dit) | 🔥🔥🔥 |
| **Tests** (couverture, cas limites) | 🔥🔥🔥 |
| **Sécurité** (pas de faille) | 🔥🔥🔥 |
| **Performance** (pas de régression) | 🔥🔥 |
| **Lisibilité** (nommage, structure) | 🔥🔥 |
| **Documentation** (docstrings, README) | 🔥🔥 |
| **Compatibilité** (async, Python 3.12+) | 🔥 |
| **Style** (ruff, mypy) | 🔥 |

### ⏱️ Délais de review

| Type de PR | Délai cible |
|------------|:-----------:|
| 🔥 Hotfix sécurité | < 24h |
| 🐛 Bug fix simple | < 3 jours |
| ✨ Feature | < 7 jours |
| 🌐 Nouveau parser | < 5 jours |
| 📝 Documentation | < 2 jours |
| 🌍 Traduction | < 3 jours |
| ♻️ Refactoring | < 7 jours |

### 🔄 Cycle de review

1. **Ouverture PR** → CI se lance automatiquement
2. **CI verte** → un mainteneur est assigné
3. **Review** → commentaires, suggestions
4. **Révisions** → vous répondez, ajustez
5. **Approbation** → `LGTM` (Looks Good To Me)
6. **Merge** → squash & merge (ou rebase selon config)
7. **Célébration** 🎉

### 💬 Commentaires de review

- **Nitpick** : détail cosmétique (pas bloquant)
- **Suggestion** : amélioration possible (à discuter)
- **Issue** : problème à corriger (bloquant)
- **Question** : demande de clarification
- **Praise** : félicitation 🎉

**Exemple de réponse** :
> Merci pour la review ! J'ai corrigé le point sur `#L42`, et j'ai ajouté un test pour couvrir le cas `None`. Pour la suggestion sur l'`httpx` client, je préfère garder la session partagée car... (raison). Dis-moi si tu préfères autrement.

### 🚫 Ce qui bloque une PR

- ❌ CI rouge (lint, type, tests)
- ❌ Conflits avec `main`
- ❌ Couverture en baisse
- ❌ Tests manquants pour du nouveau code
- ❌ Breaking change sans migration
- ❌ Secret commité
- ❌ Violation du Code de Conduite
- ❌ Refus de discuter les reviews

---

## 🐛 Signaler un bug

### ✅ Avant de signaler

1. **Vérifiez** que ce n'est pas déjà signalé : [Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues)
2. **Mettez à jour** : `make update && make install`
3. **Reproduisez** : avec la dernière version
4. **Testez** : en désactivant les plugins/customisations

### 📝 Template de bug report

```markdown
## 🐛 Description

Description claire et concise du bug.

## 🔄 Étapes de reproduction

1. `nexusdl search "..."`
2. Cliquer sur...
3. Voir l'erreur

## ✅ Comportement attendu

Ce qui devrait se passer.

## ❌ Comportement observé

Ce qui se passe réellement.

## 📸 Screenshots / Logs

```
[Coller les logs ici — utilisez `--verbose`]
```

## 🌍 Environnement

- **NexusDL** : 1.0.0
- **Python** : 3.12.7
- **OS** : Ubuntu 24.04 / Windows 11 / macOS 14
- **Interface** : CLI / Web / GUI
- **Site concerné** : mangadex / sushiscan / ...
- **Format de sortie** : cbz / zip / pdf

## 🔍 Contexte additionnel

- Fréquence : toujours / parfois / une fois
- Dernière version qui marchait : 1.0.0
- Configuration particulière : ...

## 📋 Checklist

- [ ] J'utilise la dernière version
- [ ] J'ai cherché dans les issues existantes
- [ ] J'ai testé avec les plugins désactivés
- [ ] J'ai inclus les logs (`--verbose`)
```

### 🏷️ Labels automatiques

Les mainteneurs ajouteront :
- `bug` — Bug confirmé
- `needs-triage` — À investiguer
- `priority:high` / `medium` / `low`
- `status:confirmed` / `wontfix` / `duplicate`

---

## 💡 Proposer une fonctionnalité

### 📝 Template de feature request

```markdown
## 💡 Problème à résoudre

Quel problème cette fonctionnalité résout-elle ?

## ✨ Solution proposée

Description de la solution.

## 🔄 Alternatives envisagées

Autres approches possibles.

## 📸 Maquettes / Exemples

Screenshots, mockups, liens vers des projets similaires.

## 🎯 Cas d'usage

1. En tant que [type d'utilisateur], je veux [action] pour [bénéfice].
2. ...

## 📋 Checklist

- [ ] J'ai cherché dans les issues existantes
- [ ] J'ai cherché dans les discussions
- [ ] Ce n'est pas déjà possible avec un plugin
- [ ] Je suis prêt à aider à l'implémenter (si applicable)
```

### 🗳️ Processus de décision

1. **Discussion** ouverte (7 jours minimum)
2. **Vote** de la communauté (👍 sur l'issue)
3. **Décision** des mainteneurs
4. **Roadmap** mise à jour si acceptée

---

## 🔒 Signaler une vulnérabilité

**NE PAS ouvrir d'issue publique pour une vulnérabilité.**

Consultez **[SECURITY.md](SECURITY.md)** pour le processus complet.

**Résumé** :
- Email : `security@nexus-quantum.dev`
- GitHub : [Security Advisories](https://github.com/NEXUS-QUANTUM/nexusdl/security/advisories/new)
- Délai de réponse : 48h
- Safe harbor : ✅

---

## 🏆 Reconnaissance

Tous les contributeurs sont reconnus dans :

- **[CONTRIBUTORS.md](CONTRIBUTORS.md)** — Liste complète
- **[CHANGELOG.md](CHANGELOG.md)** — Par version
- **[SECURITY_HALL_OF_FAME.md](SECURITY_HALL_OF_FAME.md)** — Chercheurs en sécurité
- **Release notes** — À chaque release
- **README.md** — Top contributeurs (si applicable)

### 🎖️ Niveaux de contribution

| Niveau | Critère | Avantages |
|--------|---------|-----------|
| 🌱 **Contributor** | 1 PR mergée | Crédit dans CONTRIBUTORS.md |
| 🌿 **Regular** | 5 PR mergées | Rôle Discord, mention README |
| 🌳 **Core** | 20 PR mergées + review | Accès écriture, décisions |
| 🏆 **Maintainer** | Décision de l'équipe | Tous les droits |

### 💖 Sponsors

Si vous souhaitez soutenir financièrement :
- **[GitHub Sponsors](https://github.com/sponsors/NEXUS-QUANTUM)**
- **[Ko-fi](https://ko-fi.com/NEXUS-QUANTUM)**
- **[Patreon](https://patreon.com/NEXUS-QUANTUM)**

---

## ❓ Questions ?

| Canal | Usage | Lien |
|-------|-------|------|
| 💬 **Discord** | Questions rapides, entraide | [Rejoindre](https://discord.gg/NEXUS-QUANTUM) |
| 💡 **GitHub Discussions** | Débats, idées, Q&A | [Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions) |
| 🐛 **GitHub Issues** | Bugs, features | [Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues) |
| 📧 **Email** | Contact privé | `nexus.quantum@protonmail.com` |
| 🔒 **Sécurité** | Vulnérabilités | `security@nexus-quantum.dev` |
| 📜 **Conduite** | Signalements | `conduct@nexus-quantum.dev` |

---

## 🙏 Merci

**Merci d'avoir pris le temps de lire ce guide.**

Chaque contribution — même la plus petite — rend NexusDL meilleur. Que vous corrigiez un typo, ajoutiez un parser, traduisiez l'interface ou signaliez un bug, vous faites partie de cette aventure.

**Bienvenue dans la communauté NexusDL !** 🌌

---

<div align="center">

**Prêt à contribuer ?**

[![Fork](https://img.shields.io/badge/Fork-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM/nexusdl/fork)
[![Issues](https://img.shields.io/badge/Issues-Open-ff6b6b?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM/nexusdl/issues)
[![Discussions](https://img.shields.io/badge/Discussions-Join-4baaaa?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)

---

**Fait avec ❤️ par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM) et la communauté.**

*« Seul on va plus vite, ensemble on va plus loin. »*

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Twitter](https://img.shields.io/badge/Twitter-@NEXUS--QUANTUM-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)
[![Email](https://img.shields.io/badge/Email-nexus.quantum@protonmail.com-8B89CC?style=for-the-badge&logo=protonmail&logoColor=white)](mailto:nexus.quantum@protonmail.com)

</div>
```

