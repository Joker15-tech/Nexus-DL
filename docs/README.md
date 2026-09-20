
# 📚 NexusDL — Documentation

<div align="center">

**Bienvenue dans la documentation officielle de NexusDL.**

*Guide complet pour utilisateurs, administrateurs et développeurs.*

[![Docs](https://img.shields.io/badge/Docs-Sphinx-blue?style=for-the-badge&logo=sphinx&logoColor=white)](https://docs.nexus-quantum.dev/nexusdl)
[![MkDocs](https://img.shields.io/badge/MkDocs-Material-green?style=for-the-badge&logo=markdown&logoColor=white)](https://docs.nexus-quantum.dev/nexusdl)
[![License](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey?style=for-the-badge)](https://creativecommons.org/licenses/by/4.0/)

[🚀 Quick Start](#-quick-start) •
[📖 Structure](#-structure) •
[✍️ Contribuer](#-contribuer) •
[🔨 Build](#-build)

</div>

---

## 📖 À propos de ce dossier

Ce dossier contient **toute la documentation** de NexusDL :

- 📘 **Guides utilisateur** : Installation, utilisation, configuration
- 📗 **Guides développeur** : Architecture, ajout de sites, plugins
- 📙 **Références techniques** : API REST, API Python, schémas
- 📕 **Documentation projet** : Contributeurs, sécurité, changelog

La documentation est **auto-générée** depuis les docstrings Python + des fichiers Markdown/rST manuels, puis publiée sur **[docs.nexus-quantum.dev](https://docs.nexus-quantum.dev/nexusdl)**.

---

## 🚀 Quick Start

### 📖 Pour les utilisateurs

Vous voulez **utiliser** NexusDL ? Commencez ici :

1. **[Installation](source/installation/)** → Installer NexusDL
2. **[Utilisation CLI](source/usage/cli.md)** → Commandes principales
3. **[Utilisation Web](source/usage/web.md)** → Interface web
4. **[Configuration](source/usage/configuration.md)** → Configurer NexusDL
5. **[FAQ](../README.md#-faq)** → Questions fréquentes

### 👨‍💻 Pour les développeurs

Vous voulez **contribuer** à NexusDL ? Commencez ici :

1. **[Contributing](../CONTRIBUTING.md)** → Guide de contribution
2. **[Architecture](source/development/architecture.md)** → Vue d'ensemble
3. **[Ajouter un site](source/development/adding_sites.md)** → Créer un parser
4. **[Plugins](source/development/plugins.md)** → Développer un plugin
5. **[API Reference](API.md)** → API REST + Python

### 🔍 Pour les curieux

Vous voulez **comprendre** NexusDL ? Commencez ici :

1. **[CHANGELOG_DEV](CHANGELOG_DEV.md)** → Journal de développement
2. **[SECURITY](../SECURITY.md)** → Politique de sécurité
3. **[CODE_OF_CONDUCT](../CODE_OF_CONDUCT.md)** → Code de conduite
4. **[Discussion](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)** → Poser une question

---

## 📁 Structure

```
docs/
├── README.md                    # 👈 Ce fichier
├── API.md                       # Référence API (REST + Python)
├── CHANGELOG_DEV.md             # Journal de développement
│
├── source/                      # 📖 Sources Sphinx/MkDocs
│   ├── index.rst                # Page d'accueil de la doc
│   ├── conf.py                  # Configuration Sphinx
│   ├── Makefile                 # Build Sphinx
│   ├── make.bat                 # Build Sphinx (Windows)
│   │
│   ├── _static/                 # Fichiers statiques
│   │   ├── custom.css           # CSS custom
│   │   ├── logo.png             # Logo
│   │   └── nexus.dl             # Manifeste
│   │
│   ├── _templates/              # Templates Sphinx
│   │   ├── layout.html          # Template principal
│   │   └── nexus.dl             # Manifeste
│   │
│   ├── installation/            # 📦 Guides d'installation
│   │   ├── windows.md           # Windows
│   │   ├── linux.md             # Linux
│   │   ├── macos.md             # macOS
│   │   ├── docker.md            # Docker
│   │   └── nexus.dl             # Manifeste
│   │
│   ├── usage/                   # 🎯 Guides d'utilisation
│   │   ├── cli.md               # Interface CLI
│   │   ├── web.md               # Interface Web
│   │   ├── gui.md               # Interface Desktop
│   │   ├── configuration.md     # Configuration complète
│   │   ├── formats.md           # Formats de sortie
│   │   └── nexus.dl             # Manifeste
│   │
│   ├── development/             # 🛠️ Guides développeur
│   │   ├── architecture.md      # Architecture interne
│   │   ├── adding_sites.md      # Ajouter un site
│   │   ├── adding_parsers.md    # Ajouter un parser
│   │   ├── plugins.md           # Développer un plugin
│   │   ├── contributing.md      # Contribuer
│   │   └── nexus.dl             # Manifeste
│   │
│   ├── api/                     # 🔌 Référence API
│   │   ├── rest.md              # API REST
│   │   ├── websocket.md         # WebSocket
│   │   ├── python.md            # API Python
│   │   └── nexus.dl             # Manifeste
│   │
│   └── adr/                     # 🏛️ Architecture Decision Records
│       ├── 001-hexagonal.md     # ADR-001
│       ├── 002-pydantic-v2.md   # ADR-002
│       ├── 003-uv.md            # ADR-003
│       ├── 004-textual.md       # ADR-004
│       ├── 005-fastapi-nextjs.md # ADR-005
│       ├── 006-mixins.md        # ADR-006
│       └── nexus.dl             # Manifeste
│
├── _build/                      # 🏗️ Sortie de build (ignoré)
└── site/                        # 🏗️ Sortie MkDocs (ignoré)
```

---

## 📚 Contenu détaillé

### 🚀 Installation (`source/installation/`)

Guides d'installation **pas-à-pas** pour chaque plateforme :

| Fichier | Contenu |
|---------|---------|
| `windows.md` | Installation sur Windows (uv, pip, Docker) |
| `linux.md` | Installation sur Linux (Debian, Fedora, Arch) |
| `macos.md` | Installation sur macOS (Homebrew, uv) |
| `docker.md` | Déploiement Docker (CLI, Web, GUI) |

### 🎯 Usage (`source/usage/`)

Guides d'utilisation des **3 interfaces** :

| Fichier | Contenu |
|---------|---------|
| `cli.md` | Commandes CLI, options, exemples |
| `web.md` | Interface web, endpoints, authentification |
| `gui.md` | Interface desktop, raccourcis, préférences |
| `configuration.md` | Toutes les options de config |
| `formats.md` | Formats ZIP, CBZ, CBR, PDF, Folder |

### 🛠️ Development (`source/development/`)

Guides **développeur** :

| Fichier | Contenu |
|---------|---------|
| `architecture.md` | Architecture hexagonale, couches, dépendances |
| `adding_sites.md` | Ajouter un site (processus complet) |
| `adding_parsers.md` | Créer un parser (avec exemples) |
| `plugins.md` | Développer un plugin (hooks, permissions) |
| `contributing.md` | Contribuer au code (lien vers CONTRIBUTING.md) |

### 🔌 API (`source/api/`)

Référence **technique** :

| Fichier | Contenu |
|---------|---------|
| `rest.md` | Endpoints REST (avec exemples curl) |
| `websocket.md` | Événements WebSocket temps réel |
| `python.md` | API Python (import, méthodes, callbacks) |

> 💡 Voir aussi **[docs/API.md](API.md)** pour la référence complète.

### 🏛️ ADR (`source/adr/`)

**Architecture Decision Records** — décisions architecturales majeures :

| ADR | Titre | Statut |
|:---:|-------|:------:|
| [001](source/adr/001-hexagonal.md) | Architecture hexagonale | ✅ |
| [002](source/adr/002-pydantic-v2.md) | Pydantic v2 | ✅ |
| [003](source/adr/003-uv.md) | uv comme gestionnaire | ✅ |
| [004](source/adr/004-textual.md) | Textual pour la TUI | ✅ |
| [005](source/adr/005-fastapi-nextjs.md) | FastAPI + Next.js | ✅ |
| [006](source/adr/006-mixins.md) | Système de mixins | ✅ |

**Format d'un ADR** :

```markdown
# ADR-XXX : Titre

**Date** : AAAA-MM-JJ
**Statut** : ✅ Accepté / ❌ Rejeté / ⏳ En cours

## Contexte
Pourquoi cette décision est nécessaire.

## Décision
Ce qui a été décidé.

## Conséquences
- ✅ Avantages
- ⚠️ Inconvénients
- 🔄 Alternatives rejetées
```

---

## ✍️ Contribuer

### 📝 Règles de rédaction

| Règle | Détail |
|-------|--------|
| **Langue** | Markdown (via MyST) ou reStructuredText |
| **Encodage** | UTF-8 sans BOM |
| **Line length** | 100 caractères max |
| **Fins de ligne** | LF (`\n`) |
| **Titres** | `#` pour H1, `##` pour H2, etc. |
| **Liens** | Relatifs pour interne, absolus pour externe |
| **Images** | Dans `_static/images/` ou `assets/` |
| **Code** | Blocs avec langage (````python`) |
| **Émojis** | Utilisés dans les titres de sections |

### 🎨 Style d'écriture

**✅ À faire** :

- Utiliser **`vous`** (vouvoiement, respectueux)
- Écrire au **présent** ("NexusDL télécharge", pas "téléchargera")
- Utiliser la **voix active** ("Lancez la commande", pas "La commande doit être lancée")
- Être **concis** et **précis**
- Fournir des **exemples** concrets
- Structurer avec **titres**, **listes**, **tableaux**
- Ajouter des **encadrés** (`::: note`, `::: warning`)

**❌ À éviter** :

- ❌ Jargon non expliqué
- ❌ Fautes d'orthographe (utiliser `make docs-linkcheck`)
- ❌ Phrases trop longues (>25 mots)
- ❌ Exemples sans contexte
- ❌ Références à des versions obsolètes
- ❌ Liens cassés

### 📐 Encadrés (MyST)

MyST fournit des **admonitions** (encadrés) :

```markdown
:::{note}
Information utile.
:::

:::{tip}
Astuce pour gagner du temps.
:::

:::{warning}
Avertissement important.
:::

:::{danger}
Action dangereuse / destructive.
:::

:::{important}
Information critique.
:::

:::{seealso}
Voir aussi [le guide XYZ](chemin/vers/xyz.md).
:::
```

**Rendu** :

> **📘 Note** — Information utile.
>
> **💡 Tip** — Astuce pour gagner du temps.
>
> **⚠️ Warning** — Avertissement important.
>
> **🔥 Danger** — Action dangereuse.

### 💻 Blocs de code

Utiliser toujours la **coloration syntaxique** :

````markdown
```python
async def search(query: str) -> list[Result]:
    """Recherche des résultats."""
    ...
```

```bash
nexusdl search "one piece"
```

```yaml
sites:
  mangadex:
    enabled: true
```
````

### 📊 Tableaux

Utiliser des tableaux pour les **comparaisons** et **listes structurées** :

```markdown
| Colonne 1 | Colonne 2 | Colonne 3 |
|-----------|:---------:|----------:|
| Gauche    | Centré    | Droite    |
```

### 🔗 Références croisées

Sphinx permet des références :

```rst
.. _my-reference:

Voir :ref:`my-reference` pour plus de détails.
```

Ou avec MyST :

````markdown
Voir {ref}`my-reference` pour plus de détails.
````

### 🖼️ Images

Placer dans `_static/images/` :

```markdown
![Description](../_static/images/screenshot.png)
```

**Règles** :
- ✅ Nom explicite (`cli-search-results.png`)
- ✅ Alt text obligatoire
- ✅ Format PNG (screenshots), SVG (diagrammes)
- ✅ Taille < 500 KB (optimiser)
- ❌ Pas d'images externes (les copier localement)

### 📝 Processus de contribution

```bash
# 1. Créer une branche
git checkout -b docs/amélioration-xyz

# 2. Éditer les fichiers
vim docs/source/usage/cli.md

# 3. Tester le build local
make docs-serve

# 4. Vérifier les liens
make docs-linkcheck

# 5. Commit
git commit -s -m "docs(cli): add download examples"

# 6. PR
gh pr create --title "docs(cli): add download examples"
```

### ✅ Checklist avant PR

- [ ] Fichier(s) modifié(s) au bon endroit
- [ ] Style d'écriture respecté
- [ ] Exemples testés (si code)
- [ ] Liens vérifiés
- [ ] Pas de fautes (vérifier avec un correcteur)
- [ ] Build local passe (`make docs`)
- [ ] `nexus.dl` du dossier mis à jour (si ajout/suppression)

---

## 🔨 Build

### 📦 Prérequis

```bash
# Installer les dépendances de doc
uv sync --extra docs

# Ou
pip install -e ".[docs]"
```

### 🏗️ Build Sphinx (recommandé)

```bash
# Build HTML
make docs

# Ou directement
uv run sphinx-build -W -b html docs/source docs/_build/html

# Le résultat est dans docs/_build/html/index.html
```

**Options Sphinx** :

| Flag | Description |
|------|-------------|
| `-W` | Traite les warnings comme erreurs |
| `-b html` | Format de sortie |
| `-d <dir>` | Dossier des doctrees |
| `-j auto` | Build parallèle |
| `-a` | Rebuild complet |
| `-E` | Ne pas utiliser le cache |
| `-n` | Nitpicky (warnings stricts) |

### 🏗️ Build MkDocs (alternative)

```bash
# Build statique
make docs-mkdocs
# → site/

# Ou avec MkDocs direct
uv run mkdocs build --strict
```

### 🚀 Servir localement (hot reload)

```bash
# Avec MkDocs (hot reload natif)
make docs-serve
# → http://localhost:8000

# Avec Sphinx (nécessite sphinx-autobuild)
uv run sphinx-autobuild docs/source docs/_build/html --open-browser
```

### 🔍 Vérifier les liens

```bash
# Vérifier les liens internes et externes
make docs-linkcheck

# Ou directement
uv run sphinx-build -b linkcheck docs/source docs/_build/linkcheck
```

### 🧹 Nettoyer

```bash
# Nettoyer la doc buildée
make docs-clean

# Ou manuellement
rm -rf docs/_build/ site/ docs/source/_autosummary/
```

### 📊 Statistiques

```bash
# Nombre de fichiers de doc
find docs -name "*.md" -o -name "*.rst" | wc -l

# Nombre de mots
find docs -name "*.md" -o -name "*.rst" -exec cat {} \; | wc -w

# Taille totale
du -sh docs/
```

---

## 🎯 Bonnes pratiques

### 📝 Pour les titres

- ✅ **Un seul H1** par fichier
- ✅ **Hiérarchie logique** : H1 → H2 → H3 → H4
- ✅ **Titres courts** (< 60 caractères)
- ✅ **Titres descriptifs** ("Installation sur Windows" > "Windows")
- ✅ **Émojis en début** pour les grandes sections
- ❌ Pas de saut de niveau (H1 → H3)

### 📄 Pour les fichiers

- ✅ **Un fichier = un sujet**
- ✅ **Nom en kebab-case** : `adding-sites.md`
- ✅ **Un fichier de plus de 500 lignes** → découper
- ✅ **Toujours un titre H1**
- ✅ **Table des matières** si > 200 lignes
- ✅ **`nexus.dl`** dans chaque dossier

### 🔗 Pour les liens

- ✅ **Relatifs** pour la doc interne : `[CLI](source/usage/cli.md)`
- ✅ **Absolus** pour l'externe : `[GitHub](https://github.com/NEXUS-QUANTUM)`
- ✅ **Descriptifs** : `[Guide CLI](cli.md)` > `[cliquez ici](cli.md)`
- ✅ **Vérifiés** : `make docs-linkcheck`
- ❌ Pas de liens morts

### 💡 Pour les exemples

- ✅ **Toujours testés** (copier-coller fonctionne)
- ✅ **Commentés** si complexe
- ✅ **Sortie attendue** affichée
- ✅ **Erreurs communes** documentées
- ✅ **Alternatives** présentées

### 🌍 Pour l'i18n

- ✅ **Français** pour les docs utilisateur principales
- ✅ **Anglais** pour les termes techniques
- ✅ **Traductions** dans `src/nexusdl/data/translations/`
- ⚠️ **Ne pas traduire** : code, noms de fichiers, endpoints API

---

## 📊 Statistiques de la documentation

| Métrique | Valeur |
|----------|:------:|
| **Fichiers Markdown** | ~30 |
| **Fichiers rST** | ~10 |
| **Mots totaux** | ~50 000 |
| **Exemples de code** | ~200 |
| **ADRs** | 6 |
| **Langues** | 4 (FR, EN, ES, DE) |
| **Dernière mise à jour** | 2026-01-15 |

---

## 🛠️ Outils utilisés

| Outil | Version | Usage |
|-------|:-------:|-------|
| **Sphinx** | 8.1+ | Générateur de doc principal |
| **MkDocs** | 1.6+ | Alternative (hot reload) |
| **MyST-Parser** | 4.0+ | Markdown pour Sphinx |
| **Sphinx RTD Theme** | 3.0+ | Thème Read the Docs |
| **MkDocs Material** | 9.5+ | Thème moderne |
| **Sphinx Autodoc** | — | Génération depuis docstrings |
| **Sphinx Autodoc Typehints** | 3.0+ | Types dans la doc |
| **Sphinx Copybutton** | 0.5+ | Bouton "Copier" sur les blocs code |
| **Sphinx Design** | 0.6+ | Composants UI (cards, grids) |
| **Sphinx Mermaid** | 1.0+ | Diagrammes Mermaid |
| **myst-parser** | 4.0+ | Markdown pour Sphinx |

---

## 🎨 Thèmes

### Sphinx — Read the Docs Theme

```python
# docs/source/conf.py
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
    "titles_only": False,
}
```

### MkDocs — Material

```yaml
# mkdocs.yml
theme:
  name: material
  palette:
    - scheme: slate
      primary: deep purple
      accent: purple
      toggle:
        icon: material/brightness-4
        name: Mode clair
    - scheme: default
      primary: deep purple
      accent: purple
      toggle:
        icon: material/brightness-7
        name: Mode sombre
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.top
    - search.suggest
    - search.highlight
    - content.code.copy
```

---

## 🚀 Publication

### 📤 Déploiement automatique

La documentation est **auto-déployée** via GitHub Actions :

- **Branche `main`** → [docs.nexus-quantum.dev/nexusdl](https://docs.nexus-quantum.dev/nexusdl) (version stable)
- **Branche `develop`** → [dev.docs.nexus-quantum.dev](https://dev.docs.nexus-quantum.dev) (version dev)
- **Tags `v*`** → Version archivée (`v1.0.0.docs.nexus-quantum.dev`)

### 🌐 URLs de la documentation

| Environnement | URL |
|---------------|-----|
| **Production** | https://docs.nexus-quantum.dev/nexusdl |
| **Development** | https://dev.docs.nexus-quantum.dev |
| **Versions archivées** | https://docs.nexus-quantum.dev/nexusdl/v1.0.0/ |
| **Latest** | https://docs.nexus-quantum.dev/nexusdl/latest/ |

### 🔄 Versioning de la doc

Chaque version majeure est archivée :

- `/latest/` → Dernière version stable
- `/v1.0.0/` → Version 1.0.0
- `/v0.9.0/` → Version 0.9.0 (legacy)

---

## 🔍 Recherche

### Sphinx

```python
# docs/source/conf.py
html_search_language = "fr"
html_search_options = {
    "type": "default",
    "dict": "/chemin/vers/dictionnaire",
}
```

### MkDocs Material

```yaml
# mkdocs.yml
plugins:
  - search:
      lang:
        - fr
        - en
      separator: '[\s\-,:!=\[\]()"/]+|(?!\b)(?=[A-Z][a-z])'
```

---

## 🐛 Debug

### Warnings Sphinx

```bash
# Voir tous les warnings
uv run sphinx-build -b html docs/source docs/_build/html -v

# Traiter les warnings comme erreurs (CI)
uv run sphinx-build -W -b html docs/source docs/_build/html

# Ignorer certains warnings
# Dans conf.py :
suppress_warnings = [
    "ref.python",  # Références Python non trouvées
    "myst.header", # Headers MyST
]
```

### Problèmes courants

| Problème | Solution |
|----------|----------|
| **Import error** | Vérifier `sys.path.insert(0, ...)` dans `conf.py` |
| **Autodoc ne trouve pas les modules** | Vérifier `autodoc_mock_imports` pour les dépendances optionnelles |
| **Liens cassés** | Lancer `make docs-linkcheck` |
| **Encodage bizarre** | Vérifier UTF-8 sans BOM |
| **Build lent** | Utiliser `-j auto` pour paralléliser |
| **Cache corrompu** | `make docs-clean && make docs` |

### Configuration `conf.py`

```python
# docs/source/conf.py
import os
import sys
from pathlib import Path

# Ajouter src/ au path
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

# Project info
project = "NexusDL"
copyright = "2026, NEXUS-QUANTUM"
author = "NEXUS-QUANTUM"
release = "1.0.0"

# Extensions
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.mermaid",
]

# MyST
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
    "attrs_inline",
    "attrs_block",
]

# Autodoc
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}

# Napoleon (Google-style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Thème
html_theme = "sphinx_rtd_theme"
html_logo = "_static/logo.png"
html_favicon = "_static/favicon.ico"

# Mocks pour les dépendances optionnelles
autodoc_mock_imports = [
    "playwright",
    "customtkinter",
    "fastapi",
    "uvicorn",
]
```

---

## 📞 Support

### 💬 Questions sur la documentation

| Canal | Usage |
|-------|-------|
| 💬 **[Discord #docs](https://discord.gg/NEXUS-QUANTUM)** | Questions rapides |
| 💡 **[GitHub Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)** | Débats, idées |
| 🐛 **[GitHub Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues)** | Bugs dans la doc |
| 📧 **Email** | `docs@nexus-quantum.dev` |

### 🐛 Signaler un problème dans la doc

Utilisez le template [Bug Report](../.github/ISSUE_TEMPLATE/bug_report.md) avec :

- **Titre** : `[DOCS] Description courte`
- **Label** : `documentation`
- **Fichier concerné**
- **Lien vers la page** (si publiée)
- **Description du problème**
- **Suggestion de correction** (optionnel)

---

## 🙏 Remerciements

Merci à tous ceux qui ont contribué à cette documentation :

- 📝 **Rédacteurs** de contenu
- 🌍 **Traducteurs**
- 🎨 **Designers** (thèmes, diagrammes)
- 🐛 **Relecteurs** (fautes, liens cassés)
- 💡 **Suggestions** d'amélioration

Voir **[CONTRIBUTORS.md](../CONTRIBUTORS.md)** pour la liste complète.

---

## 📜 Licence

Cette documentation est publiée sous licence **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

[![CC BY 4.0](https://licensebuttons.net/l/by/4.0/88x31.png)](https://creativecommons.org/licenses/by/4.0/)

Vous pouvez :
- ✅ **Partager** — copier et redistribuer
- ✅ **Adapter** — remixer, transformer, construire

À condition de :
- 📝 **Attribuer** — créditer NexusDL
- 🔗 **Indiquer** les modifications

**Exceptions** : Les extraits de code sont sous **GPL-3.0-or-later** (voir [LICENSE](../LICENSE)).

---

<div align="center">

## 📚 NexusDL Documentation

**Construite avec ❤️ par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM) et la communauté.**

---

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Twitter](https://img.shields.io/badge/Twitter-@NEXUS--QUANTUM-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)
[![Docs](https://img.shields.io/badge/Docs-docs.nexus--quantum.dev-blue?style=for-the-badge)](https://docs.nexus-quantum.dev/nexusdl)

**Bonne lecture ! 📖**

</div>
```
