# syntax=docker/dockerfile:1.7
# ============================================================================
#  NexusDL — Dockerfile (image backend générique)
#  Utilisée par : worker, cli, migrate, test, shell, web-backend-dev
#
#  Contenu :
#    - Python 3.12 + venv isolé
#    - Package nexusdl (extras: core, browsers, cli)
#    - Playwright + chromium (pour bypass Cloudflare côté worker)
#    - Scripts de migration et de validation
#    - Entrypoint universel (docker/entrypoint.sh)
#
#  NE CONTIENT PAS :
#    - Le frontend Next.js (voir Dockerfile.web)
#    - Nginx (voir l'image nginx:alpine officielle)
#    - uvicorn (à installer via l'extra [web] si besoin — rare hors web-backend)
#
#  Multi-stage :
#    stage 1 : python-builder  → compile les wheels + crée le venv + télécharge chromium
#    stage 2 : runtime         → image finale minimale non-root
#
#  Build :
#    docker build -f docker/Dockerfile \
#      --build-arg VERSION=0.1.0 \
#      --build-arg BUILD_PROFILE=release \
#      --build-arg INSTALL_PLAYWRIGHT=true \
#      -t nexusdl:0.1.0 .
#
#  Run (worker) :
#    docker run --rm \
#      -e NEXUSDL_ROLE=worker \
#      -e NEXUSDL_REDIS_URL=redis://redis:6379/0 \
#      -v nexusdl-config:/config \
#      -v nexusdl-data:/data \
#      nexusdl:0.1.0
#
#  Run (CLI one-shot) :
#    docker run --rm -it \
#      -v nexusdl-config:/config \
#      -v nexusdl-data:/data \
#      nexusdl:0.1.0 \
#      python -m nexusdl download --site mangadex --url https://...
#
#  Variables de build :
#    VERSION              → version injectée (défaut: 0.1.0)
#    BUILD_PROFILE        → release | dev (défaut: release)
#    PYTHON_VERSION       → version Python (défaut: 3.12)
#    INSTALL_PLAYWRIGHT   → true | false (défaut: true)
#    ENABLE_DEV_TOOLS     → true | false (défaut: false) — inclut ruff, mypy, pytest
# ============================================================================

# ============================================================================
#  Stage 1 : python-builder — compilation + venv + Playwright
# ============================================================================
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS python-builder

ARG VERSION=0.1.0
ARG BUILD_PROFILE=release
ARG INSTALL_PLAYWRIGHT=true
ARG ENABLE_DEV_TOOLS=false

# --- Dépendances de build ----------------------------------------------------
# Nécessaires uniquement pour compiler les extensions C des dépendances
# (cryptography, pillow, lxml, aiohttp, cffi...). Le runtime final n'a
# PAS besoin de gcc/make.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        gcc \
        git \
        libffi-dev \
        libjpeg-dev \
        liblzma-dev \
        libpng-dev \
        libssl-dev \
        libwebp-dev \
        libxml2-dev \
        libxslt1-dev \
        pkg-config \
        zlib1g-dev \
    ; \
    rm -rf /var/lib/apt/lists/*

# --- Installation de uv (rapide, déterministe) -------------------------------
RUN set -eux; \
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    mv /root/.local/bin/uv /usr/local/bin/uv; \
    mv /root/.local/bin/uvx /usr/local/bin/uvx 2>/dev/null || true; \
    uv --version

# --- Création du venv --------------------------------------------------------
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python -m venv ${VIRTUAL_ENV}

# --- Copie des manifests (cache Docker optimal) ------------------------------
# Ne copie que ce qui est nécessaire à `pip install`, pas le code source.
# Résultat : un changement dans src/ ne réinvalide PAS l'installation des deps.
WORKDIR /build

COPY pyproject.toml ./
COPY README.md ./
COPY LICENSE* ./
COPY MANIFEST.in ./

# Le pyproject peut référencer version.py via hatchling — on le copie.
COPY src/nexusdl/__init__.py ./src/nexusdl/__init__.py
COPY src/nexusdl/version.py ./src/nexusdl/version.py

# --- Mise à jour de pip + setuptools + wheel ---------------------------------
RUN set -eux; \
    uv pip install --upgrade pip setuptools wheel

# --- Installation des dépendances --------------------------------------------
# On installe les extras [core,browsers] + [cli] (Textual/Typer/Rich).
# L'extra [web] (FastAPI/uvicorn) n'est PAS installé — c'est le rôle de
# Dockerfile.web. Si un service a besoin des deux, il utilise Dockerfile.web.
#
# L'extra [dev] est optionnel (ENABLE_DEV_TOOLS=true) — utile pour l'image
# `test` ou `shell` en dev.
RUN set -eux; \
    EXTRAS="core,browsers,cli"; \
    if [ "${ENABLE_DEV_TOOLS}" = "true" ]; then \
        EXTRAS="${EXTRAS},dev"; \
    fi; \
    uv pip install -e ".[${EXTRAS}]"

# --- Copie du code source complet --------------------------------------------
COPY src/ ./src/

# --- Réinstallation du package (editable, avec tout le code) -----------------
RUN set -eux; \
    uv pip install --no-deps -e .; \
    uv cache clean; \
    find ${VIRTUAL_ENV} -name "*.pyc" -delete; \
    find ${VIRTUAL_ENV} -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# --- Téléchargement des navigateurs Playwright -------------------------------
# Stockés dans /opt/playwright-browsers pour être copiés dans le runtime.
# Chromium uniquement par défaut (firefox/webkit = +300 MB chacun, inutile).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

RUN set -eux; \
    if [ "${INSTALL_PLAYWRIGHT}" = "true" ]; then \
        ${VIRTUAL_ENV}/bin/playwright install chromium; \
        # Nettoyage des archives ZIP téléchargées (inutiles après extraction)
        find ${PLAYWRIGHT_BROWSERS_PATH} -name "*.zip" -delete 2>/dev/null || true; \
        # Trace de version pour debug
        ${VIRTUAL_ENV}/bin/playwright --version > /opt/playwright-browsers/.version; \
    else \
        mkdir -p ${PLAYWRIGHT_BROWSERS_PATH}; \
        echo "Playwright désactivé lors du build" > ${PLAYWRIGHT_BROWSERS_PATH}/.disabled; \
    fi

# --- Trace de build ----------------------------------------------------------
RUN set -eux; \
    printf 'NexusDL v%s (profile=%s, playwright=%s, dev_tools=%s)\n' \
        "${VERSION}" "${BUILD_PROFILE}" "${INSTALL_PLAYWRIGHT}" "${ENABLE_DEV_TOOLS}" \
        > /build/.build-info

# ============================================================================
#  Stage 2 : runtime — image finale minimale
# ============================================================================
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG VERSION=0.1.0
ARG BUILD_PROFILE=release
ARG INSTALL_PLAYWRIGHT=true

# --- Labels OCI ---------------------------------------------------------------
LABEL org.opencontainers.image.title="NexusDL Backend"
LABEL org.opencontainers.image.description="Backend générique NexusDL (worker, CLI, migrations)"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.vendor="NexusDL"
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"
LABEL org.opencontainers.image.source="https://github.com/nexusdl/nexusdl"
LABEL org.opencontainers.image.documentation="https://github.com/nexusdl/nexusdl/blob/main/README.md"
LABEL org.opencontainers.image.base.name="python:${PYTHON_VERSION}-slim-bookworm"

# --- Variables d'environnement -----------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONIOENCODING=UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    NEXUSDL_ENV=production \
    NEXUSDL_ROLE=worker \
    NEXUSDL_CONFIG_DIR=/config \
    NEXUSDL_LIBRARY_DIR=/data \
    NEXUSDL_LOG_DIR=/logs \
    NEXUSDL_CACHE_DIR=/cache \
    NEXUSDL_VERSION=${VERSION} \
    TZ=UTC \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

# --- Dépendances système runtime ---------------------------------------------
# Seules les libs partagées strictement nécessaires à l'exécution.
# gcc/make/binutils sont absents (installation multi-stage terminée).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
        gosu \
        libjpeg62-turbo \
        liblzma5 \
        libpng16-16 \
        libssl3 \
        libwebp7 \
        libxml2 \
        libxslt1.1 \
        sqlite3 \
        tini \
        tzdata \
        unrar-free \
        unzip \
        zlib1g \
    ; \
    rm -rf /var/lib/apt/lists/*; \
    find /usr/share/doc -type f -delete; \
    find /usr/share/man -type f -delete

# --- Dépendances Playwright (libs système pour chromium) ---------------------
# `playwright install-deps chromium` installe ~30 libs (libnss3, libatk,
# libcups, libdrm, libgbm, etc.). On le fait ici pour ne pas polluer
# l'image si INSTALL_PLAYWRIGHT=false.
RUN set -eux; \
    if [ "${INSTALL_PLAYWRIGHT}" = "true" ]; then \
        python -m pip install --no-cache-dir "playwright>=1.45,<2"; \
        python -m playwright install-deps chromium; \
        python -m pip uninstall -y playwright; \
        # Nettoyage des caches pip résiduels
        find /root/.cache -type f -delete 2>/dev/null || true; \
    fi

# --- Copie du venv -----------------------------------------------------------
COPY --from=python-builder /opt/venv /opt/venv

# --- Copie des navigateurs Playwright ----------------------------------------
COPY --from=python-builder /opt/playwright-browsers /opt/playwright-browsers

# --- Copie du code source (utile pour migrations et scripts) -----------------
WORKDIR /app

COPY --from=python-builder /build/src /app/src
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
COPY scripts/ /app/scripts/
COPY config/ /app/config/
COPY pyproject.toml /app/pyproject.toml
COPY --from=python-builder /build/.build-info /app/.build-info

# --- Création de l'utilisateur non-root --------------------------------------
RUN set -eux; \
    groupadd --gid 1000 nexusdl; \
    useradd --uid 1000 --gid 1000 --shell /sbin/nologin --create-home --home-dir /home/nexusdl nexusdl; \
    # Dossiers de données + cache Playwright
    mkdir -p /config /data /logs /cache /home/nexusdl/.cache; \
    chown -R nexusdl:nexusdl /config /data /logs /cache /home/nexusdl; \
    # Le binaire playwright doit pouvoir écrire dans son cache au 1er lancement
    mkdir -p /opt/playwright-browsers; \
    chown -R nexusdl:nexusdl /opt/playwright-browsers; \
    # Script exécutable
    chmod +x /app/docker/entrypoint.sh; \
    chown -R nexusdl:nexusdl /app

# --- Healthcheck (basique : Python répond) -----------------------------------
# Le vrai healthcheck est défini par service dans docker-compose.yml
# (web-backend = /api/health ; worker = heartbeat fichier).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import nexusdl; print(nexusdl.__version__)" || exit 1

# --- Volumes (documentaire) --------------------------------------------------
VOLUME ["/config", "/data", "/logs", "/cache"]

# --- Entrypoint + CMD --------------------------------------------------------
# tini = PID 1 minimaliste (reap zombies, forward signaux).
# entrypoint.sh = bootstrap (configs, secrets, migrations, drop privileges).
# CMD par défaut = worker. À surcharger pour cli, migrate, shell.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD ["python", "-m", "nexusdl", "worker", "--foreground"]
