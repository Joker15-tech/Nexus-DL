# ============================================================================
# NEXUSDL - Dockerfile pour Railway (avec Playwright fonctionnel)
# ============================================================================

# Stage 1 : Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Installer les dépendances système pour la compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copier les fichiers de dépendances
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
COPY README.md ./

# Installer le package et ses dépendances
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Installer Playwright et ses dépendances système + navigateurs
RUN playwright install-deps chromium && \
    playwright install chromium

# Stage 2 : Runtime (image finale plus légère)
FROM python:3.12-slim

WORKDIR /app

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUSDL_ENV=production \
    PORT=8000 \
    # Playwright doit savoir où sont les navigateurs
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Installer les dépendances système MINIMALES pour faire tourner Playwright au runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    # Dépendances Playwright runtime
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
    libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

# Copier l'environnement Python et les navigateurs depuis le builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright
COPY --from=builder /app /app

# Créer les répertoires nécessaires
RUN mkdir -p /app/data /app/logs /app/cache && \
    chmod -R 777 /app/data /app/logs /app/cache

# Exposer le port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Commande de démarrage
CMD ["sh", "-c", "uvicorn nexusdl.interfaces.web.backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
