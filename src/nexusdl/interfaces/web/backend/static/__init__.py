"""Module helper pour le montage des fichiers statiques dans FastAPI.

Ce module fournit une fonction utilitaire pour monter facilement le dossier
`static/` dans une application FastAPI, avec configuration des headers
de cache, CORS, et types MIME.

**Utilisation** :
    >>> from fastapi import FastAPI
    >>> from nexusdl.interfaces.web.backend.static import mount_static_files
    >>>
    >>> app = FastAPI()
    >>> mount_static_files(app)
    >>>
    >>> # Les fichiers sont maintenant accessibles :
    >>> # GET /static/favicon.svg
    >>> # GET /static/css/api-docs.css
    >>> # GET /static/health.html

Intégration :
    - fastapi.staticfiles : StaticFiles
    - fastapi             : FastAPI app
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from loguru import logger


# ============================================================================
# CONSTANTES
# ============================================================================


# Chemin du dossier static
STATIC_DIR: Final[Path] = Path(__file__).parent.resolve()

# URL de montage
STATIC_URL: Final[str] = "/static"

# Durées de cache (secondes)
CACHE_IMMUTABLE_SECONDS: Final[int] = 31536000  # 1 an (fichiers avec hash)
CACHE_LONG_SECONDS: Final[int] = 86400  # 1 jour (assets)
CACHE_SHORT_SECONDS: Final[int] = 3600  # 1 heure (HTML)
CACHE_NO_CACHE: Final[str] = "no-cache, no-store, must-revalidate"

# Extensions avec cache immutable (fichiers versionnés)
IMMUTABLE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
})

# Extensions avec cache long
LONG_CACHE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".css", ".js",
})

# Extensions avec cache court
SHORT_CACHE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".html", ".htm", ".json", ".xml",
})


# ============================================================================
# FONCTIONS PUBLIQUES
# ============================================================================


def get_static_dir() -> Path:
    """Retourne le chemin absolu du dossier static.

    Returns:
        Chemin vers le dossier static.
    """
    return STATIC_DIR


def get_static_url() -> str:
    """Retourne l'URL de montage des fichiers statiques.

    Returns:
        URL de base (ex: "/static").
    """
    return STATIC_URL


def mount_static_files(
    app: Any,
    *,
    url: str = STATIC_URL,
    html: bool = False,
    check_dir: bool = True,
) -> None:
    """Monte le dossier static dans une application FastAPI.

    Configure les headers de cache et les types MIME de manière appropriée
    pour chaque type de fichier.

    Args:
        app: Instance FastAPI.
        url: URL de montage (défaut: "/static").
        html: Si True, active le mode HTML (index.html servi à la racine).
        check_dir: Si True, vérifie que le dossier existe.

    Raises:
        FileNotFoundError: Si le dossier n'existe pas et check_dir=True.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> mount_static_files(app)
    """
    try:
        from fastapi.staticfiles import StaticFiles
    except ImportError as e:
        logger.error("FastAPI n'est pas installé. Impossible de monter les fichiers statiques.")
        raise ImportError(
            "FastAPI est requis pour monter les fichiers statiques. "
            "Installez-le avec: pip install fastapi"
        ) from e

    if check_dir and not STATIC_DIR.exists():
        raise FileNotFoundError(
            f"Le dossier static n'existe pas: {STATIC_DIR}"
        )

    try:
        app.mount(
            url,
            StaticFiles(
                directory=str(STATIC_DIR),
                html=html,
            ),
            name="static",
        )
        logger.info("Fichiers statiques montés sur {} depuis {}", url, STATIC_DIR)
    except Exception as e:
        logger.error("Impossible de monter les fichiers statiques: {}", e)
        raise


def add_cache_headers_middleware(app: Any) -> None:
    """Ajoute un middleware pour configurer les headers de cache.

    Configure automatiquement les headers Cache-Control en fonction
    de l'extension du fichier demandé.

    Args:
        app: Instance FastAPI.

    Example:
        >>> add_cache_headers_middleware(app)
    """
    @app.middleware("http")
    async def cache_headers_middleware(request: Any, call_next: Any) -> Any:
        response = await call_next(request)

        # Appliquer uniquement aux fichiers statiques
        if not request.url.path.startswith(STATIC_URL):
            return response

        # Déterminer l'extension
        path = request.url.path
        ext = Path(path).suffix.lower()

        # Appliquer le cache approprié
        if ext in IMMUTABLE_EXTENSIONS:
            response.headers["Cache-Control"] = f"public, max-age={CACHE_IMMUTABLE_SECONDS}, immutable"
        elif ext in LONG_CACHE_EXTENSIONS:
            response.headers["Cache-Control"] = f"public, max-age={CACHE_LONG_SECONDS}"
        elif ext in SHORT_CACHE_EXTENSIONS:
            response.headers["Cache-Control"] = f"public, max-age={CACHE_SHORT_SECONDS}"
        else:
            response.headers["Cache-Control"] = CACHE_NO_CACHE

        # Headers de sécurité
        response.headers["X-Content-Type-Options"] = "nosniff"

        return response


def get_static_url_for(filename: str) -> str:
    """Construit l'URL complète pour un fichier statique.

    Args:
        filename: Nom du fichier (ex: "favicon.svg" ou "css/api-docs.css").

    Returns:
        URL complète (ex: "/static/favicon.svg").

    Example:
        >>> get_static_url_for("favicon.svg")
        '/static/favicon.svg'
        >>> get_static_url_for("css/api-docs.css")
        '/static/css/api-docs.css'
    """
    # Normaliser le chemin
    filename = filename.lstrip("/")
    return f"{STATIC_URL}/{filename}"


# ============================================================================
# EXPORTS
# ============================================================================


__all__ = [
    # Constantes
    "STATIC_DIR",
    "STATIC_URL",
    "CACHE_IMMUTABLE_SECONDS",
    "CACHE_LONG_SECONDS",
    "CACHE_SHORT_SECONDS",
    "CACHE_NO_CACHE",
    "IMMUTABLE_EXTENSIONS",
    "LONG_CACHE_EXTENSIONS",
    "SHORT_CACHE_EXTENSIONS",
    # Fonctions
    "get_static_dir",
    "get_static_url",
    "mount_static_files",
    "add_cache_headers_middleware",
    "get_static_url_for",
]
