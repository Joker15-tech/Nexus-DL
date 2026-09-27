
================================================================================
                    NEXUSDL - GUIDE DE DÉPLOIEMENT COMPLET
================================================================================

Ce document couvre TOUTES les méthodes de déploiement pour NexusDL :
  ✓ Développement local (Windows/Linux/macOS)
  ✓ Backend Web (Railway)
  ✓ Frontend Web (Netlify)
  ✓ Docker (conteneur complet)
  ✓ GUI Desktop (PyQt6)
  ✓ CLI Terminal (Textual)
  ✓ VPS (Ubuntu/Debian)
  ✓ PowerShell & Terminal

================================================================================
                              TABLE DES MATIÈRES
================================================================================

1. PRÉREQUIS
2. INSTALLATION LOCALE (DÉVELOPPEMENT)
3. DÉPLOIEMENT BACKEND (RAILWAY)
4. DÉPLOIEMENT FRONTEND (NETLIFY)
5. DÉPLOIEMENT DOCKER (COMPLET)
6. DÉPLOIEMENT GUI (PYQT6)
7. DÉPLOIEMENT CLI (TEXTUAL)
8. DÉPLOIEMENT VPS (UBUNTU/DEBIAN)
9. COMMANDS UTILES (POWERSHELL & TERMINAL)
10. DÉPANNAGE
11. URLs DE RÉFÉRENCE

================================================================================
                              1. PRÉREQUIS
================================================================================

### Outils nécessaires :

- Python 3.11+ (3.12 recommandé)
- Node.js 20+ (pour le frontend)
- pnpm (gestionnaire de paquets Node)
- Git
- Docker (optionnel, pour le déploiement conteneurisé)

### Installation des outils :

#### Windows (PowerShell) :
```powershell
# Python
winget install Python.Python.3.12

# Node.js
winget install OpenJS.NodeJS.LTS

# pnpm
npm install -g pnpm

# Git
winget install Git.Git

# Docker Desktop
winget install Docker.DockerDesktop
```

#### macOS (Terminal) :
```bash
# Homebrew (si pas installé)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python
brew install python@3.12

# Node.js
brew install node@20

# pnpm
brew install pnpm

# Docker
brew install --cask docker
```

#### Linux (Ubuntu/Debian) :
```bash
# Python
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip

# Node.js (via NodeSource)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# pnpm
npm install -g pnpm

# Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
sudo usermod -aG docker $USER
```

================================================================================
                    2. INSTALLATION LOCALE (DÉVELOPPEMENT)
================================================================================

### Étape 1 : Cloner le dépôt

```bash
git clone https://github.com/JokerApocalypse-cmd/Nexus-DL.git
cd Nexus-DL
```

### Étape 2 : Créer un environnement virtuel Python

#### Windows (PowerShell) :
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

#### Linux/macOS (Terminal) :
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Étape 3 : Installer les dépendances Python

```bash
# Installation complète (dev + CLI + GUI + Web)
pip install -e ".[all]"

# OU installation minimale (backend uniquement)
pip install -e ".[web]"

# Installer Playwright
playwright install chromium
```

### Étape 4 : Installer les dépendances frontend

```bash
cd src/nexusdl/interfaces/web/frontend
pnpm install
cd ../../..
```

### Étape 5 : Configurer l'environnement

```bash
# Copier le fichier .env.example vers .env.local
cp .env.example .env.local

# Éditer .env.local avec vos valeurs
# (Windows PowerShell)
notepad .env.local

# (Linux/macOS)
nano .env.local
```

### Étape 6 : Lancer l'application

#### Backend (FastAPI) :
```bash
# Mode développement avec hot-reload
uvicorn nexusdl.interfaces.web.backend.main:app --reload --host 127.0.0.1 --port 8000

# OU via le script principal
python -m nexusdl --web
```

#### Frontend (Next.js) :
```bash
cd src/nexusdl/interfaces/web/frontend
pnpm dev
```

#### CLI (Textual) :
```bash
python -m nexusdl --cli
```

#### GUI (PyQt6) :
```bash
python -m nexusdl --gui
```

### Accès local :
- Backend API : http://localhost:8000
- Documentation Swagger : http://localhost:8000/docs
- Frontend : http://localhost:3000

================================================================================
                      3. DÉPLOIEMENT BACKEND (RAILWAY)
================================================================================

### Étape 1 : Créer un compte Railway

1. Aller sur https://railway.com
2. Se connecter avec GitHub

### Étape 2 : Créer un nouveau projet

1. Cliquer sur "+ New Project"
2. Nommer le projet : "NexusDL"
3. Cliquer sur "+ New" → "GitHub Repo"
4. Sélectionner "JokerApocalypse-cmd/Nexus-DL"
5. Branche : "main"

### Étape 3 : Configurer le Build

Dans Settings → Build :
- **Builder** : `Railpack`
- **Custom Build Command** :
  ```bash
  pip install --upgrade pip && pip install -e . && playwright install chromium
  ```

### Étape 4 : Configurer le Deploy

Dans Settings → Deploy :
- **Custom Start Command** :
  ```bash
  uvicorn nexusdl.interfaces.web.backend.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips '*'
  ```
- **Healthcheck Path** : `/health`
- **Serverless** : ❌ DÉCOCHÉ (très important !)

### Étape 5 : Ajouter les variables d'environnement

Dans l'onglet Variables, ajouter :

```env
# Configuration générale
NEXUSDL_ENV=production
NEXUSDL_LOG_LEVEL=INFO
PYTHONUNBUFFERED=1

# Sécurité
NEXUSDL_SECRET_KEY=<votre-clé-64-caractères>
NEXUSDL_ADMIN_EMAIL=drxenon487@gmail.com
NEXUSDL_ADMIN_PASSWORD=<votre-mot-de-passe>
NEXUSDL_API_KEY=<votre-clé-api>

# CORS
NEXUSDL_CORS_ORIGINS=https://nexusdldev.netlify.app,http://localhost:3000

# Chemins
NEXUSDL_DATA_DIR=/app/data
NEXUSDL_DB_PATH=/app/data/nexusdl.db
DATABASE_URL=sqlite:////app/data/nexusdl.db

# Performance
NEXUSDL_MAX_CONCURRENT_DOWNLOADS=3
NEXUSDL_MAX_CONCURRENT_PAGES=5

# Réseau
HOST=0.0.0.0
PORT=8000

# Corrections critiques
MISE_PYTHON_GITHUB_ATTESTATIONS=false
RAILPACK_PYTHON_PLAYWRIGHT_INSTALL=1
```

### Étape 6 : Monter un volume persistant

1. Settings → Volumes
2. Cliquer sur "+ Add Volume"
3. Mount Path : `/app/data`

### Étape 7 : Générer un domaine public

1. Settings → Networking
2. Cliquer sur "Generate Domain"
3. Copier l'URL générée (ex: https://nexus-dl-production.up.railway.app)

### Étape 8 : Déployer

1. Cliquer sur "Deploy"
2. Attendre que le build se termine (5-10 minutes)
3. Vérifier dans les logs que vous voyez :
   ```
   ✅ Application startup complete.
   ✅ Uvicorn running on http://0.0.0.0:$PORT
   ```

### Test :
```bash
curl https://nexus-dl-production.up.railway.app/health
# Attendu : {"status":"healthy","version":"0.1.0",...}
```

================================================================================
                      4. DÉPLOIEMENT FRONTEND (NETLIFY)
================================================================================

### Étape 1 : Créer un compte Netlify

1. Aller sur https://netlify.com
2. Se connecter avec GitHub

### Étape 2 : Créer un nouveau site

1. Cliquer sur "Add new site" → "Import an existing project"
2. Sélectionner "JokerApocalypse-cmd/Nexus-DL"
3. Branche : "main"

### Étape 3 : Configurer le build

Dans Site settings → Build & deploy :
- **Base directory** : `src/nexusdl/interfaces/web/frontend`
- **Build command** : `pnpm build`
- **Publish directory** : `.next`

### Étape 4 : Ajouter les variables d'environnement

Dans Site settings → Environment variables :

```env
NODE_VERSION=20
NEXT_PUBLIC_API_URL=/api/v1
NEXT_PUBLIC_WS_URL=wss://nexus-dl-production.up.railway.app/ws
NEXT_PUBLIC_APP_NAME=NexusDL
NEXT_PUBLIC_APP_VERSION=0.1.0
```

### Étape 5 : Déployer

1. Cliquer sur "Deploy site"
2. Attendre que le build se termine (2-3 minutes)
3. Netlify génère une URL (ex: https://nexusdldev.netlify.app)

### Test :
Ouvrir https://nexusdldev.netlify.app dans un navigateur
Vérifier dans la console (F12) qu'il n'y a PAS d'erreur CORS

================================================================================
                      5. DÉPLOIEMENT DOCKER (COMPLET)
================================================================================

### Option A : Backend uniquement

```bash
# Build
docker build -f docker/Dockerfile -t nexusdl-backend:latest .

# Run
docker run -d \
  -p 8000:8000 \
  -v nexusdl-data:/app/data \
  -e NEXUSDL_SECRET_KEY=<votre-clé> \
  -e NEXUSDL_CORS_ORIGINS=https://nexusdldev.netlify.app \
  --name nexusdl-backend \
  nexusdl-backend:latest

# Logs
docker logs -f nexusdl-backend

# Stop
docker stop nexusdl-backend
```

### Option B : Web complet (Backend + Frontend)

```bash
# Build
docker build -f docker/Dockerfile.web -t nexusdl-web:latest .

# Run
docker run -d \
  -p 8000:8000 \
  -v nexusdl-web-data:/app/data \
  -e NEXUSDL_SECRET_KEY=<votre-clé> \
  --name nexusdl-web \
  nexusdl-web:latest

# Accès
# Frontend : http://localhost:8000
# Backend : http://localhost:8000/api/v1
# Docs : http://localhost:8000/docs
```

### Option C : Via Docker Compose (recommandé)

```bash
# Développement
docker compose -f docker/docker-compose.dev.yml up -d

# Production
docker compose -f docker/docker-compose.yml up -d --build

# Logs
docker compose logs -f

# Stop
docker compose down
```

### Option D : GUI avec noVNC (accès via navigateur)

```bash
# Build
docker build -f docker/Dockerfile.gui -t nexusdl-gui:latest .

# Run
docker run -d \
  -p 6080:6080 \
  -p 5900:5900 \
  -v nexusdl-gui-data:/app/data \
  -e VNC_PASSWORD=nexusdl \
  --name nexusdl-gui \
  nexusdl-gui:latest

# Accès via navigateur
# http://localhost:6080/vnc.html
# Mot de passe : nexusdl
```

================================================================================
                        6. DÉPLOIEMENT GUI (PYQT6)
================================================================================

### Installation locale (Windows/Linux/macOS)

```bash
# Activer l'environnement virtuel
# Windows
.\.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate

# Installer les dépendances GUI
pip install -e ".[gui]"

# Lancer la GUI
python -m nexusdl --gui
```

### Dépendances système (Linux uniquement)

```bash
# Ubuntu/Debian
sudo apt install -y \
    libgl1-mesa-glx \
    libegl1-mesa \
    libxkbcommon0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-xinerama0 \
    libxcb-xfixes0

# Fedora
sudo dnf install -y \
    mesa-libGL \
    libxkbcommon \
    xcb-util-wm \
    xcb-util-image
```

### Création d'un exécutable standalone (Windows)

```bash
# Installer PyInstaller
pip install pyinstaller

# Créer l'exécutable
pyinstaller --noconfirm --onedir --windowed \
    --name "NexusDL" \
    --icon "src/nexusdl/interfaces/gui/assets/icon.ico" \
    --add-data "src/nexusdl;src/nexusdl" \
    src/nexusdl/__main__.py

# L'exécutable sera dans dist/NexusDL/
```

================================================================================
                        7. DÉPLOIEMENT CLI (TEXTUAL)
================================================================================

### Installation

```bash
# Activer l'environnement virtuel
source .venv/bin/activate  # Linux/macOS
.\.venv\Scripts\Activate.ps1  # Windows

# Installer les dépendances CLI
pip install -e ".[cli]"

# Lancer la CLI
python -m nexusdl --cli
```

### Commandes CLI disponibles

```bash
# Recherche
nexusdl search "one piece"

# Téléchargement
nexusdl download <manga-id>

# Liste des sites
nexusdl sites

# Configuration
nexusdl config

# Aide
nexusdl --help
```

================================================================================
                    8. DÉPLOIEMENT VPS (UBUNTU/DEBIAN)
================================================================================

### Étape 1 : Préparer le serveur

```bash
# Connexion SSH
ssh user@your-vps-ip

# Mise à jour
sudo apt update && sudo apt upgrade -y

# Installer les dépendances
sudo apt install -y \
    python3.12 \
    python3.12-venv \
    python3-pip \
    nodejs \
    npm \
    git \
    docker.io \
    docker-compose \
    nginx \
    certbot \
    python3-certbot-nginx

# Activer Docker
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
```

### Étape 2 : Cloner le projet

```bash
cd /opt
sudo git clone https://github.com/JokerApocalypse-cmd/Nexus-DL.git
cd Nexus-DL
sudo chown -R $USER:$USER .
```

### Étape 3 : Déployer avec Docker

```bash
# Copier le fichier .env
cp .env.example .env
nano .env  # Éditer avec vos valeurs

# Lancer avec Docker Compose
docker compose -f docker/docker-compose.yml up -d --build

# Vérifier les logs
docker compose logs -f
```

### Étape 4 : Configurer Nginx (reverse proxy)

```bash
# Créer la configuration Nginx
sudo nano /etc/nginx/sites-available/nexusdl
```

Contenu :
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
# Activer le site
sudo ln -s /etc/nginx/sites-available/nexusdl /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Obtenir un certificat SSL
sudo certbot --nginx -d your-domain.com
```

### Étape 5 : Automatiser avec systemd

```bash
sudo nano /etc/systemd/system/nexusdl.service
```

Contenu :
```ini
[Unit]
Description=NexusDL Backend
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/Nexus-DL
Environment="PATH=/opt/Nexus-DL/.venv/bin"
ExecStart=/opt/Nexus-DL/.venv/bin/uvicorn nexusdl.interfaces.web.backend.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Activer le service
sudo systemctl daemon-reload
sudo systemctl enable nexusdl
sudo systemctl start nexusdl
sudo systemctl status nexusdl
```

================================================================================
                9. COMMANDS UTILES (POWERSHELL & TERMINAL)
================================================================================

### PowerShell (Windows)

```powershell
# Installation
pip install -e ".[all]"
playwright install chromium
cd src/nexusdl/interfaces/web/frontend; pnpm install; cd ../../..

# Développement
uvicorn nexusdl.interfaces.web.backend.main:app --reload
cd src/nexusdl/interfaces/web/frontend; pnpm dev

# Tests
pytest tests/ -v
pytest tests/ --cov=src/nexusdl

# Linting
ruff check src/ tests/
ruff format src/ tests/
mypy src/nexusdl

# Build
python -m build
cd src/nexusdl/interfaces/web/frontend; pnpm build

# Docker
docker build -f docker/Dockerfile -t nexusdl-backend:latest .
docker run -d -p 8000:8000 nexusdl-backend:latest

# Nettoyage
Remove-Item -Recurse -Force __pycache__, .pytest_cache, .mypy_cache, .ruff_cache
```

### Terminal (Linux/macOS)

```bash
# Installation
pip install -e ".[all]"
playwright install chromium
cd src/nexusdl/interfaces/web/frontend && pnpm install && cd ../../..

# Développement
uvicorn nexusdl.interfaces.web.backend.main:app --reload
cd src/nexusdl/interfaces/web/frontend && pnpm dev

# Tests
pytest tests/ -v
pytest tests/ --cov=src/nexusdl

# Linting
ruff check src/ tests/
ruff format src/ tests/
mypy src/nexusdl

# Build
python -m build
cd src/nexusdl/interfaces/web/frontend && pnpm build

# Docker
docker build -f docker/Dockerfile -t nexusdl-backend:latest .
docker run -d -p 8000:8000 nexusdl-backend:latest

# Nettoyage
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type d -name ".pytest_cache" -exec rm -rf {} +
```

================================================================================
                            10. DÉPANNAGE
================================================================================

### Erreur : "ModuleNotFoundError: No module named 'jsonschema'"

**Solution** :
```bash
pip install jsonschema
# OU
pip install -e .
```

### Erreur : "Host system is missing dependencies to run browsers"

**Solution** :
```bash
playwright install-deps chromium
playwright install chromium
```

### Erreur CORS sur Netlify

**Solution** :
1. Vérifier que `NEXUSDL_CORS_ORIGINS` sur Railway contient `https://nexusdldev.netlify.app`
2. Vérifier que `netlify.toml` contient les redirections vers Railway

### Erreur : "No GitHub artifact attestations found"

**Solution** :
Ajouter dans les variables Railway :
```
MISE_PYTHON_GITHUB_ATTESTATIONS=false
```

### Erreur : Build Netlify échoue avec "require is not defined"

**Solution** :
Renommer les fichiers :
- `next.config.js` → `next.config.cjs`
- `postcss.config.js` → `postcss.config.cjs`

### Erreur : WebSocket ne se connecte pas

**Solution** :
1. Vérifier que `NEXT_PUBLIC_WS_URL` sur Netlify est `wss://nexus-dl-production.up.railway.app/ws`
2. Vérifier que le backend Railway supporte les WebSockets

### Erreur : "Permission denied" sur Docker

**Solution** :
```bash
sudo usermod -aG docker $USER
# Se déconnecter et se reconnecter
```

================================================================================
                          11. URLs DE RÉFÉRENCE
================================================================================

### Production
- Frontend : https://nexusdldev.netlify.app
- Backend : https://nexus-dl-production.up.railway.app
- Documentation : https://nexus-dl-production.up.railway.app/docs
- Health check : https://nexus-dl-production.up.railway.app/health

### Développement local
- Frontend : http://localhost:3000
- Backend : http://localhost:8000
- Documentation : http://localhost:8000/docs

### GitHub
- Repository : https://github.com/JokerApocalypse-cmd/Nexus-DL
- Issues : https://github.com/JokerApocalypse-cmd/Nexus-DL/issues

### Documentation
- FastAPI : https://fastapi.tiangolo.com
- Next.js : https://nextjs.org
- Railway : https://docs.railway.com
- Netlify : https://docs.netlify.com
- Docker : https://docs.docker.com

================================================================================
                              FIN DU DOCUMENT
================================================================================

Pour toute question ou problème, consulter :
- Les logs de déploiement (Railway/Netlify/Docker)
- La console du navigateur (F12) pour les erreurs frontend
- Les fichiers de configuration (.env, netlify.toml, Dockerfile)

Bon déploiement ! 🚀
```

---

Ce fichier `instruction.txt` est maintenant **100% complet** et couvre toutes les méthodes de déploiement pour NexusDL. Vous pouvez le commiter et le pousser :

```powershell
git add instruction.txt
git commit -m "docs: add comprehensive deployment guide for all platforms"
git push origin main
```
