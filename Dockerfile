# ============================================================================
# NEXUSDL BACKEND - Dockerfile pour Railway
# ============================================================================

# Stage 1 : Build
FROM python:3.12-slim as builder

WORKDIR /app

# Installer les dépendances système
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copier les fichiers de dépendances
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Installer le package
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[all]"

# Stage 2 : Runtime
FROM python:3.12-slim

WORKDIR /app

# Installer les dépendances système minimales
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier l'application installée depuis le builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Créer les répertoires nécessaires
RUN mkdir -p /app/data /app/logs /app/cache

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUSDL_ENV=production \
    PORT=8000

# Exposer le port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Commande de démarrage
CMD ["sh", "-c", "uvicorn nexusdl.interfaces.web.backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
