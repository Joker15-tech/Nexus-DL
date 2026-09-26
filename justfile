# ============================================================================
# NEXUSDL - Just Command Runner
# ============================================================================
# Installation de just :
#   - macOS   : brew install just
#   - Linux   : cargo install just  (ou via package manager)
#   - Windows : scoop install just  (ou cargo install just)
#
# Utilisation :
#   just              # Liste toutes les commandes
#   just <recette>    # Exécute une recette
#   just --list       # Liste toutes les recettes
# ============================================================================

# ============================================================================
# VARIABLES GLOBALES
# ============================================================================

# Chemins
backend_dir := "src/nexusdl"
frontend_dir := "src/nexusdl/interfaces/web/frontend"
docker_dir := "docker"
tests_dir := "tests"
docs_dir := "docs"

# Python
python := "python"
pip := python + " -m pip"
pytest := python + " -m pytest"
ruff := python + " -m ruff"
mypy := python + " -m mypy"

# Node.js
pnpm := "pnpm"
node := "node"
npm := "npm"

# Docker
docker := "docker"
docker_compose := "docker compose"

# URLs
backend_url := "http://localhost:8000"
frontend_url := "http://localhost:3000"
railway_url := "https://nexus-dl-production.up.railway.app"
netlify_url := "https://nexusdldev.netlify.app"

# Environnement
env := "development"
log_level := "INFO"

# ============================================================================
# RECETTES PAR DÉFAUT
# ============================================================================

# Liste toutes les commandes disponibles
default:
    #!/usr/bin/env bash
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                  NEXUSDL - Command Runner                       ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  Utilise 'just <command>' pour exécuter une commande            ║"
    echo "║  Utilise 'just --list' pour voir toutes les commandes           ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    just --list

# ============================================================================
# INSTALLATION & SETUP
# ============================================================================

# Installe toutes les dépendances (Python + Node.js)
install:
    #!/usr/bin/env bash
    echo "🔧 Installation des dépendances Python..."
    {{pip}} install --upgrade pip setuptools wheel
    {{pip}} install -e ".[dev]"
    echo ""
    echo "🔧 Installation des dépendances frontend..."
    cd {{frontend_dir}} && {{pnpm}} install
    echo ""
    echo "✅ Installation terminée !"

# Installe uniquement le backend Python
install-backend:
    {{pip}} install --upgrade pip setuptools wheel
    {{pip}} install -e ".[dev]"

# Installe uniquement le frontend
install-frontend:
    cd {{frontend_dir}} && {{pnpm}} install

# Installe Playwright avec les navigateurs
install-playwright:
    {{python}} -m playwright install chromium
    {{python}} -m playwright install-deps chromium

# Installe les pre-commit hooks
install-hooks:
    {{pip}} install pre-commit
    {{python}} -m pre_commit install
    {{python}} -m pre_commit install --hook-type commit-msg

# ============================================================================
# DÉVELOPPEMENT - BACKEND
# ============================================================================

# Lance le backend en mode développement (avec hot reload)
dev-backend:
    {{python}} -m uvicorn nexusdl.interfaces.web.backend.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --reload \
        --reload-dir {{backend_dir}} \
        --log-level info

# Lance le backend en mode production
run-backend:
    {{python}} -m uvicorn nexusdl.interfaces.web.backend.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers 1 \
        --proxy-headers \
        --forwarded-allow-ips '*' \
        --log-level info

# Lance l'interface CLI (Textual)
dev-cli:
    {{python}} -m nexusdl --cli

# Lance l'interface GUI (PyQt6)
dev-gui:
    {{python}} -m nexusdl --gui

# Lance le shell Python interactif avec NexusDL chargé
shell:
    {{python}} -c "import nexusdl; from nexusdl.core import *; print('NexusDL chargé !')"

# ============================================================================
# DÉVELOPPEMENT - FRONTEND
# ============================================================================

# Lance le frontend en mode développement
dev-frontend:
    cd {{frontend_dir}} && {{pnpm}} dev

# Lance le frontend en mode production
run-frontend:
    cd {{frontend_dir}} && {{pnpm}} build && {{pnpm}} start

# Lance Storybook pour les composants UI
storybook:
    cd {{frontend_dir}} && {{pnpm}} storybook

# ============================================================================
# DÉVELOPPEMENT COMBINÉ
# ============================================================================

# Lance backend + frontend en parallèle
dev:
    #!/usr/bin/env bash
    echo "🚀 Démarrage du backend sur {{backend_url}}..."
    echo "🚀 Démarrage du frontend sur {{frontend_url}}..."
    echo ""
    echo "Appuie sur Ctrl+C pour arrêter les deux"
    echo ""
    
    # Lance le backend en arrière-plan
    {{python}} -m uvicorn nexusdl.interfaces.web.backend.main:app \
        --host 0.0.0.0 --port 8000 --reload &
    BACKEND_PID=$!
    
    # Lance le frontend
    cd {{frontend_dir}} && {{pnpm}} dev &
    FRONTEND_PID=$!
    
    # Capture Ctrl+C pour arrêter les deux
    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
    wait

# ============================================================================
# TESTS
# ============================================================================

# Lance tous les tests Python
test:
    {{pytest}} {{tests_dir}} -v --tb=short

# Lance les tests avec couverture
test-cov:
    {{pytest}} {{tests_dir}} \
        --cov={{backend_dir}} \
        --cov-report=term-missing \
        --cov-report=html:htmlcov \
        --cov-report=xml:coverage.xml \
        -v

# Lance uniquement les tests rapides
test-fast:
    {{pytest}} {{tests_dir}} -v -m "not slow"

# Lance les tests d'intégration
test-integration:
    {{pytest}} {{tests_dir}} -v -m "integration"

# Lance les tests end-to-end (Playwright)
test-e2e:
    cd {{frontend_dir}} && {{pnpm}} test:e2e

# Lance les tests en watch mode (re-exécute à chaque modification)
test-watch:
    {{pytest}} {{tests_dir}} -v --watch

# ============================================================================
# LINTING & QUALITÉ DU CODE
# ============================================================================

# Lance tous les linters (Python + Frontend)
lint: lint-python lint-frontend

# Lint Python avec Ruff
lint-python:
    {{ruff}} check {{backend_dir}} {{tests_dir}}
    {{ruff}} format --check {{backend_dir}} {{tests_dir}}
    {{mypy}} {{backend_dir}} --ignore-missing-imports

# Lint frontend avec ESLint + Prettier
lint-frontend:
    cd {{frontend_dir}} && {{pnpm}} lint
    cd {{frontend_dir}} && {{pnpm}} format:check
    cd {{frontend_dir}} && {{pnpm}} type-check

# Corrige automatiquement les erreurs de lint
fix: fix-python fix-frontend

# Fix Python avec Ruff
fix-python:
    {{ruff}} check --fix {{backend_dir}} {{tests_dir}}
    {{ruff}} format {{backend_dir}} {{tests_dir}}

# Fix frontend avec ESLint + Prettier
fix-frontend:
    cd {{frontend_dir}} && {{pnpm}} lint --fix
    cd {{frontend_dir}} && {{pnpm}} format

# ============================================================================
# BUILD
# ============================================================================

# Build complet (Python package + Frontend)
build: build-python build-frontend

# Build du package Python
build-python:
    {{python}} -m build --sdist --wheel --outdir dist/

# Build du frontend pour la production
build-frontend:
    cd {{frontend_dir}} && {{pnpm}} build

# Analyse la taille du bundle frontend
analyze:
    cd {{frontend_dir}} && {{pnpm}} analyze

# ============================================================================
# DOCKER
# ============================================================================

# Build toutes les images Docker
docker-build: docker-build-backend docker-build-web docker-build-gui

# Build l'image backend
docker-build-backend:
    {{docker}} build -t nexusdl-backend:latest -f {{docker_dir}}/Dockerfile .

# Build l'image web (backend + frontend)
docker-build-web:
    {{docker}} build -t nexusdl-web:latest -f {{docker_dir}}/Dockerfile.web .

# Build l'image GUI
docker-build-gui:
    {{docker}} build -t nexusdl-gui:latest -f {{docker_dir}}/Dockerfile.gui .

# Lance le backend dans Docker
docker-run-backend:
    {{docker}} run --rm -it \
        -p 8000:8000 \
        -v nexusdl-data:/app/data \
        -e NEXUSDL_ENV=production \
        nexusdl-backend:latest

# Lance le web (backend + frontend) dans Docker
docker-run-web:
    {{docker}} run --rm -it \
        -p 8000:8000 \
        -v nexusdl-data:/app/data \
        -e NEXUSDL_ENV=production \
        nexusdl-web:latest

# Lance docker-compose (tous les services)
docker-up:
    {{docker_compose}} up -d

# Arrête docker-compose
docker-down:
    {{docker_compose}} down

# Voir les logs docker-compose
docker-logs:
    {{docker_compose}} logs -f

# ============================================================================
# DÉPLOIEMENT
# ============================================================================

# Déploie sur Railway (via CLI)
deploy-railway:
    railway up

# Déploie sur Netlify (via CLI)
deploy-netlify:
    cd {{frontend_dir}} && netlify deploy --prod

# Déploie sur Netlify (mode preview)
deploy-netlify-preview:
    cd {{frontend_dir}} && netlify deploy

# Pousse vers GitHub (déclenche les déploiements automatiques)
deploy-push:
    git push origin main

# ============================================================================
# BASE DE DONNÉES
# ============================================================================

# Ouvre la base SQLite dans le shell
db-shell:
    sqlite3 data/nexusdl.db

# Backup de la base de données
db-backup:
    #!/usr/bin/env bash
    BACKUP_FILE="data/nexusdl-backup-$(date +%Y%m%d-%H%M%S).db"
    cp data/nexusdl.db "$BACKUP_FILE"
    echo "✅ Backup créé : $BACKUP_FILE"

# Restaure la base de données depuis un backup
db-restore BACKUP_FILE:
    cp {{BACKUP_FILE}} data/nexusdl.db
    echo "✅ Base restaurée depuis {{BACKUP_FILE}}"

# ============================================================================
# DOCUMENTATION
# ============================================================================

# Génère la documentation
docs:
    cd {{docs_dir}} && {{python}} -m mkdocs build

# Sert la documentation en local
docs-serve:
    cd {{docs_dir}} && {{python}} -m mkdocs serve

# ============================================================================
# NETTOYAGE
# ============================================================================

# Nettoie tous les fichiers temporaires
clean:
    #!/usr/bin/env bash
    echo "🧹 Nettoyage des fichiers Python..."
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name "*.pyo" -delete 2>/dev/null || true
    
    echo "🧹 Nettoyage des fichiers Node.js..."
    rm -rf {{frontend_dir}}/node_modules 2>/dev/null || true
    rm -rf {{frontend_dir}}/.next 2>/dev/null || true
    rm -rf {{frontend_dir}}/out 2>/dev/null || true
    
    echo "🧹 Nettoyage des fichiers de build..."
    rm -rf dist/ build/ *.egg-info 2>/dev/null || true
    rm -rf htmlcov/ coverage.xml .coverage 2>/dev/null || true
    
    echo "✅ Nettoyage terminé !"

# Nettoie uniquement Python
clean-python:
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    rm -rf dist/ build/ *.egg-info htmlcov/ coverage.xml .coverage 2>/dev/null || true

# Nettoie uniquement le frontend
clean-frontend:
    rm -rf {{frontend_dir}}/node_modules {{frontend_dir}}/.next {{frontend_dir}}/out 2>/dev/null || true

# ============================================================================
# INFORMATIONS
# ============================================================================

# Affiche la version de NexusDL
version:
    {{python}} -c "import nexusdl; print(f'NexusDL v{nexusdl.__version__}')"

# Affiche les informations du système
info:
    #!/usr/bin/env bash
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                    NEXUSDL - System Info                        ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║ Python    : $({{python}} --version)"
    echo "║ Node.js   : $({{node}} --version 2>/dev/null || echo 'not installed')"
    echo "║ pnpm      : $({{pnpm}} --version 2>/dev/null || echo 'not installed')"
    echo "║ Docker    : $({{docker}} --version 2>/dev/null || echo 'not installed')"
    echo "║ just      : $(just --version 2>/dev/null || echo 'not installed')"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║ Backend   : {{backend_url}}"
    echo "║ Frontend  : {{frontend_url}}"
    echo "║ Railway   : {{railway_url}}"
    echo "║ Netlify   : {{netlify_url}}"
    echo "╚══════════════════════════════════════════════════════════════════╝"

# Affiche l'état des services
status:
    #!/usr/bin/env bash
    echo "🔍 Vérification de l'état des services..."
    echo ""
    
    # Backend
    echo -n "Backend  : "
    if curl -sf {{backend_url}}/health > /dev/null 2>&1; then
        echo "✅ Running"
    else
        echo "❌ Not running"
    fi
    
    # Frontend
    echo -n "Frontend : "
    if curl -sf {{frontend_url}} > /dev/null 2>&1; then
        echo "✅ Running"
    else
        echo "❌ Not running"
    fi
    
    # Railway
    echo -n "Railway  : "
    if curl -sf {{railway_url}}/health > /dev/null 2>&1; then
        echo "✅ Online"
    else
        echo "❌ Offline"
    fi

# ============================================================================
# UTILITAIRES
# ============================================================================

# Génère une nouvelle clé secrète
generate-secret:
    {{python}} -c "import secrets; print(secrets.token_urlsafe(64))"

# Génère une nouvelle clé API
generate-api-key:
    {{python}} -c "import secrets; print('nxd_' + secrets.token_hex(32))"

# Vérifie les dépendances Python
check-deps:
    {{pip}} list --outdated
    {{pip}} audit 2>/dev/null || echo "pip-audit non installé"

# Vérifie les dépendances Node.js
check-deps-frontend:
    cd {{frontend_dir}} && {{pnpm}} audit

# Met à jour toutes les dépendances
update-deps:
    {{pip}} install --upgrade -r requirements.txt
    cd {{frontend_dir}} && {{pnpm}} update

# ============================================================================
# HELPERS
# ============================================================================

# Ouvre la documentation API dans le navigateur
open-docs:
    #!/usr/bin/env bash
    if command -v xdg-open > /dev/null; then
        xdg-open {{backend_url}}/docs
    elif command -v open > /dev/null; then
        open {{backend_url}}/docs
    else
        echo "Ouvre {{backend_url}}/docs dans ton navigateur"
    fi

# Ouvre le frontend dans le navigateur
open-frontend:
    #!/usr/bin/env bash
    if command -v xdg-open > /dev/null; then
        xdg-open {{frontend_url}}
    elif command -v open > /dev/null; then
        open {{frontend_url}}
    else
        echo "Ouvre {{frontend_url}} dans ton navigateur"
    fi

# ============================================================================
# ALIASES
# ============================================================================

# Alias courts pour les commandes fréquentes
alias d := dev
alias db := dev-backend
alias df := dev-frontend
alias t := test
alias tc := test-cov
alias l := lint
alias b := build
alias c := clean
alias i := install

# ============================================================================
# PHONY
# ============================================================================

# Marque toutes les recettes comme phony (pas de fichiers du même nom)
[phony]
default
install
install-backend
install-frontend
install-playwright
install-hooks
dev-backend
run-backend
dev-cli
dev-gui
shell
dev-frontend
run-frontend
storybook
dev
test
test-cov
test-fast
test-integration
test-e2e
test-watch
lint
lint-python
lint-frontend
fix
fix-python
fix-frontend
build
build-python
build-frontend
analyze
docker-build
docker-build-backend
docker-build-web
docker-build-gui
docker-run-backend
docker-run-web
docker-up
docker-down
docker-logs
deploy-railway
deploy-netlify
deploy-netlify-preview
deploy-push
db-shell
db-backup
db-restore
docs
docs-serve
clean
clean-python
clean-frontend
version
info
status
generate-secret
generate-api-key
check-deps
check-deps-frontend
update-deps
open-docs
open-frontend
