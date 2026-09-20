# ==============================================================================
#  NexusDL — justfile
# ==============================================================================
#  Raccourcis de développement et d'exploitation pour NexusDL.
#  Nécessite : just >= 1.36  →  https://github.com/casey/just
#
#  Usage :
#      just                  → liste les recettes disponibles
#      just <recipe>         → exécute une recette
#      just <recipe> arg     → exécute avec arguments
#      just --list           → liste détaillée
#      just --show <recipe>  → affiche une recette
# ==============================================================================

# ------------------------------------------------------------------------------
# CONFIGURATION GLOBALE
# ------------------------------------------------------------------------------

set shell := ["bash", "-euo", "pipefail", "-c"]
set dotenv-load := true
set dotenv-filename := ".env"
set dotenv-required := false
set export := true
set positional-arguments := true
set quiet := false
set allow-duplicate-recipes := false

# Dossiers principaux
src_dir       := "src/nexusdl"
tests_dir     := "tests"
scripts_dir   := "scripts"
docs_dir      := "docs"
docker_dir    := "docker"
assets_dir    := "assets"
config_dir    := "config"

# Fichiers principaux
pyproject     := "pyproject.toml"
lockfile      := "uv.lock"
readme        := "README.md"
changelog     := "CHANGELOG.md"

# Outils
PYTHON        := "python3.12"
UV            := "uv"
PYTEST        := "uv run pytest"
RUFF          := "uv run ruff"
MYPY          := "uv run mypy"
BANDIT        := "uv run bandit"
PIP_AUDIT     := "uv run pip-audit"
TOX           := "uv run tox"
SPHINX        := "uv run sphinx-build"
BUILD         := "uv run python -m build"
TWINE         := "uv run twine"
BUMP          := "uv run bump-my-version"

# Couleurs (pour l'affichage)
RESET   := '\033[0m'
BOLD    := '\033[1m'
RED     := '\033[31m'
GREEN   := '\033[32m'
YELLOW  := '\033[33m'
BLUE    := '\033[34m'
CYAN    := '\033[36m'

# Version du projet (extraite dynamiquement)
VERSION := `uv run python -c "from nexusdl.version import __version__; print(__version__)"`

# ==============================================================================
#  RECETTE PAR DÉFAUT
# ==============================================================================

# Liste les recettes disponibles
@default:
    @just --list --unsorted

# ==============================================================================
#  AIDE
# ==============================================================================

# Affiche l'aide détaillée
help:
    @echo -e "{{BOLD}}{{CYAN}}NexusDL — Commandes disponibles{{RESET}}"
    @echo ""
    @just --list

# Affiche la version du projet
version:
    @echo -e "{{BOLD}}NexusDL{{RESET}} v{{VERSION}}"

# Affiche les informations d'environnement
info:
    @echo -e "{{BOLD}}{{CYAN}}Informations projet NexusDL{{RESET}}"
    @echo "  Version        : {{VERSION}}"
    @echo "  Python         : $(python3 --version 2>&1)"
    @echo "  uv             : $(uv --version 2>&1)"
    @echo "  just           : $(just --version 2>&1)"
    @echo "  OS             : $(uname -srm 2>/dev/null || echo 'unknown')"
    @echo "  Répertoire     : $(pwd)"
    @echo "  Branche Git    : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"
    @echo "  Commit         : $(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
    @echo "  Statut Git     : $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') fichier(s) modifié(s)"

# ==============================================================================
#  INSTALLATION & SETUP
# ==============================================================================

# Installation complète (core + tous les extras + dev)
[group('setup')]
install:
    @echo -e "{{BLUE}}→ Installation des dépendances...{{RESET}}"
    uv sync --all-extras --dev
    @echo -e "{{GREEN}}✅ Installation terminée{{RESET}}"

# Installation minimale (core uniquement)
[group('setup')]
install-core:
    @echo -e "{{BLUE}}→ Installation des dépendances core...{{RESET}}"
    uv sync
    @echo -e "{{GREEN}}✅ Core installé{{RESET}}"

# Installation de l'interface web
[group('setup')]
install-web:
    @echo -e "{{BLUE}}→ Installation de l'interface Web...{{RESET}}"
    uv sync --extra web
    @echo -e "{{GREEN}}✅ Web installé{{RESET}}"

# Installation de l'interface desktop
[group('setup')]
install-gui:
    @echo -e "{{BLUE}}→ Installation de l'interface Desktop...{{RESET}}"
    uv sync --extra gui
    @echo -e "{{GREEN}}✅ GUI installée{{RESET}}"

# Installation des navigateurs Playwright
[group('setup')]
install-playwright:
    @echo -e "{{BLUE}}→ Installation des navigateurs Playwright...{{RESET}}"
    uv run playwright install chromium
    uv run playwright install-deps chromium || true
    @echo -e "{{GREEN}}✅ Playwright prêt{{RESET}}"

# Installation du frontend (Node.js)
[group('setup')]
install-frontend:
    @echo -e "{{BLUE}}→ Installation du frontend Next.js...{{RESET}}"
    cd {{src_dir}}/interfaces/web/frontend && pnpm install
    @echo -e "{{GREEN}}✅ Frontend prêt{{RESET}}"

# Installation des hooks pre-commit
[group('setup')]
install-hooks:
    @echo -e "{{BLUE}}→ Installation des hooks pre-commit...{{RESET}}"
    uv run pre-commit install
    uv run pre-commit install --hook-type commit-msg
    @echo -e "{{GREEN}}✅ Hooks installés{{RESET}}"

# Installation initiale complète (première fois)
[group('setup')]
bootstrap: install install-playwright install-hooks
    @echo -e "{{GREEN}}{{BOLD}}🎉 NexusDL est prêt à être utilisé !{{RESET}}"
    @echo -e "{{YELLOW}}Lance : just run{{RESET}}"

# ==============================================================================
#  DÉVELOPPEMENT
# ==============================================================================

# Lance l'interface CLI interactive (Textual)
[group('dev')]
run *ARGS:
    @echo -e "{{BLUE}}→ Lancement de NexusDL...{{RESET}}"
    uv run nexusdl {{ARGS}}

# Lance l'interface Web (backend FastAPI)
[group('dev')]
run-web *ARGS:
    @echo -e "{{BLUE}}→ Démarrage du backend FastAPI...{{RESET}}"
    uv run uvicorn nexusdl.interfaces.web.backend.main:app \
        --reload \
        --host 0.0.0.0 \
        --port 8000 \
        {{ARGS}}

# Lance le frontend Next.js en mode développement
[group('dev')]
run-frontend:
    @echo -e "{{BLUE}}→ Démarrage du frontend Next.js...{{RESET}}"
    cd {{src_dir}}/interfaces/web/frontend && pnpm dev

# Lance TOUT en parallèle (backend + frontend + flaresolverr)
[group('dev')]
run-all:
    @echo -e "{{BLUE}}→ Démarrage de la stack complète de dev...{{RESET}}"
    just --yes --shell-command --command 'docker compose -f {{docker_dir}}/docker-compose.dev.yml up'

# Lance l'interface Desktop (CustomTkinter)
[group('dev')]
run-gui *ARGS:
    @echo -e "{{BLUE}}→ Lancement de l'interface Desktop...{{RESET}}"
    uv run nexusdl-gui {{ARGS}}

# Lance un shell Python dans le venv (avec imports préchargés)
[group('dev')]
shell:
    @echo -e "{{BLUE}}→ Shell Python interactif...{{RESET}}"
    uv run python -i -c "import nexusdl; from nexusdl import NexusDL; print('NexusDL chargé. Utilise NexusDL() pour commencer.')"

# Lance un REPL IPython avec auto-imports
[group('dev')]
ipython:
    @echo -e "{{BLUE}}→ IPython avec auto-imports...{{RESET}}"
    uv run ipython --InteractiveShellApp.extensions=autoreload

# ==============================================================================
#  TESTS
# ==============================================================================

# Lance tous les tests
[group('test')]
test *ARGS:
    @echo -e "{{BLUE}}→ Exécution des tests...{{RESET}}"
    {{PYTEST}} {{tests_dir}} {{ARGS}}

# Lance les tests unitaires uniquement
[group('test')]
test-unit:
    @echo -e "{{BLUE}}→ Tests unitaires...{{RESET}}"
    {{PYTEST}} {{tests_dir}}/unit -m "unit"

# Lance les tests d'intégration
[group('test')]
test-integration:
    @echo -e "{{BLUE}}→ Tests d'intégration...{{RESET}}"
    {{PYTEST}} {{tests_dir}}/integration -m "integration"

# Lance les tests end-to-end (réseau réel)
[group('test')]
test-e2e:
    @echo -e "{{YELLOW}}→ Tests E2E (réseau réel requis)...{{RESET}}"
    {{PYTEST}} {{tests_dir}}/e2e -m "e2e" --no-header -v

# Lance les tests des parsers
[group('test')]
test-parsers:
    @echo -e "{{BLUE}}→ Tests des parsers...{{RESET}}"
    {{PYTEST}} {{tests_dir}}/integration/test_parsers -v

# Lance les tests avec couverture
[group('test')]
test-cov:
    @echo -e "{{BLUE}}→ Tests avec couverture...{{RESET}}"
    {{PYTEST}} --cov=src/nexusdl --cov-report=term-missing --cov-report=html --cov-report=xml

# Lance les tests en parallèle (plus rapide)
[group('test')]
test-fast:
    @echo -e "{{BLUE}}→ Tests en parallèle...{{RESET}}"
    {{PYTEST}} -n auto --dist loadgroup

# Lance les tests d'un module spécifique
[group('test')]
test-module MODULE:
    @echo -e "{{BLUE}}→ Tests de {{MODULE}}...{{RESET}}"
    {{PYTEST}} {{MODULE}} -v

# Lance les tests en mode watch (relance à chaque changement)
[group('test')]
test-watch:
    @echo -e "{{BLUE}}→ Tests en watch mode...{{RESET}}"
    uv run pytest-watch {{tests_dir}} -- -v

# Lance les doctests uniquement
[group('test')]
test-doctest:
    @echo -e "{{BLUE}}→ Doctests...{{RESET}}"
    {{PYTEST}} --doctest-modules {{src_dir}}

# Ouvre le rapport de couverture
[group('test')]
coverage-open:
    @echo -e "{{BLUE}}→ Ouverture du rapport de couverture...{{RESET}}"
    @command -v xdg-open >/dev/null && xdg-open htmlcov/index.html || \
     command -v open >/dev/null && open htmlcov/index.html || \
     echo -e "{{YELLOW}}Ouvre htmlcov/index.html dans ton navigateur{{RESET}}"

# ==============================================================================
#  QUALITÉ DE CODE
# ==============================================================================

# Vérifie tout (lint + type + sécurité)
[group('quality')]
check: lint-check format-check type-check security
    @echo -e "{{GREEN}}{{BOLD}}✅ Toutes les vérifications sont passées{{RESET}}"

# Vérifie le lint
[group('quality')]
lint-check:
    @echo -e "{{BLUE}}→ Vérification lint (ruff)...{{RESET}}"
    {{RUFF}} check {{src_dir}} {{tests_dir}} {{scripts_dir}}

# Corrige le lint automatiquement
[group('quality')]
lint:
    @echo -e "{{BLUE}}→ Lint + auto-fix...{{RESET}}"
    {{RUFF}} check --fix {{src_dir}} {{tests_dir}} {{scripts_dir}}

# Vérifie le formatage
[group('quality')]
format-check:
    @echo -e "{{BLUE}}→ Vérification formatage (ruff)...{{RESET}}"
    {{RUFF}} format --check {{src_dir}} {{tests_dir}} {{scripts_dir}}

# Applique le formatage
[group('quality')]
format:
    @echo -e "{{BLUE}}→ Formatage (ruff)...{{RESET}}"
    {{RUFF}} format {{src_dir}} {{tests_dir}} {{scripts_dir}}

# Vérifie les types (mypy strict)
[group('quality')]
type-check:
    @echo -e "{{BLUE}}→ Vérification de typage (mypy strict)...{{RESET}}"
    {{MYPY}} {{src_dir}}

# Vérifie la sécurité (bandit + pip-audit)
[group('quality')]
security:
    @echo -e "{{BLUE}}→ Audit de sécurité...{{RESET}}"
    {{BANDIT}} -c {{pyproject}} -r {{src_dir}}
    {{PIP_AUDIT}} --skip-editable

# Vérifie les dépendances inutilisées
[group('quality')]
check-deps:
    @echo -e "{{BLUE}}→ Vérification des dépendances...{{RESET}}"
    uv run deptry .

# Analyse la complexité du code
[group('quality')]
complexity:
    @echo -e "{{BLUE}}→ Analyse de complexité...{{RESET}}"
    uv run radon cc {{src_dir}} -a -s
    uv run radon mi {{src_dir}} -s

# Détecte le code mort
[group('quality')]
dead-code:
    @echo -e "{{BLUE}}→ Détection de code mort (vulture)...{{RESET}}"
    uv run vulture {{src_dir}} --min-confidence 80

# Lance tous les hooks pre-commit sur tous les fichiers
[group('quality')]
pre-commit-all:
    @echo -e "{{BLUE}}→ Pre-commit sur tous les fichiers...{{RESET}}"
    uv run pre-commit run --all-files

# ==============================================================================
#  NETTOYAGE
# ==============================================================================

# Nettoie tous les artefacts de build et caches
[group('clean')]
clean:
    @echo -e "{{BLUE}}→ Nettoyage...{{RESET}}"
    rm -rf build/ dist/ *.egg-info
    rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ .coverage coverage.xml htmlcov/
    rm -rf .tox/ .nox/
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name "*.pyo" -delete 2>/dev/null || true
    find . -type f -name ".DS_Store" -delete 2>/dev/null || true
    @echo -e "{{GREEN}}✅ Nettoyage terminé{{RESET}}"

# Nettoie le cache de build Rust/Node
[group('clean')]
clean-frontend:
    @echo -e "{{BLUE}}→ Nettoyage frontend...{{RESET}}"
    rm -rf {{src_dir}}/interfaces/web/frontend/node_modules
    rm -rf {{src_dir}}/interfaces/web/frontend/.next
    rm -rf {{src_dir}}/interfaces/web/frontend/out
    @echo -e "{{GREEN}}✅ Frontend nettoyé{{RESET}}"

# Nettoie le cache uv
[group('clean')]
clean-uv:
    @echo -e "{{BLUE}}→ Nettoyage du cache uv...{{RESET}}"
    uv cache clean
    @echo -e "{{GREEN}}✅ Cache uv nettoyé{{RESET}}"

# Nettoie tout (y compris venv)
[group('clean')]
clean-all: clean clean-frontend
    @echo -e "{{YELLOW}}→ Suppression du venv...{{RESET}}"
    rm -rf .venv/
    @echo -e "{{GREEN}}✅ Tout est nettoyé{{RESET}}"

# Réinstalle tout from scratch
[group('clean')]
reinstall: clean-all install install-playwright install-hooks
    @echo -e "{{GREEN}}{{BOLD}}🎉 Réinstallation complète terminée{{RESET}}"

# ==============================================================================
#  BUILD & PACKAGING
# ==============================================================================

# Build le package (wheel + sdist)
[group('build')]
build: clean
    @echo -e "{{BLUE}}→ Build du package...{{RESET}}"
    {{BUILD}}
    @echo -e "{{GREEN}}✅ Package construit dans dist/{{RESET}}"

# Vérifie le package avant publication
[group('build')]
build-check: build
    @echo -e "{{BLUE}}→ Vérification du package (twine)...{{RESET}}"
    {{TWINE}} check dist/*
    @echo -e "{{GREEN}}✅ Package vérifié{{RESET}}"

# Build du frontend (production)
[group('build')]
build-frontend:
    @echo -e "{{BLUE}}→ Build frontend (production)...{{RESET}}"
    cd {{src_dir}}/interfaces/web/frontend && pnpm build
    @echo -e "{{GREEN}}✅ Frontend build dans .next/{{RESET}}"

# Build les exécutables standalone (PyInstaller)
[group('build')]
build-binary:
    @echo -e "{{BLUE}}→ Build binaire standalone...{{RESET}}"
    uv run pyinstaller --clean --noconfirm \
        --name nexusdl \
        --icon {{assets_dir}}/icons/nexusdl.ico \
        --add-data "{{src_dir}}/data:nexusdl/data" \
        --hidden-import=pydantic \
        --hidden-import=playwright \
        {{src_dir}}/__main__.py
    @echo -e "{{GREEN}}✅ Binaire dans dist/nexusdl{{RESET}}"

# ==============================================================================
#  DOCUMENTATION
# ==============================================================================

# Build la documentation Sphinx
[group('docs')]
docs:
    @echo -e "{{BLUE}}→ Build de la documentation...{{RESET}}"
    {{SPHINX}} -W -b html {{docs_dir}}/source {{docs_dir}}/_build/html
    @echo -e "{{GREEN}}✅ Doc dans {{docs_dir}}/_build/html/index.html{{RESET}}"

# Build la doc avec MkDocs
[group('docs')]
docs-mkdocs:
    @echo -e "{{BLUE}}→ Build de la doc MkDocs...{{RESET}}"
    uv run mkdocs build --strict

# Sert la doc localement (hot reload)
[group('docs')]
docs-serve:
    @echo -e "{{BLUE}}→ Serveur de doc sur http://localhost:8000{{RESET}}"
    uv run mkdocs serve -a localhost:8000

# Ouvre la doc dans le navigateur
[group('docs')]
docs-open: docs
    @echo -e "{{BLUE}}→ Ouverture de la doc...{{RESET}}"
    @command -v xdg-open >/dev/null && xdg-open {{docs_dir}}/_build/html/index.html || \
     command -v open >/dev/null && open {{docs_dir}}/_build/html/index.html || \
     echo -e "{{YELLOW}}Ouvre {{docs_dir}}/_build/html/index.html{{RESET}}"

# Nettoie la doc
[group('docs')]
docs-clean:
    @echo -e "{{BLUE}}→ Nettoyage de la doc...{{RESET}}"
    rm -rf {{docs_dir}}/_build/
    rm -rf site/
    @echo -e "{{GREEN}}✅ Doc nettoyée{{RESET}}"

# ==============================================================================
#  BASE DE DONNÉES & MIGRATIONS
# ==============================================================================

# Initialise la base de données locale
[group('db')]
db-init:
    @echo -e "{{BLUE}}→ Initialisation de la BDD...{{RESET}}"
    uv run nexusdl db init
    @echo -e "{{GREEN}}✅ BDD initialisée{{RESET}}"

# Applique les migrations
[group('db')]
db-migrate:
    @echo -e "{{BLUE}}→ Application des migrations...{{RESET}}"
    uv run nexusdl db migrate
    @echo -e "{{GREEN}}✅ Migrations appliquées{{RESET}}"

# Reset la base de données
[group('db')]
db-reset:
    @echo -e "{{YELLOW}}⚠️  Reset de la BDD (destructif)...{{RESET}}"
    uv run nexusdl db reset --yes
    @echo -e "{{GREEN}}✅ BDD reset{{RESET}}"

# Sauvegarde la BDD
[group('db')]
db-backup:
    @echo -e "{{BLUE}}→ Sauvegarde de la BDD...{{RESET}}"
    uv run nexusdl db backup --output "backups/library-$(date +%Y%m%d-%H%M%S).db"
    @echo -e "{{GREEN}}✅ Sauvegarde effectuée{{RESET}}"

# ==============================================================================
#  SITES & PARSERS
# ==============================================================================

# Valide la configuration de tous les sites (sites.yaml)
[group('sites')]
validate-sites:
    @echo -e "{{BLUE}}→ Validation des sites...{{RESET}}"
    uv run python {{scripts_dir}}/validate_sites.py

# Teste un parser spécifique
[group('sites')]
test-site SITE:
    @echo -e "{{BLUE}}→ Test du parser {{SITE}}...{{RESET}}"
    uv run nexusdl test-parser {{SITE}} --verbose

# Liste tous les sites supportés
[group('sites')]
list-sites:
    @echo -e "{{BLUE}}→ Sites supportés :{{RESET}}"
    uv run nexusdl sites --all

# Rafraîchit les cookies Cloudflare pour un site
[group('sites')]
refresh-cookies SITE:
    @echo -e "{{BLUE}}→ Rafraîchissement des cookies pour {{SITE}}...{{RESET}}"
    uv run nexusdl cookies refresh {{SITE}}

# Génère un squelette de nouveau parser
[group('sites')]
new-parser SITE LANGUAGE:
    @echo -e "{{BLUE}}→ Génération d'un parser pour {{SITE}} ({{LANGUAGE}})...{{RESET}}"
    uv run python {{scripts_dir}}/generate_parser.py --site {{SITE}} --lang {{LANGUAGE}}
    @echo -e "{{GREEN}}✅ Parser généré{{RESET}}"

# ==============================================================================
#  DOCKER
# ==============================================================================

# Build les images Docker
[group('docker')]
docker-build:
    @echo -e "{{BLUE}}→ Build des images Docker...{{RESET}}"
    docker compose -f {{docker_dir}}/docker-compose.yml build

# Démarre la stack Docker (production)
[group('docker')]
docker-up:
    @echo -e "{{BLUE}}→ Démarrage de la stack Docker...{{RESET}}"
    docker compose -f {{docker_dir}}/docker-compose.yml up -d
    @echo -e "{{GREEN}}✅ Stack démarrée{{RESET}}"

# Démarre la stack Docker (développement)
[group('docker')]
docker-dev:
    @echo -e "{{BLUE}}→ Démarrage de la stack dev...{{RESET}}"
    docker compose -f {{docker_dir}}/docker-compose.dev.yml up

# Arrête la stack Docker
[group('docker')]
docker-down:
    @echo -e "{{BLUE}}→ Arrêt de la stack Docker...{{RESET}}"
    docker compose -f {{docker_dir}}/docker-compose.yml down

# Affiche les logs Docker
[group('docker')]
docker-logs *SERVICE:
    docker compose -f {{docker_dir}}/docker-compose.yml logs -f {{SERVICE}}

# Nettoie les ressources Docker
[group('docker')]
docker-clean:
    @echo -e "{{YELLOW}}→ Nettoyage Docker...{{RESET}}"
    docker compose -f {{docker_dir}}/docker-compose.yml down -v --rmi local
    docker system prune -f
    @echo -e "{{GREEN}}✅ Docker nettoyé{{RESET}}"

# ==============================================================================
#  CI/CD
# ==============================================================================

# Simule la CI locale (toutes les vérifications)
[group('ci')]
ci: check test-cov build-check
    @echo -e "{{GREEN}}{{BOLD}}🎉 CI locale passée avec succès{{RESET}}"

# Lance la matrice tox complète
[group('ci')]
tox:
    @echo -e "{{BLUE}}→ Exécution de la matrice tox...{{RESET}}"
    {{TOX}}

# Lance tox pour un environnement spécifique
[group('ci')]
tox-env ENV:
    @echo -e "{{BLUE}}→ Tox env : {{ENV}}...{{RESET}}"
    {{TOX}} -e {{ENV}}

# ==============================================================================
#  RELEASE
# ==============================================================================

# Bump la version (patch par défaut)
[group('release')]
bump PART="patch":
    @echo -e "{{BLUE}}→ Bump version ({{PART}})...{{RESET}}"
    {{BUMP}} bump {{PART}}
    @echo -e "{{GREEN}}✅ Version bumpée{{RESET}}"

# Crée un tag de release
[group('release')]
tag:
    @echo -e "{{BLUE}}→ Création du tag v{{VERSION}}...{{RESET}}"
    git tag -a "v{{VERSION}}" -m "Release v{{VERSION}}"
    @echo -e "{{GREEN}}✅ Tag créé{{RESET}}"

# Publie sur PyPI (test)
[group('release')]
publish-test: build-check
    @echo -e "{{BLUE}}→ Publication sur TestPyPI...{{RESET}}"
    {{TWINE}} upload --repository testpypi dist/*
    @echo -e "{{GREEN}}✅ Publié sur TestPyPI{{RESET}}"

# Publie sur PyPI (production)
[group('release')]
publish: build-check
    @echo -e "{{YELLOW}}⚠️  Publication sur PyPI (production)...{{RESET}}"
    {{TWINE}} upload dist/*
    @echo -e "{{GREEN}}✅ Publié sur PyPI{{RESET}}"

# Workflow complet de release
[group('release')]
release PART="patch": clean check test-cov bump tag build-check
    @echo -e "{{GREEN}}{{BOLD}}🎉 Release v{{VERSION}} prête à être publiée{{RESET}}"
    @echo -e "{{YELLOW}}Prochaine étape : git push --follow-tags{{RESET}}"

# ==============================================================================
#  GIT
# ==============================================================================

# Affiche l'état Git
[group('git')]
git-status:
    @git status --short --branch

# Ajoute tous les changements et commit
[group('git')]
commit MSG:
    git add -A
    git commit -m "{{MSG}}"

# Push vers origin
[group('git')]
push:
    git push origin $(git rev-parse --abbrev-ref HEAD)

# Pull depuis origin avec rebase
[group('git')]
pull:
    git pull --rebase origin $(git rev-parse --abbrev-ref HEAD)

# Crée une nouvelle branche feature
[group('git')]
branch NAME:
    git checkout -b "feature/{{NAME}}"

# Nettoie les branches locales mergées
[group('git')]
git-clean:
    git branch --merged | grep -v "\*\|main\|develop" | xargs -n 1 git branch -d || true

# ==============================================================================
#  UTILITAIRES
# ==============================================================================

# Valide tous les manifestes nexus.dl
[group('utils')]
validate-manifests:
    @echo -e "{{BLUE}}→ Validation des manifestes nexus.dl...{{RESET}}"
    uv run python {{scripts_dir}}/validate_nexus_dl.py

# Régénère tous les manifestes nexus.dl
[group('utils')]
generate-manifests:
    @echo -e "{{BLUE}}→ Régénération des manifestes nexus.dl...{{RESET}}"
    uv run python {{scripts_dir}}/generate_nexus_manifests.py --root .

# Génère les requirements.txt depuis pyproject
[group('utils')]
freeze:
    @echo -e "{{BLUE}}→ Génération des requirements.txt...{{RESET}}"
    uv pip compile {{pyproject}} -o requirements.txt
    uv pip compile {{pyproject}} --extra dev -o requirements-dev.txt
    uv pip compile {{pyproject}} --extra web -o requirements-web.txt
    uv pip compile {{pyproject}} --extra gui -o requirements-gui.txt
    @echo -e "{{GREEN}}✅ requirements*.txt générés{{RESET}}"

# Vérifie la cohérence du lockfile
[group('utils')]
lock-check:
    uv lock --check

# Met à jour toutes les dépendances
[group('utils')]
update:
    @echo -e "{{BLUE}}→ Mise à jour des dépendances...{{RESET}}"
    uv lock --upgrade
    @echo -e "{{GREEN}}✅ Dépendances mises à jour{{RESET}}"

# Benchmark les performances
[group('utils')]
bench:
    @echo -e "{{BLUE}}→ Benchmarks...{{RESET}}"
    uv run python {{scripts_dir}}/benchmark.py

# Profiling d'une commande NexusDL
[group('utils')]
profile *ARGS:
    @echo -e "{{BLUE}}→ Profiling : nexusdl {{ARGS}}{{RESET}}"
    uv run py-spy record -o profile.svg -- uv run nexusdl {{ARGS}}
    @echo -e "{{GREEN}}✅ Profil dans profile.svg{{RESET}}"

# Statistiques du projet (lignes de code, etc.)
[group('utils')]
stats:
    @echo -e "{{BOLD}}{{CYAN}}Statistiques NexusDL{{RESET}}"
    @echo "  Fichiers Python  : $(find {{src_dir}} -name '*.py' | wc -l | tr -d ' ')"
    @echo "  Lignes Python    : $(find {{src_dir}} -name '*.py' -exec cat {} + | wc -l | tr -d ' ')"
    @echo "  Fichiers TS/TSX  : $(find {{src_dir}}/interfaces/web/frontend/src -name '*.ts*' 2>/dev/null | wc -l | tr -d ' ')"
    @echo "  Parseurs         : $(find {{src_dir}}/parsers -name '*.py' -not -name '__init__.py' -not -name 'base.py' | wc -l | tr -d ' ')"
    @echo "  Sites configurés : $(grep -c '^  [a-z_]*:' {{src_dir}}/core/registry/sites.yaml 2>/dev/null || echo 'N/A')"

# Ouvre le projet dans VSCode
[group('utils')]
code:
    code .

# Ouvre le projet dans PyCharm
[group('utils')]
pycharm:
    pycharm .

# ==============================================================================
#  ALIASES PRATIQUES
# ==============================================================================

alias r   := run
alias t   := test
alias tc  := test-cov
alias l   := lint
alias f   := format
alias c   := check
alias b   := build
alias d   := docs
alias i   := info
alias s   := stats
alias v   := version
