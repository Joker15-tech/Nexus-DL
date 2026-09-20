# ==============================================================================
#  NexusDL — Makefile
# ==============================================================================
#  Raccourcis de développement et d'exploitation pour NexusDL.
#  Compatible : GNU Make >= 4.3 (Linux, macOS, WSL, Git Bash)
#
#  Usage :
#      make                  → affiche l'aide
#      make <target>         → exécute une cible
#      make <target> ARG=... → avec argument
#      make help             → aide détaillée
#      make -j4 <target>     → parallélise si possible
# ==============================================================================

# ------------------------------------------------------------------------------
# CONFIGURATION GLOBALE
# ------------------------------------------------------------------------------

# Ne pas afficher les commandes (mode silencieux)
MAKEFLAGS += --no-print-directory

# Shell robuste avec gestion d'erreurs
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# Encodage
export LANG := C.UTF-8
export LC_ALL := C.UTF-8

# Charger .env si présent
-include .env
export

# ------------------------------------------------------------------------------
# VARIABLES
# ------------------------------------------------------------------------------

# Dossiers
SRC_DIR       := src/nexusdl
TESTS_DIR     := tests
SCRIPTS_DIR   := scripts
DOCS_DIR      := docs
DOCKER_DIR    := docker
ASSETS_DIR    := assets
CONFIG_DIR    := config

# Fichiers
PYPROJECT     := pyproject.toml
LOCKFILE      := uv.lock
README        := README.md
CHANGELOG     := CHANGELOG.md

# Outils
PYTHON        := python3.12
UV            := uv
PYTEST        := $(UV) run pytest
RUFF          := $(UV) run ruff
MYPY          := $(UV) run mypy
BANDIT        := $(UV) run bandit
PIP_AUDIT     := $(UV) run pip-audit
TOX           := $(UV) run tox
SPHINX        := $(UV) run sphinx-build
BUILD         := $(UV) run python -m build
TWINE         := $(UV) run twine
BUMP          := $(UV) run bump-my-version

# Version du projet (extraite dynamiquement)
VERSION       := $(shell $(UV) run python -c "from nexusdl.version import __version__; print(__version__)" 2>/dev/null || echo "dev")

# Couleurs ANSI (désactivées si NO_COLOR est défini)
ifdef NO_COLOR
    RESET  :=
    BOLD   :=
    RED    :=
    GREEN  :=
    YELLOW :=
    BLUE   :=
    CYAN   :=
else
    RESET  := \033[0m
    BOLD   := \033[1m
    RED    := \033[31m
    GREEN  := \033[32m
    YELLOW := \033[33m
    BLUE   := \033[34m
    CYAN   := \033[36m
endif

# Commande d'ouverture navigateur (cross-platform)
ifeq ($(shell uname -s),Darwin)
    OPEN := open
else ifeq ($(OS),Windows_NT)
    OPEN := start
else
    OPEN := xdg-open
endif

# ------------------------------------------------------------------------------
# CIBLES PHONY
# ------------------------------------------------------------------------------

.PHONY: help version info \
        install install-core install-web install-gui install-playwright \
        install-frontend install-hooks bootstrap \
        run run-web run-frontend run-all run-gui shell ipython \
        test test-unit test-integration test-e2e test-parsers test-cov \
        test-fast test-module test-watch test-doctest coverage-open \
        check lint-check lint format-check format type-check security \
        check-deps complexity dead-code pre-commit-all \
        clean clean-frontend clean-uv clean-all reinstall \
        build build-check build-frontend build-binary \
        docs docs-mkdocs docs-serve docs-open docs-clean \
        db-init db-migrate db-reset db-backup \
        validate-sites test-site list-sites refresh-cookies new-parser \
        docker-build docker-up docker-dev docker-down docker-logs docker-clean \
        ci tox tox-env \
        bump tag publish-test publish release \
        git-status commit push pull branch git-clean \
        validate-manifests generate-manifests freeze lock-check update \
        bench profile stats code pycharm \
        all

# ------------------------------------------------------------------------------
# CIBLE PAR DÉFAUT
# ------------------------------------------------------------------------------

.DEFAULT_GOAL := help

# ==============================================================================
#  AIDE
# ==============================================================================

## Affiche cette aide
help: ## 📖 Affiche cette aide
	@echo ""
	@echo -e "$(BOLD)$(CYAN)╔══════════════════════════════════════════════════════════════╗$(RESET)"
	@echo -e "$(BOLD)$(CYAN)║                    NexusDL — Commandes                       ║$(RESET)"
	@echo -e "$(BOLD)$(CYAN)╚══════════════════════════════════════════════════════════════╝$(RESET)"
	@echo ""
	@echo -e "$(BOLD)Usage :$(RESET) make $(CYAN)<target>$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo -e "$(BOLD)Info projet :$(RESET) make version"
	@echo ""

## Affiche la version du projet
version: ## 📌 Affiche la version
	@echo -e "$(BOLD)NexusDL$(RESET) v$(VERSION)"

## Affiche les informations d'environnement
info: ## ℹ️  Informations d'environnement
	@echo -e "$(BOLD)$(CYAN)Informations projet NexusDL$(RESET)"
	@echo "  Version        : $(VERSION)"
	@echo "  Python         : $$(python3 --version 2>&1)"
	@echo "  uv             : $$(uv --version 2>&1)"
	@echo "  make           : $$(make --version | head -n1)"
	@echo "  OS             : $$(uname -srm 2>/dev/null || echo 'unknown')"
	@echo "  Répertoire     : $$(pwd)"
	@echo "  Branche Git    : $$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"
	@echo "  Commit         : $$(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
	@echo "  Statut Git     : $$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') fichier(s) modifié(s)"

# ==============================================================================
#  INSTALLATION & SETUP
# ==============================================================================

## Installation complète (core + extras + dev)
install: ## 📦 Installation complète
	@echo -e "$(BLUE)→ Installation des dépendances...$(RESET)"
	$(UV) sync --all-extras --dev
	@echo -e "$(GREEN)✅ Installation terminée$(RESET)"

## Installation minimale (core uniquement)
install-core: ## 📦 Installation core uniquement
	@echo -e "$(BLUE)→ Installation des dépendances core...$(RESET)"
	$(UV) sync
	@echo -e "$(GREEN)✅ Core installé$(RESET)"

## Installation de l'interface Web
install-web: ## 📦 Installation de l'interface Web
	@echo -e "$(BLUE)→ Installation de l'interface Web...$(RESET)"
	$(UV) sync --extra web
	@echo -e "$(GREEN)✅ Web installé$(RESET)"

## Installation de l'interface Desktop
install-gui: ## 📦 Installation de l'interface Desktop
	@echo -e "$(BLUE)→ Installation de l'interface Desktop...$(RESET)"
	$(UV) sync --extra gui
	@echo -e "$(GREEN)✅ GUI installée$(RESET)"

## Installation des navigateurs Playwright
install-playwright: ## 🎭 Installation de Playwright
	@echo -e "$(BLUE)→ Installation des navigateurs Playwright...$(RESET)"
	$(UV) run playwright install chromium
	$(UV) run playwright install-deps chromium || true
	@echo -e "$(GREEN)✅ Playwright prêt$(RESET)"

## Installation du frontend (Node.js)
install-frontend: ## 📦 Installation du frontend Next.js
	@echo -e "$(BLUE)→ Installation du frontend Next.js...$(RESET)"
	cd $(SRC_DIR)/interfaces/web/frontend && pnpm install
	@echo -e "$(GREEN)✅ Frontend prêt$(RESET)"

## Installation des hooks pre-commit
install-hooks: ## 🪝 Installation des hooks pre-commit
	@echo -e "$(BLUE)→ Installation des hooks pre-commit...$(RESET)"
	$(UV) run pre-commit install
	$(UV) run pre-commit install --hook-type commit-msg
	@echo -e "$(GREEN)✅ Hooks installés$(RESET)"

## Installation initiale complète (première fois)
bootstrap: install install-playwright install-hooks ## 🚀 Bootstrap complet
	@echo -e "$(GREEN)$(BOLD)🎉 NexusDL est prêt à être utilisé !$(RESET)"
	@echo -e "$(YELLOW)Lance : make run$(RESET)"

# ==============================================================================
#  DÉVELOPPEMENT
# ==============================================================================

## Lance l'interface CLI interactive (Textual)
run: ## ▶️  Lance NexusDL (CLI)
	@echo -e "$(BLUE)→ Lancement de NexusDL...$(RESET)"
	$(UV) run nexusdl $(ARGS)

## Lance l'interface Web (backend FastAPI)
run-web: ## ▶️  Lance le backend FastAPI
	@echo -e "$(BLUE)→ Démarrage du backend FastAPI...$(RESET)"
	$(UV) run uvicorn nexusdl.interfaces.web.backend.main:app \
		--reload --host 0.0.0.0 --port 8000 $(ARGS)

## Lance le frontend Next.js en mode développement
run-frontend: ## ▶️  Lance le frontend Next.js
	@echo -e "$(BLUE)→ Démarrage du frontend Next.js...$(RESET)"
	cd $(SRC_DIR)/interfaces/web/frontend && pnpm dev

## Lance TOUT en parallèle (Docker)
run-all: ## ▶️  Lance la stack complète (Docker)
	@echo -e "$(BLUE)→ Démarrage de la stack complète de dev...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.dev.yml up

## Lance l'interface Desktop (CustomTkinter)
run-gui: ## ▶️  Lance l'interface Desktop
	@echo -e "$(BLUE)→ Lancement de l'interface Desktop...$(RESET)"
	$(UV) run nexusdl-gui $(ARGS)

## Lance un shell Python interactif
shell: ## 🐍 Shell Python interactif
	@echo -e "$(BLUE)→ Shell Python interactif...$(RESET)"
	$(UV) run python -i -c "import nexusdl; from nexusdl import NexusDL; print('NexusDL chargé. Utilise NexusDL() pour commencer.')"

## Lance IPython avec auto-imports
ipython: ## 🐍 IPython interactif
	@echo -e "$(BLUE)→ IPython avec auto-imports...$(RESET)"
	$(UV) run ipython --InteractiveShellApp.extensions=autoreload

# ==============================================================================
#  TESTS
# ==============================================================================

## Lance tous les tests
test: ## 🧪 Tous les tests
	@echo -e "$(BLUE)→ Exécution des tests...$(RESET)"
	$(PYTEST) $(TESTS_DIR) $(ARGS)

## Lance les tests unitaires uniquement
test-unit: ## 🧪 Tests unitaires
	@echo -e "$(BLUE)→ Tests unitaires...$(RESET)"
	$(PYTEST) $(TESTS_DIR)/unit -m "unit"

## Lance les tests d'intégration
test-integration: ## 🧪 Tests d'intégration
	@echo -e "$(BLUE)→ Tests d'intégration...$(RESET)"
	$(PYTEST) $(TESTS_DIR)/integration -m "integration"

## Lance les tests end-to-end (réseau réel)
test-e2e: ## 🧪 Tests E2E (réseau réel)
	@echo -e "$(YELLOW)→ Tests E2E (réseau réel requis)...$(RESET)"
	$(PYTEST) $(TESTS_DIR)/e2e -m "e2e" --no-header -v

## Lance les tests des parsers
test-parsers: ## 🧪 Tests des parsers
	@echo -e "$(BLUE)→ Tests des parsers...$(RESET)"
	$(PYTEST) $(TESTS_DIR)/integration/test_parsers -v

## Lance les tests avec couverture
test-cov: ## 🧪 Tests avec couverture
	@echo -e "$(BLUE)→ Tests avec couverture...$(RESET)"
	$(PYTEST) --cov=src/nexusdl --cov-report=term-missing --cov-report=html --cov-report=xml

## Lance les tests en parallèle
test-fast: ## 🧪 Tests en parallèle
	@echo -e "$(BLUE)→ Tests en parallèle...$(RESET)"
	$(PYTEST) -n auto --dist loadgroup

## Lance les tests d'un module spécifique
test-module: ## 🧪 Tests d'un module (MODULE=...)
	@if [ -z "$(MODULE)" ]; then echo -e "$(RED)❌ MODULE requis. Ex: make test-module MODULE=tests/unit/test_config.py$(RESET)"; exit 1; fi
	@echo -e "$(BLUE)→ Tests de $(MODULE)...$(RESET)"
	$(PYTEST) $(MODULE) -v

## Lance les tests en mode watch
test-watch: ## 🧪 Tests en watch mode
	@echo -e "$(BLUE)→ Tests en watch mode...$(RESET)"
	$(UV) run pytest-watch $(TESTS_DIR) -- -v

## Lance les doctests uniquement
test-doctest: ## 🧪 Doctests
	@echo -e "$(BLUE)→ Doctests...$(RESET)"
	$(PYTEST) --doctest-modules $(SRC_DIR)

## Ouvre le rapport de couverture
coverage-open: ## 📊 Ouvre le rapport de couverture
	@echo -e "$(BLUE)→ Ouverture du rapport de couverture...$(RESET)"
	@$(OPEN) htmlcov/index.html

# ==============================================================================
#  QUALITÉ DE CODE
# ==============================================================================

## Vérifie tout (lint + type + sécurité)
check: lint-check format-check type-check security ## ✅ Toutes les vérifications
	@echo -e "$(GREEN)$(BOLD)✅ Toutes les vérifications sont passées$(RESET)"

## Vérifie le lint
lint-check: ## 🔍 Vérification lint
	@echo -e "$(BLUE)→ Vérification lint (ruff)...$(RESET)"
	$(RUFF) check $(SRC_DIR) $(TESTS_DIR) $(SCRIPTS_DIR)

## Corrige le lint automatiquement
lint: ## 🔧 Lint + auto-fix
	@echo -e "$(BLUE)→ Lint + auto-fix...$(RESET)"
	$(RUFF) check --fix $(SRC_DIR) $(TESTS_DIR) $(SCRIPTS_DIR)

## Vérifie le formatage
format-check: ## 🔍 Vérification formatage
	@echo -e "$(BLUE)→ Vérification formatage (ruff)...$(RESET)"
	$(RUFF) format --check $(SRC_DIR) $(TESTS_DIR) $(SCRIPTS_DIR)

## Applique le formatage
format: ## 🎨 Formatage auto
	@echo -e "$(BLUE)→ Formatage (ruff)...$(RESET)"
	$(RUFF) format $(SRC_DIR) $(TESTS_DIR) $(SCRIPTS_DIR)

## Vérifie les types (mypy strict)
type-check: ## 🔍 Vérification de typage
	@echo -e "$(BLUE)→ Vérification de typage (mypy strict)...$(RESET)"
	$(MYPY) $(SRC_DIR)

## Vérifie la sécurité (bandit + pip-audit)
security: ## 🔒 Audit de sécurité
	@echo -e "$(BLUE)→ Audit de sécurité...$(RESET)"
	$(BANDIT) -c $(PYPROJECT) -r $(SRC_DIR)
	$(PIP_AUDIT) --skip-editable

## Vérifie les dépendances inutilisées
check-deps: ## 🔍 Vérification des dépendances
	@echo -e "$(BLUE)→ Vérification des dépendances...$(RESET)"
	$(UV) run deptry .

## Analyse la complexité du code
complexity: ## 📊 Analyse de complexité
	@echo -e "$(BLUE)→ Analyse de complexité...$(RESET)"
	$(UV) run radon cc $(SRC_DIR) -a -s
	$(UV) run radon mi $(SRC_DIR) -s

## Détecte le code mort
dead-code: ## 💀 Détection de code mort
	@echo -e "$(BLUE)→ Détection de code mort (vulture)...$(RESET)"
	$(UV) run vulture $(SRC_DIR) --min-confidence 80

## Lance tous les hooks pre-commit sur tous les fichiers
pre-commit-all: ## 🪝 Pre-commit sur tous les fichiers
	@echo -e "$(BLUE)→ Pre-commit sur tous les fichiers...$(RESET)"
	$(UV) run pre-commit run --all-files

# ==============================================================================
#  NETTOYAGE
# ==============================================================================

## Nettoie tous les artefacts de build et caches
clean: ## 🧹 Nettoie les artefacts
	@echo -e "$(BLUE)→ Nettoyage...$(RESET)"
	rm -rf build/ dist/ *.egg-info
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ .coverage coverage.xml htmlcov/
	rm -rf .tox/ .nox/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type f -name ".DS_Store" -delete 2>/dev/null || true
	@echo -e "$(GREEN)✅ Nettoyage terminé$(RESET)"

## Nettoie le cache du frontend
clean-frontend: ## 🧹 Nettoie le frontend
	@echo -e "$(BLUE)→ Nettoyage frontend...$(RESET)"
	rm -rf $(SRC_DIR)/interfaces/web/frontend/node_modules
	rm -rf $(SRC_DIR)/interfaces/web/frontend/.next
	rm -rf $(SRC_DIR)/interfaces/web/frontend/out
	@echo -e "$(GREEN)✅ Frontend nettoyé$(RESET)"

## Nettoie le cache uv
clean-uv: ## 🧹 Nettoie le cache uv
	@echo -e "$(BLUE)→ Nettoyage du cache uv...$(RESET)"
	$(UV) cache clean
	@echo -e "$(GREEN)✅ Cache uv nettoyé$(RESET)"

## Nettoie tout (y compris venv)
clean-all: clean clean-frontend ## 🧹 Nettoyage complet
	@echo -e "$(YELLOW)→ Suppression du venv...$(RESET)"
	rm -rf .venv/
	@echo -e "$(GREEN)✅ Tout est nettoyé$(RESET)"

## Réinstalle tout from scratch
reinstall: clean-all install install-playwright install-hooks ## 🔄 Réinstallation complète
	@echo -e "$(GREEN)$(BOLD)🎉 Réinstallation complète terminée$(RESET)"

# ==============================================================================
#  BUILD & PACKAGING
# ==============================================================================

## Build le package (wheel + sdist)
build: clean ## 📦 Build du package
	@echo -e "$(BLUE)→ Build du package...$(RESET)"
	$(BUILD)
	@echo -e "$(GREEN)✅ Package construit dans dist/$(RESET)"

## Vérifie le package avant publication
build-check: build ## 📦 Vérifie le package
	@echo -e "$(BLUE)→ Vérification du package (twine)...$(RESET)"
	$(TWINE) check dist/*
	@echo -e "$(GREEN)✅ Package vérifié$(RESET)"

## Build du frontend (production)
build-frontend: ## 📦 Build frontend production
	@echo -e "$(BLUE)→ Build frontend (production)...$(RESET)"
	cd $(SRC_DIR)/interfaces/web/frontend && pnpm build
	@echo -e "$(GREEN)✅ Frontend build dans .next/$(RESET)"

## Build les exécutables standalone (PyInstaller)
build-binary: ## 📦 Build binaire standalone
	@echo -e "$(BLUE)→ Build binaire standalone...$(RESET)"
	$(UV) run pyinstaller --clean --noconfirm \
		--name nexusdl \
		--icon $(ASSETS_DIR)/icons/nexusdl.ico \
		--add-data "$(SRC_DIR)/data:nexusdl/data" \
		--hidden-import=pydantic \
		--hidden-import=playwright \
		$(SRC_DIR)/__main__.py
	@echo -e "$(GREEN)✅ Binaire dans dist/nexusdl$(RESET)"

# ==============================================================================
#  DOCUMENTATION
# ==============================================================================

## Build la documentation Sphinx
docs: ## 📚 Build de la doc
	@echo -e "$(BLUE)→ Build de la documentation...$(RESET)"
	$(SPHINX) -W -b html $(DOCS_DIR)/source $(DOCS_DIR)/_build/html
	@echo -e "$(GREEN)✅ Doc dans $(DOCS_DIR)/_build/html/index.html$(RESET)"

## Build la doc avec MkDocs
docs-mkdocs: ## 📚 Build de la doc (MkDocs)
	@echo -e "$(BLUE)→ Build de la doc MkDocs...$(RESET)"
	$(UV) run mkdocs build --strict

## Sert la doc localement (hot reload)
docs-serve: ## 📚 Sert la doc localement
	@echo -e "$(BLUE)→ Serveur de doc sur http://localhost:8000$(RESET)"
	$(UV) run mkdocs serve -a localhost:8000

## Ouvre la doc dans le navigateur
docs-open: docs ## 📚 Ouvre la doc
	@echo -e "$(BLUE)→ Ouverture de la doc...$(RESET)"
	@$(OPEN) $(DOCS_DIR)/_build/html/index.html

## Nettoie la doc
docs-clean: ## 🧹 Nettoie la doc
	@echo -e "$(BLUE)→ Nettoyage de la doc...$(RESET)"
	rm -rf $(DOCS_DIR)/_build/
	rm -rf site/
	@echo -e "$(GREEN)✅ Doc nettoyée$(RESET)"

# ==============================================================================
#  BASE DE DONNÉES & MIGRATIONS
# ==============================================================================

## Initialise la base de données locale
db-init: ## 💾 Initialise la BDD
	@echo -e "$(BLUE)→ Initialisation de la BDD...$(RESET)"
	$(UV) run nexusdl db init
	@echo -e "$(GREEN)✅ BDD initialisée$(RESET)"

## Applique les migrations
db-migrate: ## 💾 Applique les migrations
	@echo -e "$(BLUE)→ Application des migrations...$(RESET)"
	$(UV) run nexusdl db migrate
	@echo -e "$(GREEN)✅ Migrations appliquées$(RESET)"

## Reset la base de données
db-reset: ## 💾 Reset la BDD (destructif)
	@echo -e "$(YELLOW)⚠️  Reset de la BDD (destructif)...$(RESET)"
	$(UV) run nexusdl db reset --yes
	@echo -e "$(GREEN)✅ BDD reset$(RESET)"

## Sauvegarde la BDD
db-backup: ## 💾 Sauvegarde la BDD
	@echo -e "$(BLUE)→ Sauvegarde de la BDD...$(RESET)"
	mkdir -p backups
	$(UV) run nexusdl db backup --output "backups/library-$$(date +%Y%m%d-%H%M%S).db"
	@echo -e "$(GREEN)✅ Sauvegarde effectuée$(RESET)"

# ==============================================================================
#  SITES & PARSERS
# ==============================================================================

## Valide la configuration de tous les sites
validate-sites: ## 🌐 Valide les sites configurés
	@echo -e "$(BLUE)→ Validation des sites...$(RESET)"
	$(UV) run python $(SCRIPTS_DIR)/validate_sites.py

## Teste un parser spécifique
test-site: ## 🌐 Teste un parser (SITE=...)
	@if [ -z "$(SITE)" ]; then echo -e "$(RED)❌ SITE requis. Ex: make test-site SITE=mangadex$(RESET)"; exit 1; fi
	@echo -e "$(BLUE)→ Test du parser $(SITE)...$(RESET)"
	$(UV) run nexusdl test-parser $(SITE) --verbose

## Liste tous les sites supportés
list-sites: ## 🌐 Liste les sites supportés
	@echo -e "$(BLUE)→ Sites supportés :$(RESET)"
	$(UV) run nexusdl sites --all

## Rafraîchit les cookies Cloudflare pour un site
refresh-cookies: ## 🔐 Rafraîchit les cookies (SITE=...)
	@if [ -z "$(SITE)" ]; then echo -e "$(RED)❌ SITE requis. Ex: make refresh-cookies SITE=sushiscan$(RESET)"; exit 1; fi
	@echo -e "$(BLUE)→ Rafraîchissement des cookies pour $(SITE)...$(RESET)"
	$(UV) run nexusdl cookies refresh $(SITE)

## Génère un squelette de nouveau parser
new-parser: ## 🌐 Génère un parser (SITE=... LANG=...)
	@if [ -z "$(SITE)" ] || [ -z "$(LANG)" ]; then echo -e "$(RED)❌ SITE et LANG requis. Ex: make new-parser SITE=mysite LANG=fr$(RESET)"; exit 1; fi
	@echo -e "$(BLUE)→ Génération d'un parser pour $(SITE) ($(LANG))...$(RESET)"
	$(UV) run python $(SCRIPTS_DIR)/generate_parser.py --site $(SITE) --lang $(LANG)
	@echo -e "$(GREEN)✅ Parser généré$(RESET)"

# ==============================================================================
#  DOCKER
# ==============================================================================

## Build les images Docker
docker-build: ## 🐳 Build des images Docker
	@echo -e "$(BLUE)→ Build des images Docker...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.yml build

## Démarre la stack Docker (production)
docker-up: ## 🐳 Démarre la stack Docker
	@echo -e "$(BLUE)→ Démarrage de la stack Docker...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.yml up -d
	@echo -e "$(GREEN)✅ Stack démarrée$(RESET)"

## Démarre la stack Docker (développement)
docker-dev: ## 🐳 Démarre la stack dev
	@echo -e "$(BLUE)→ Démarrage de la stack dev...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.dev.yml up

## Arrête la stack Docker
docker-down: ## 🐳 Arrête la stack Docker
	@echo -e "$(BLUE)→ Arrêt de la stack Docker...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.yml down

## Affiche les logs Docker
docker-logs: ## 🐳 Logs Docker (SERVICE=...)
	docker compose -f $(DOCKER_DIR)/docker-compose.yml logs -f $(SERVICE)

## Nettoie les ressources Docker
docker-clean: ## 🐳 Nettoie Docker
	@echo -e "$(YELLOW)→ Nettoyage Docker...$(RESET)"
	docker compose -f $(DOCKER_DIR)/docker-compose.yml down -v --rmi local
	docker system prune -f
	@echo -e "$(GREEN)✅ Docker nettoyé$(RESET)"

# ==============================================================================
#  CI/CD
# ==============================================================================

## Simule la CI locale (toutes les vérifications)
ci: check test-cov build-check ## 🤖 CI locale complète
	@echo -e "$(GREEN)$(BOLD)🎉 CI locale passée avec succès$(RESET)"

## Lance la matrice tox complète
tox: ## 🤖 Matrice tox complète
	@echo -e "$(BLUE)→ Exécution de la matrice tox...$(RESET)"
	$(TOX)

## Lance tox pour un environnement spécifique
tox-env: ## 🤖 Tox env spécifique (ENV=...)
	@if [ -z "$(ENV)" ]; then echo -e "$(RED)❌ ENV requis. Ex: make tox-env ENV=py312$(RESET)"; exit 1; fi
	@echo -e "$(BLUE)→ Tox env : $(ENV)...$(RESET)"
	$(TOX) -e $(ENV)

# ==============================================================================
#  RELEASE
# ==============================================================================

## Bump la version (PART=patch|minor|major)
bump: ## 🚀 Bump version (PART=patch)
	@PART=$${PART:-patch}; \
	echo -e "$(BLUE)→ Bump version ($$PART)...$(RESET)"; \
	$(BUMP) bump $$PART; \
	echo -e "$(GREEN)✅ Version bumpée$(RESET)"

## Crée un tag de release
tag: ## 🚀 Crée un tag de release
	@echo -e "$(BLUE)→ Création du tag v$(VERSION)...$(RESET)"
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo -e "$(GREEN)✅ Tag créé$(RESET)"

## Publie sur PyPI (test)
publish-test: build-check ## 🚀 Publie sur TestPyPI
	@echo -e "$(BLUE)→ Publication sur TestPyPI...$(RESET)"
	$(TWINE) upload --repository testpypi dist/*
	@echo -e "$(GREEN)✅ Publié sur TestPyPI$(RESET)"

## Publie sur PyPI (production)
publish: build-check ## 🚀 Publie sur PyPI
	@echo -e "$(YELLOW)⚠️  Publication sur PyPI (production)...$(RESET)"
	$(TWINE) upload dist/*
	@echo -e "$(GREEN)✅ Publié sur PyPI$(RESET)"

## Workflow complet de release
release: clean check test-cov bump tag build-check ## 🚀 Release complète
	@echo -e "$(GREEN)$(BOLD)🎉 Release v$(VERSION) prête à être publiée$(RESET)"
	@echo -e "$(YELLOW)Prochaine étape : git push --follow-tags$(RESET)"

# ==============================================================================
#  GIT
# ==============================================================================

## Affiche l'état Git
git-status: ## 🔀 État Git
	@git status --short --branch

## Commit tous les changements (MSG=...)
commit: ## 🔀 Commit (MSG=...)
	@if [ -z "$(MSG)" ]; then echo -e "$(RED)❌ MSG requis. Ex: make commit MSG='feat: add mangafire'$(RESET)"; exit 1; fi
	git add -A
	git commit -m "$(MSG)"

## Push vers origin
push: ## 🔀 Push vers origin
	git push origin $$(git rev-parse --abbrev-ref HEAD)

## Pull depuis origin avec rebase
pull: ## 🔀 Pull avec rebase
	git pull --rebase origin $$(git rev-parse --abbrev-ref HEAD)

## Crée une nouvelle branche feature (NAME=...)
branch: ## 🔀 Nouvelle branche (NAME=...)
	@if [ -z "$(NAME)" ]; then echo -e "$(RED)❌ NAME requis. Ex: make branch NAME=ma-feature$(RESET)"; exit 1; fi
	git checkout -b "feature/$(NAME)"

## Nettoie les branches locales mergées
git-clean: ## 🔀 Nettoie les branches mergées
	git branch --merged | grep -v "\*\|main\|develop" | xargs -n 1 git branch -d || true

# ==============================================================================
#  UTILITAIRES
# ==============================================================================

## Valide tous les manifestes nexus.dl
validate-manifests: ## 🛠️  Valide les manifestes nexus.dl
	@echo -e "$(BLUE)→ Validation des manifestes nexus.dl...$(RESET)"
	$(UV) run python $(SCRIPTS_DIR)/validate_nexus_dl.py

## Régénère tous les manifestes nexus.dl
generate-manifests: ## 🛠️  Régénère les manifestes nexus.dl
	@echo -e "$(BLUE)→ Régénération des manifestes nexus.dl...$(RESET)"
	$(UV) run python $(SCRIPTS_DIR)/generate_nexus_manifests.py --root .

## Génère les requirements.txt depuis pyproject
freeze: ## 🛠️  Génère les requirements.txt
	@echo -e "$(BLUE)→ Génération des requirements.txt...$(RESET)"
	$(UV) pip compile $(PYPROJECT) -o requirements.txt
	$(UV) pip compile $(PYPROJECT) --extra dev -o requirements-dev.txt
	$(UV) pip compile $(PYPROJECT) --extra web -o requirements-web.txt
	$(UV) pip compile $(PYPROJECT) --extra gui -o requirements-gui.txt
	@echo -e "$(GREEN)✅ requirements*.txt générés$(RESET)"

## Vérifie la cohérence du lockfile
lock-check: ## 🛠️  Vérifie le lockfile
	$(UV) lock --check

## Met à jour toutes les dépendances
update: ## 🛠️  Met à jour les dépendances
	@echo -e "$(BLUE)→ Mise à jour des dépendances...$(RESET)"
	$(UV) lock --upgrade
	@echo -e "$(GREEN)✅ Dépendances mises à jour$(RESET)"

## Benchmark les performances
bench: ## 🛠️  Benchmarks
	@echo -e "$(BLUE)→ Benchmarks...$(RESET)"
	$(UV) run python $(SCRIPTS_DIR)/benchmark.py

## Profiling d'une commande NexusDL
profile: ## 🛠️  Profiling (ARGS=...)
	@echo -e "$(BLUE)→ Profiling : nexusdl $(ARGS)$(RESET)"
	$(UV) run py-spy record -o profile.svg -- $(UV) run nexusdl $(ARGS)
	@echo -e "$(GREEN)✅ Profil dans profile.svg$(RESET)"

## Statistiques du projet
stats: ## 🛠️  Statistiques du projet
	@echo -e "$(BOLD)$(CYAN)Statistiques NexusDL$(RESET)"
	@echo "  Fichiers Python  : $$(find $(SRC_DIR) -name '*.py' | wc -l | tr -d ' ')"
	@echo "  Lignes Python    : $$(find $(SRC_DIR) -name '*.py' -exec cat {} + | wc -l | tr -d ' ')"
	@echo "  Fichiers TS/TSX  : $$(find $(SRC_DIR)/interfaces/web/frontend/src -name '*.ts*' 2>/dev/null | wc -l | tr -d ' ')"
	@echo "  Parseurs         : $$(find $(SRC_DIR)/parsers -name '*.py' -not -name '__init__.py' -not -name 'base.py' 2>/dev/null | wc -l | tr -d ' ')"
	@echo "  Manifestes       : $$(find . -name 'nexus.dl' 2>/dev/null | wc -l | tr -d ' ')"

## Ouvre le projet dans VSCode
code: ## 🛠️  Ouvre dans VSCode
	code .

## Ouvre le projet dans PyCharm
pycharm: ## 🛠️  Ouvre dans PyCharm
	pycharm .

# ==============================================================================
#  TOUT
# ==============================================================================

## Exécute tout : check + test + build
all: check test-cov build ## 🎯 Check + Test + Build
	@echo -e "$(GREEN)$(BOLD)🎉 Tout est passé avec succès$(RESET)"

# ==============================================================================
#  GARDE-FOUS
# ==============================================================================

# Empêche make de chercher des fichiers correspondant aux noms de cibles
%:
	@:

# Vérifie que uv est installé
.check-uv:
	@command -v $(UV) >/dev/null 2>&1 || { \
		echo -e "$(RED)❌ uv n'est pas installé.$(RESET)"; \
		echo -e "$(YELLOW)Installation : curl -LsSf https://astral.sh/uv/install.sh | sh$(RESET)"; \
		exit 1; \
	}

# Vérifie que docker est installé
.check-docker:
	@command -v docker >/dev/null 2>&1 || { \
		echo -e "$(RED)❌ docker n'est pas installé.$(RESET)"; \
		exit 1; \
	}
