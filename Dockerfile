# ============================================================================
# NEXUSDL - Dockerfile Universel Complet
# Compatible : Railway, Render, Fly.io, AWS, VPS, Docker local
# ============================================================================

# ============================================================================
# STAGE 1 : BUILDER (installation des dépendances)
# ============================================================================
FROM python:3.12-slim AS builder

# Variables système pour éviter les caches et les prompts interactifs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Dépendances système pour COMPILER les packages Python (gcc, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copier les fichiers de dépendances EN PREMIER (pour le cache Docker)
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/

# Installer toutes les dépendances Python
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    pip install -e .

# Installer Playwright + Chromium + TOUTES les dépendances système
RUN playwright install --with-deps chromium

# ============================================================================
# STAGE 2 : RUNTIME (image finale optimisée)
# ============================================================================
FROM python:3.12-slim

# Variables d'environnement système
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUSDL_ENV=production \
    PORT=8000 \
    HOST=0.0.0.0 \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ============================================================================
# DÉPENDANCES SYSTÈME OBLIGATOIRES POUR PLAYWRIGHT AU RUNTIME
# (Sans ces libs, Playwright crash au démarrage)
# ============================================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Utilitaires de base
    curl \
    ca-certificates \
    # Dépendances Playwright/Chromium OBLIGATOIRES
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
    # Fonts pour le rendu
    fonts-liberation \
    fonts-dejavu-core \
    # Utilitaires pour healthcheck
    && rm -rf /var/lib/apt/lists/*

# ============================================================================
# COPIER L'ENVIRONNEMENT PYTHON DEPUIS LE BUILDER
# ============================================================================
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright
COPY --from=builder /app /app

# ============================================================================
# CRÉER LES DOSSIERS NÉCESSAIRES
# ============================================================================
RUN mkdir -p /app/data /app/data/downloads /app/data/library \
             /app/logs /app/cache /app/config \
    && chmod -R 777 /app/data /app/logs /app/cache /app/config

# ============================================================================
# HEALTHCHECK (obligatoire pour Railway/Render)
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
CMD ["sh", "-c", "uvicorn nexusdl.interfaces.web.backend.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
