# ============================================================================
# NEXUSDL - Makefile
# ============================================================================
# Usage :
#   make              # Affiche l'aide
#   make <command>    # Exécute une commande
#   make help         # Affiche toutes les commandes disponibles
# ============================================================================

# ============================================================================
# VARIABLES
# ============================================================================

# Couleurs pour l'affichage
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Chemins
BACKEND_DIR := src/nexusdl
FRONTEND_DIR := src/nexusdl/interfaces/web/frontend
DOCKER_DIR := docker
TESTS_DIR := tests
DOCS_DIR := docs

# Python
PYTHON := python
PIP := $(PYTHON) -m pip
PYTEST := $(PYTHON) -m pytest
RUFF := $(PYTHON) -m ruff
MYPY := $(PYTHON) -m mypy

# Node.js
PNPM := pnpm
NODE := node
NPM := npm

# Docker
DOCKER := docker
DOCKER_COMPOSE := docker compose

# URLs
BACKEND_URL := http://localhost:8000
FRONTEND_URL := http://localhost:3000
RAILWAY_URL := https://nexus-dl-production.up.railway.app
NETLIFY_URL := https://nexusdldev.netlify.app

# Environnement
ENV := development
LOG_LEVEL := INFO

# ============================================================================
# CIBLES PAR DÉFAUT
# ============================================================================

.PHONY: help
help: ## Affiche cette aide
	@echo ""
	@echo "$(BLUE)╔══════════════════════════════════════════════════════════════════╗$(NC)"
	@echo "$(BLUE)║                    NEXUSDL - Command Runner                    ║$(NC)"
	@echo "$(BLUE)╚══════════════════════════════════════════════════════════════════╝$(NC)"
	@echo ""
	@echo "$(GREEN)Commandes disponibles :$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

.DEFAULT_GOAL := help

# ============================================================================
# INSTALLATION & SETUP
# ============================================================================

.PHONY: install
install: ## Installe toutes les dépendances (Python + Node.js)
	@echo "$(BLUE)🔧 Installation des dépendances Python...$(NC)"
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "$(BLUE)🔧 Installation des dépendances frontend...$(NC)"
	cd $(FRONTEND_DIR) && $(PNPM) install
	@echo ""
	@echo "$(GREEN)✅ Installation terminée !$(NC)"

.PHONY: install-backend
install-backend: ## Installe uniquement le backend Python
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -e ".[dev]"

.PHONY: install-frontend
install-frontend: ## Installe uniquement le frontend
	cd $(FRONTEND_DIR) && $(PNPM) install

.PHONY: install-playwright
install-playwright: ## Installe Playwright avec les navigateurs
	$(PYTHON) -m playwright install chromium
	$(PYTHON) -m playwright install-deps chromium

.PHONY: install-hooks
install-hooks: ## Installe les pre-commit hooks
	$(PIP) install pre-commit
	$(PYTHON) -m pre_commit install
	$(PYTHON) -m pre_commit install --hook-type commit-msg

# ============================================================================
# DÉVELOPPEMENT - BACKEND
# ============================================================================

.PHONY: dev-backend
dev-backend: ## Lance le backend en mode développement (avec hot reload)
	$(PYTHON) -m uvicorn nexusdl.interfaces.web.backend.main:app \
		--host 0.0.0.0 \
		--port 8000 \
		--reload \
		--reload-dir $(BACKEND_DIR) \
		--log-level info

.PHONY: run-backend
run-backend: ## Lance le backend en mode production
	$(PYTHON) -m uvicorn nexusdl.interfaces.web.backend.main:app \
		--host 0.0.0.0 \
		--port 8000 \
		--workers 1 \
		--proxy-headers \
		--forwarded-allow-ips '*' \
		--log-level info

.PHONY: dev-cli
dev-cli: ## Lance l'interface CLI (Textual)
	$(PYTHON) -m nexusdl --cli

.PHONY: dev-gui
dev-gui: ## Lance l'interface GUI (PyQt6)
	$(PYTHON) -m nexusdl --gui

.PHONY: shell
shell: ## Lance le shell Python interactif avec NexusDL chargé
	$(PYTHON) -c "import nexusdl; from nexusdl.core import *; print('NexusDL chargé !')"

# ============================================================================
# DÉVELOPPEMENT - FRONTEND
# ============================================================================

.PHONY: dev-frontend
dev-frontend: ## Lance le frontend en mode développement
	cd $(FRONTEND_DIR) && $(PNPM) dev

.PHONY: run-frontend
run-frontend: ## Lance le frontend en mode production
	cd $(FRONTEND_DIR) && $(PNPM) build && $(PNPM) start

.PHONY: storybook
storybook: ## Lance Storybook pour les composants UI
	cd $(FRONTEND_DIR) && $(PNPM) storybook

# ============================================================================
# DÉVELOPPEMENT COMBINÉ
# ============================================================================

.PHONY: dev
dev: ## Lance backend + frontend en parallèle
	@echo "$(BLUE)🚀 Démarrage du backend sur $(BACKEND_URL)...$(NC)"
	@echo "$(BLUE)🚀 Démarrage du frontend sur $(FRONTEND_URL)...$(NC)"
	@echo ""
	@echo "Appuie sur Ctrl+C pour arrêter les deux"
	@echo ""
	@# Lance le backend en arrière-plan
	@$(PYTHON) -m uvicorn nexusdl.interfaces.web.backend.main:app \
		--host 0.0.0.0 --port 8000 --reload & \
	BACKEND_PID=$$!; \
	cd $(FRONTEND_DIR) && $(PNPM) dev & \
	FRONTEND_PID=$$!; \
	trap "kill $$BACKEND_PID $$FRONTEND_PID 2>/dev/null; exit" INT TERM; \
	wait

# ============================================================================
# TESTS
# ============================================================================

.PHONY: test
test: ## Lance tous les tests Python
	$(PYTEST) $(TESTS_DIR) -v --tb=short

.PHONY: test-cov
test-cov: ## Lance les tests avec couverture
	$(PYTEST) $(TESTS_DIR) \
		--cov=$(BACKEND_DIR) \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-report=xml:coverage.xml \
		-v

.PHONY: test-fast
test-fast: ## Lance uniquement les tests rapides
	$(PYTEST) $(TESTS_DIR) -v -m "not slow"

.PHONY: test-integration
test-integration: ## Lance les tests d'intégration
	$(PYTEST) $(TESTS_DIR) -v -m "integration"

.PHONY: test-e2e
test-e2e: ## Lance les tests end-to-end (Playwright)
	cd $(FRONTEND_DIR) && $(PNPM) test:e2e

.PHONY: test-watch
test-watch: ## Lance les tests en watch mode
	$(PYTEST) $(TESTS_DIR) -v --watch

# ============================================================================
# LINTING & QUALITÉ DU CODE
# ============================================================================

.PHONY: lint
lint: lint-python lint-frontend ## Lance tous les linters (Python + Frontend)

.PHONY: lint-python
lint-python: ## Lint Python avec Ruff
	$(RUFF) check $(BACKEND_DIR) $(TESTS_DIR)
	$(RUFF) format --check $(BACKEND_DIR) $(TESTS_DIR)
	$(MYPY) $(BACKEND_DIR) --ignore-missing-imports

.PHONY: lint-frontend
lint-frontend: ## Lint frontend avec ESLint + Prettier
	cd $(FRONTEND_DIR) && $(PNPM) lint
	cd $(FRONTEND_DIR) && $(PNPM) format:check
	cd $(FRONTEND_DIR) && $(PNPM) type-check

.PHONY: fix
fix: fix-python fix-frontend ## Corrige automatiquement les erreurs de lint

.PHONY: fix-python
fix-python: ## Fix Python avec Ruff
	$(RUFF) check --fix $(BACKEND_DIR) $(TESTS_DIR)
	$(RUFF) format $(BACKEND_DIR) $(TESTS_DIR)

.PHONY: fix-frontend
fix-frontend: ## Fix frontend avec ESLint + Prettier
	cd $(FRONTEND_DIR) && $(PNPM) lint --fix
	cd $(FRONTEND_DIR) && $(PNPM) format

# ============================================================================
# BUILD
# ============================================================================

.PHONY: build
build: build-python build-frontend ## Build complet (Python package + Frontend)

.PHONY: build-python
build-python: ## Build du package Python
	$(PYTHON) -m build --sdist --wheel --outdir dist/

.PHONY: build-frontend
build-frontend: ## Build du frontend pour la production
	cd $(FRONTEND_DIR) && $(PNPM) build

.PHONY: analyze
analyze: ## Analyse la taille du bundle frontend
	cd $(FRONTEND_DIR) && $(PNPM) analyze

# ============================================================================
# DOCKER
# ============================================================================

.PHONY: docker-build
docker-build: docker-build-backend docker-build-web docker-build-gui ## Build toutes les images Docker

.PHONY: docker-build-backend
docker-build-backend: ## Build l'image backend
	$(DOCKER) build -t nexusdl-backend:latest -f $(DOCKER_DIR)/Dockerfile .

.PHONY: docker-build-web
docker-build-web: ## Build l'image web (backend + frontend)
	$(DOCKER) build -t nexusdl-web:latest -f $(DOCKER_DIR)/Dockerfile.web .

.PHONY: docker-build-gui
docker-build-gui: ## Build l'image GUI
	$(DOCKER) build -t nexusdl-gui:latest -f $(DOCKER_DIR)/Dockerfile.gui .

.PHONY: docker-run-backend
docker-run-backend: ## Lance le backend dans Docker
	$(DOCKER) run --rm -it \
		-p 8000:8000 \
		-v nexusdl-data:/app/data \
		-e NEXUSDL_ENV=production \
		nexusdl-backend:latest

.PHONY: docker-run-web
docker-run-web: ## Lance le web (backend + frontend) dans Docker
	$(DOCKER) run --rm -it \
		-p 8000:8000 \
		-v nexusdl-data:/app/data \
		-e NEXUSDL_ENV=production \
		nexusdl-web:latest

.PHONY: docker-up
docker-up: ## Lance docker-compose (tous les services)
	$(DOCKER_COMPOSE) up -d

.PHONY: docker-down
docker-down: ## Arrête docker-compose
	$(DOCKER_COMPOSE) down

.PHONY: docker-logs
docker-logs: ## Voir les logs docker-compose
	$(DOCKER_COMPOSE) logs -f

# ============================================================================
# DÉPLOIEMENT
# ============================================================================

.PHONY: deploy-railway
deploy-railway: ## Déploie sur Railway (via CLI)
	railway up

.PHONY: deploy-netlify
deploy-netlify: ## Déploie sur Netlify (via CLI)
	cd $(FRONTEND_DIR) && netlify deploy --prod

.PHONY: deploy-netlify-preview
deploy-netlify-preview: ## Déploie sur Netlify (mode preview)
	cd $(FRONTEND_DIR) && netlify deploy

.PHONY: deploy-push
deploy-push: ## Pousse vers GitHub (déclenche les déploiements automatiques)
	git push origin main

# ============================================================================
# BASE DE DONNÉES
# ============================================================================

.PHONY: db-shell
db-shell: ## Ouvre la base SQLite dans le shell
	sqlite3 data/nexusdl.db

.PHONY: db-backup
db-backup: ## Backup de la base de données
	@BACKUP_FILE="data/nexusdl-backup-$$(date +%Y%m%d-%H%M%S).db"; \
	cp data/nexusdl.db "$$BACKUP_FILE"; \
	echo "$(GREEN)✅ Backup créé : $$BACKUP_FILE$(NC)"

.PHONY: db-restore
db-restore: ## Restaure la base de données depuis un backup
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "$(RED)❌ Erreur: BACKUP_FILE non défini$(NC)"; \
		echo "Usage: make db-restore BACKUP_FILE=data/nexusdl-backup-20260126-120000.db"; \
		exit 1; \
	fi
	cp $(BACKUP_FILE) data/nexusdl.db
	@echo "$(GREEN)✅ Base restaurée depuis $(BACKUP_FILE)$(NC)"

# ============================================================================
# DOCUMENTATION
# ============================================================================

.PHONY: docs
docs: ## Génère la documentation
	cd $(DOCS_DIR) && $(PYTHON) -m mkdocs build

.PHONY: docs-serve
docs-serve: ## Sert la documentation en local
	cd $(DOCS_DIR) && $(PYTHON) -m mkdocs serve

# ============================================================================
# NETTOYAGE
# ============================================================================

.PHONY: clean
clean: ## Nettoie tous les fichiers temporaires
	@echo "$(BLUE)🧹 Nettoyage des fichiers Python...$(NC)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "$(BLUE)🧹 Nettoyage des fichiers Node.js...$(NC)"
	@rm -rf $(FRONTEND_DIR)/node_modules 2>/dev/null || true
	@rm -rf $(FRONTEND_DIR)/.next 2>/dev/null || true
	@rm -rf $(FRONTEND_DIR)/out 2>/dev/null || true
	@echo "$(BLUE)🧹 Nettoyage des fichiers de build...$(NC)"
	@rm -rf dist/ build/ *.egg-info 2>/dev/null || true
	@rm -rf htmlcov/ coverage.xml .coverage 2>/dev/null || true
	@echo "$(GREEN)✅ Nettoyage terminé !$(NC)"

.PHONY: clean-python
clean-python: ## Nettoie uniquement Python
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist/ build/ *.egg-info htmlcov/ coverage.xml .coverage 2>/dev/null || true

.PHONY: clean-frontend
clean-frontend: ## Nettoie uniquement le frontend
	@rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/.next $(FRONTEND_DIR)/out 2>/dev/null || true

# ============================================================================
# INFORMATIONS
# ============================================================================

.PHONY: version
version: ## Affiche la version de NexusDL
	$(PYTHON) -c "import nexusdl; print(f'NexusDL v{nexusdl.__version__}')"

.PHONY: info
info: ## Affiche les informations du système
	@echo ""
	@echo "$(BLUE)╔══════════════════════════════════════════════════════════════════╗$(NC)"
	@echo "$(BLUE)║                    NEXUSDL - System Info                       ║$(NC)"
	@echo "$(BLUE)╠══════════════════════════════════════════════════════════════════╣$(NC)"
	@echo "║ Python    : $$($(PYTHON) --version)"
	@echo "║ Node.js   : $$($(NODE) --version 2>/dev/null || echo 'not installed')"
	@echo "║ pnpm      : $$($(PNPM) --version 2>/dev/null || echo 'not installed')"
	@echo "║ Docker    : $$($(DOCKER) --version 2>/dev/null || echo 'not installed')"
	@echo "║ make      : $$(make --version 2>/dev/null | head -n 1 || echo 'not installed')"
	@echo "$(BLUE)╠══════════════════════════════════════════════════════════════════╣$(NC)"
	@echo "║ Backend   : $(BACKEND_URL)"
	@echo "║ Frontend  : $(FRONTEND_URL)"
	@echo "║ Railway   : $(RAILWAY_URL)"
	@echo "║ Netlify   : $(NETLIFY_URL)"
	@echo "$(BLUE)╚══════════════════════════════════════════════════════════════════╝$(NC)"
	@echo ""

.PHONY: status
status: ## Affiche l'état des services
	@echo "$(BLUE)🔍 Vérification de l'état des services...$(NC)"
	@echo ""
	@echo -n "Backend  : "
	@if curl -sf $(BACKEND_URL)/health > /dev/null 2>&1; then \
		echo "$(GREEN)✅ Running$(NC)"; \
	else \
		echo "$(RED)❌ Not running$(NC)"; \
	fi
	@echo -n "Frontend : "
	@if curl -sf $(FRONTEND_URL) > /dev/null 2>&1; then \
		echo "$(GREEN)✅ Running$(NC)"; \
	else \
		echo "$(RED)❌ Not running$(NC)"; \
	fi
	@echo -n "Railway  : "
	@if curl -sf $(RAILWAY_URL)/health > /dev/null 2>&1; then \
		echo "$(GREEN)✅ Online$(NC)"; \
	else \
		echo "$(RED)❌ Offline$(NC)"; \
	fi

# ============================================================================
# UTILITAIRES
# ============================================================================

.PHONY: generate-secret
generate-secret: ## Génère une nouvelle clé secrète
	$(PYTHON) -c "import secrets; print(secrets.token_urlsafe(64))"

.PHONY: generate-api-key
generate-api-key: ## Génère une nouvelle clé API
	$(PYTHON) -c "import secrets; print('nxd_' + secrets.token_hex(32))"

.PHONY: check-deps
check-deps: ## Vérifie les dépendances Python
	$(PIP) list --outdated
	$(PIP) audit 2>/dev/null || echo "pip-audit non installé"

.PHONY: check-deps-frontend
check-deps-frontend: ## Vérifie les dépendances Node.js
	cd $(FRONTEND_DIR) && $(PNPM) audit

.PHONY: update-deps
update-deps: ## Met à jour toutes les dépendances
	$(PIP) install --upgrade -r requirements.txt
	cd $(FRONTEND_DIR) && $(PNPM) update

# ============================================================================
# HELPERS
# ============================================================================

.PHONY: open-docs
open-docs: ## Ouvre la documentation API dans le navigateur
	@if command -v xdg-open > /dev/null; then \
		xdg-open $(BACKEND_URL)/docs; \
	elif command -v open > /dev/null; then \
		open $(BACKEND_URL)/docs; \
	else \
		echo "Ouvre $(BACKEND_URL)/docs dans ton navigateur"; \
	fi

.PHONY: open-frontend
open-frontend: ## Ouvre le frontend dans le navigateur
	@if command -v xdg-open > /dev/null; then \
		xdg-open $(FRONTEND_URL); \
	elif command -v open > /dev/null; then \
		open $(FRONTEND_URL); \
	else \
		echo "Ouvre $(FRONTEND_URL) dans ton navigateur"; \
	fi

# ============================================================================
# ALIASES
# ============================================================================

.PHONY: d db df t tc l b c i
d: dev ## Alias pour 'dev'
db: dev-backend ## Alias pour 'dev-backend'
df: dev-frontend ## Alias pour 'dev-frontend'
t: test ## Alias pour 'test'
tc: test-cov ## Alias pour 'test-cov'
l: lint ## Alias pour 'lint'
b: build ## Alias pour 'build'
c: clean ## Alias pour 'clean'
i: install ## Alias pour 'install'
