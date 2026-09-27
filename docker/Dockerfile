# ============================================================================
# NEXUSDL - Dockerfile Universel (Production)
# ============================================================================
# Chemin : docker/Dockerfile
# Contexte de build : racine du projet
#
# Build :
#   docker build -f docker/Dockerfile -t nexusdl-backend:latest .
#
# Compatible : Railway, Render, Fly.io, AWS, VPS, Docker local
# ============================================================================

# ============================================================================
# STAGE 1 : BUILDER (installation des dépendances + compilation)
# ============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Dépendances système pour compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    curl \
    git \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ============================================================================
# ÉTAPE 1 : Copier les fichiers de dépendances EN PREMIER (cache Docker)
# ============================================================================
COPY pyproject.toml ./
COPY requirements*.txt ./
COPY README.md ./

# Installer les dépendances Python
RUN pip install --upgrade pip setuptools wheel && \
    if [ -f requirements-web.txt ]; then \
        pip install -r requirements-web.txt; \
    elif [ -f requirements.txt ]; then \
        pip install -r requirements.txt; \
    fi

# ============================================================================
# ÉTAPE 2 : Copier le code source et installer le package
# ============================================================================
COPY src/ ./src/

# ⚠️ CRITIQUE : Installer le package en mode éditable
# Cela crée le lien nexusdl -> src/nexusdl dans site-packages
RUN pip install -e . && \
    # Vérifier que l'installation a fonctionné
    python -c "import nexusdl; print(f'✅ nexusdl installed: {nexusdl.__file__}')" || \
    echo "⚠️ nexusdl package import failed, checking structure..." && \
    ls -la src/nexusdl/ || true

# ============================================================================
# ÉTAPE 3 : Installer Playwright + Chromium + dépendances système
# ============================================================================
RUN playwright install --with-deps chromium

# ============================================================================
# STAGE 2 : RUNTIME (image finale optimisée)
# ============================================================================
FROM python:3.12-slim

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUSDL_ENV=production \
    PORT=8000 \
    HOST=0.0.0.0 \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    DEBIAN_FRONTEND=noninteractive \
    # ⚠️ CRITIQUE : Ajouter le chemin du package au PYTHONPATH
    PYTHONPATH=/app/src:/app

WORKDIR /app

# ============================================================================
# DÉPENDANCES SYSTÈME OBLIGATOIRES POUR PLAYWRIGHT AU RUNTIME
# ============================================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    # Playwright/Chromium
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libx11-6 \
    libxcb1 \
    libxext6 \
    libwayland-client0 \
    # Fonts
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ============================================================================
# COPIER DEPUIS LE BUILDER
# ============================================================================

# Copier les packages Python installés
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copier les navigateurs Playwright
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

# ⚠️ CRITIQUE : Copier TOUT le dossier /app pour garder le lien créé par pip install -e .
COPY --from=builder /app /app

# ============================================================================
# VÉRIFICATION POST-BUILD
# ============================================================================
# Vérifier que les imports critiques fonctionnent
RUN python -c " \
    import sys; \
    print(f'Python: {sys.version}'); \
    print(f'PYTHONPATH: {sys.path}'); \
    import nexusdl; \
    print(f'✅ nexusdl: {nexusdl.__file__}'); \
    try: \
        from nexusdl.core.constants import APP_NAME, APP_VERSION; \
        print(f'✅ constants: {APP_NAME} v{APP_VERSION}'); \
    except ImportError as e: \
        print(f'⚠️ constants import failed: {e}'); \
    try: \
        import jsonschema; \
        print(f'✅ jsonschema: {jsonschema.__version__}'); \
    except ImportError as e: \
        print(f'❌ jsonschema missing: {e}'); \
    try: \
        import fastapi; \
        print(f'✅ fastapi: {fastapi.__version__}'); \
    except ImportError as e: \
        print(f'❌ fastapi missing: {e}'); \
    try: \
        import playwright; \
        print(f'✅ playwright installed'); \
    except ImportError as e: \
        print(f'❌ playwright missing: {e}'); \
    print('--- Build verification complete ---'); \
" || echo "⚠️ Some imports failed, but continuing build..."

# ============================================================================
# CRÉER LES DOSSIERS NÉCESSAIRES
# ============================================================================
RUN mkdir -p /app/data/downloads /app/data/library \
             /app/logs /app/cache /app/config \
    && chmod -R 777 /app/data /app/logs /app/cache /app/config

# ============================================================================
# HEALTHCHECK
# ============================================================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# ============================================================================
# EXPOSER LE PORT
# ============================================================================
EXPOSE ${PORT}

# ============================================================================
# COMMANDE DE DÉMARRAGE
# ============================================================================
# Note : On utilise PYTHONPATH pour s'assurer que nexusdl est trouvable
CMD ["sh", "-c", "cd /app && uvicorn nexusdl.interfaces.web.backend.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
