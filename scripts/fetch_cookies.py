#!/usr/bin/env python3
"""Récupération interactive de cookies pour les sites NexusDL.

Trois modes :

    interactive → ouvre un navigateur Playwright, l'utilisateur se connecte,
                  les cookies sont extraits et sauvegardés chiffrés.
    import      → importe les cookies depuis un navigateur existant
                  (Firefox, Chrome, Chromium, Edge) via leur base SQLite.
    refresh     → recharge les cookies existants dans un Playwright headless
                  pour vérifier leur validité et rafraîchir cf_clearance.

Example:
    Login interactif sur SushiScan ::

        python scripts/fetch_cookies.py interactive sushiscan

    Import depuis Firefox pour MangaDex ::

        python scripts/fetch_cookies.py import mangadex --browser firefox

    Rafraîchir tous les cookies expirés ::

        python scripts/fetch_cookies.py refresh --all

    Lister les cookies stockés (sans les déchiffrer) ::

        python scripts/fetch_cookies.py list
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Final, Iterator

# --- Résolution des chemins racine -----------------------------------------
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
_ENV_FILE: Final[Path] = _ROOT_DIR / ".env"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Confirm, Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

from nexusdl.core.exceptions import NexusDLError  # noqa: E402
from nexusdl.core.logger import setup_logging  # noqa: E402
from nexusdl.core.paths import (  # noqa: E402
    get_config_dir,
    get_cookies_path,
)
from nexusdl.core.registry.site_registry import SiteRegistry  # noqa: E402
from nexusdl.core.session.cookie_manager import CookieManager  # noqa: E402
from nexusdl.core.utils.time import utcnow  # noqa: E402

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="fetch-cookies",
    help="Récupération de cookies pour NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

DEFAULT_LOGIN_TIMEOUT: Final[float] = 300.0   # 5 minutes pour se connecter
DEFAULT_POLL_INTERVAL: Final[float] = 2.0     # vérification toutes les 2s
MAX_COOKIE_AGE_DAYS: Final[int] = 90          # avertit au-delà

# Chemins par navigateur (Linux/macOS/Windows) pour l'import
BROWSER_COOKIE_PATHS: Final[dict[str, dict[str, str]]] = {
    "firefox": {
        "linux": "~/.mozilla/firefox/*/cookies.sqlite",
        "darwin": "~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite",
        "windows": "~/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite",
    },
    "chrome": {
        "linux": "~/.config/google-chrome/*/Cookies",
        "darwin": "~/Library/Application Support/Google/Chrome/*/Cookies",
        "windows": "~/AppData/Local/Google/Chrome/User Data/*/Cookies",
    },
    "chromium": {
        "linux": "~/.config/chromium/*/Cookies",
        "darwin": "~/Library/Application Support/Chromium/*/Cookies",
        "windows": "~/AppData/Local/Chromium/User Data/*/Cookies",
    },
    "edge": {
        "linux": "~/.config/microsoft-edge/*/Cookies",
        "darwin": "~/Library/Application Support/Microsoft Edge/*/Cookies",
        "windows": "~/AppData/Local/Microsoft/Edge/User Data/*/Cookies",
    },
}

# Cookies de session (à exclure lors de l'import navigateur — jamais persistants)
SESSION_COOKIE_NAMES: Final[frozenset[str]] = frozenset(
    {"PHPSESSID", "sessionid", "session", "JSESSIONID", "ASP.NET_SessionId"},
)


class FetchMode(str, Enum):
    """Mode de récupération des cookies."""

    INTERACTIVE = "interactive"
    IMPORT = "import"
    REFRESH = "refresh"


class CookieStatus(str, Enum):
    """Statut d'un cookie stocké."""

    OK = "ok"
    EXPIRED = "expired"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class CookieInfo:
    """Métadonnées d'un cookie sans exposer sa valeur.

    Attributes:
        site_id: Site associé.
        name: Nom du cookie.
        domain: Domaine du cookie.
        expires_at: Timestamp epoch d'expiration, ou None (session).
        http_only: Cookie HttpOnly.
        secure: Cookie Secure.
        status: Statut calculé.
    """

    site_id: str
    name: str
    domain: str
    expires_at: float | None
    http_only: bool
    secure: bool
    status: CookieStatus = CookieStatus.UNKNOWN


@dataclass(slots=True)
class FetchReport:
    """Rapport de récupération.

    Attributes:
        mode: Mode utilisé.
        site_id: Site ciblé.
        fetched_at: Timestamp epoch.
        cookie_count: Nombre de cookies récupérés.
        bytes_written: Taille du fichier de cookies chiffré.
        cookie_names: Noms des cookies (pas les valeurs).
        warnings: Avertissements.
        errors: Erreurs.
    """

    mode: FetchMode
    site_id: str
    fetched_at: float
    cookie_count: int = 0
    bytes_written: int = 0
    cookie_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ============================================================================
#  Utilitaires
# ============================================================================


def _load_secret_key() -> bytes:
    """Charge la clé de chiffrement depuis l'environnement.

    Génère une clé aléatoire si absente (warning — les cookies précédents
    seront illisibles). Recommande d'ajouter NEXUSDL_SECRET_KEY dans .env.

    Returns:
        Clé de 32 octets pour AES-GCM.
    """
    key_hex = os.environ.get("NEXUSDL_SECRET_KEY")
    if key_hex is None and _ENV_FILE.exists():
        # Fallback : parse .env à la main (on n'importe pas python-dotenv ici)
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("NEXUSDL_SECRET_KEY="):
                key_hex = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if key_hex is None:
        console.print(
            "[yellow]⚠ Pas de NEXUSDL_SECRET_KEY — génération d'une clé éphémère.[/yellow]\n"
            "[yellow]  Les cookies ne seront pas réutilisables dans une autre session.[/yellow]\n"
            "[yellow]  Ajoute NEXUSDL_SECRET_KEY dans .env pour persister.[/yellow]",
        )
        return os.urandom(32)

    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        msg = f"NEXUSDL_SECRET_KEY invalide (attendu: hex 64 caractères, got: {len(key_hex)})"
        raise RuntimeError(msg) from exc

    if len(key) != 32:
        msg = f"NEXUSDL_SECRET_KEY doit faire 32 octets (64 hex), got {len(key)}"
        raise RuntimeError(msg)
    return key


def _read_cookie_db(browser: str, profile_pattern: str) -> Path | None:
    """Trouve le fichier de cookies d'un navigateur.

    Args:
        browser: Nom du navigateur (firefox, chrome, chromium, edge).
        profile_pattern: Pattern de chemin avec glob (voir BROWSER_COOKIE_PATHS).

    Returns:
        Chemin du fichier cookies.sqlite (Firefox) ou Cookies (Chromium),
        ou None si introuvable.
    """
    import glob

    expanded = Path(os.path.expanduser(profile_pattern))
    candidates = [Path(p) for p in glob.glob(str(expanded))]
    if not candidates:
        return None
    # Prend le profil le plus récent
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _copy_locked_file(source: Path) -> Path:
    """Copie un fichier potentiellement verrouillé (navigateur ouvert).

    Args:
        source: Fichier source.

    Returns:
        Chemin vers une copie temporaire (à supprimer par l'appelant).

    Raises:
        OSError: Si la copie échoue.
    """
    tmp = Path(tempfile.mkstemp(suffix=source.suffix)[1])
    shutil.copy2(source, tmp)
    return tmp


def _detect_platform() -> str:
    """Détecte la plateforme pour choisir les chemins navigateur.

    Returns:
        "linux", "darwin" ou "windows".

    Raises:
        RuntimeError: Si la plateforme n'est pas supportée.
    """
    import platform as _platform

    sysname = _platform.system().lower()
    mapping = {"linux": "linux", "darwin": "darwin", "windows": "windows"}
    resolved = mapping.get(sysname)
    if resolved is None:
        msg = f"Plateforme non supportée pour l'import navigateur : {sysname}"
        raise RuntimeError(msg)
    return resolved


def _cookies_to_playwright(cookies: dict[str, str], domain: str) -> list[dict[str, Any]]:
    """Convertit un dict {name: value} en format Playwright.

    Args:
        cookies: Dict nom → valeur.
        domain: Domaine pour les cookies (ex: ".example.com").

    Returns:
        Liste de dicts au format Playwright `BrowserContext.add_cookies`.
    """
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": domain.startswith("https"),
            "sameSite": "Lax",
        }
        for name, value in cookies.items()
    ]


def _cookies_to_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    """Convertit une liste Playwright en dict {name: value}.

    Args:
        cookies: Liste Playwright.

    Returns:
        Dict nom → valeur.
    """
    return {c["name"]: c["value"] for c in cookies}


# ============================================================================
#  Mode interactif (Playwright)
# ============================================================================


async def _fetch_interactive(
    site_id: str,
    *,
    url: str | None,
    timeout: float,
    headless: bool,
    browser_name: str,
) -> FetchReport:
    """Ouvre un navigateur Playwright, laisse l'utilisateur se connecter.

    Args:
        site_id: ID du site.
        url: URL de départ (sinon, premier domaine du site).
        timeout: Timeout max d'attente de connexion.
        headless: Mode headless (déconseillé pour login).
        browser_name: chromium, firefox ou webkit.

    Returns:
        Rapport de récupération.

    Raises:
        RuntimeError: Si Playwright échoue à démarrer.
    """
    report = FetchReport(
        mode=FetchMode.INTERACTIVE,
        site_id=site_id,
        fetched_at=time.time(),
    )

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        msg = "Playwright non installé. Lancer : `uv pip install -e '.[browsers]'`"
        raise RuntimeError(msg) from exc

    from nexusdl.core.config import get_settings

    settings = get_settings()
    registry = SiteRegistry.from_settings(settings)
    registry.load()
    site = registry.get_site(site_id)

    start_url = url or str(site.domains[0])
    console.print(f"[cyan]Ouverture de {start_url} dans {browser_name}…[/cyan]")
    console.print(
        Panel(
            "[bold]1.[/bold] Connecte-toi au site dans la fenêtre qui va s'ouvrir.\n"
            "[bold]2.[/bold] Résous le challenge Cloudflare si présent.\n"
            "[bold]3.[/bold] Reviens ici et appuie sur [cyan]Entrée[/cyan] pour capturer les cookies.\n"
            f"[dim]Timeout : {timeout:.0f}s[/dim]",
            title="Instructions",
            border_style="cyan",
        ),
    )

    async with async_playwright() as pw:
        browser_type = getattr(pw, browser_name, None)
        if browser_type is None:
            msg = f"Navigateur inconnu : {browser_name}"
            raise RuntimeError(msg)

        browser = await browser_type.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
            timezone_id="Europe/Paris",
        )
        page = await context.new_page()
        await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)

        # Attend que l'utilisateur confirme via Entrée OU timeout
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Prompt.ask("[cyan]Appuie sur Entrée quand tu es connecté[/cyan]"),
            )
        except (KeyboardInterrupt, EOFError):
            report.warnings.append("Confirmation interrompue — capture partielle")
        finally:
            all_cookies = await context.cookies()
            await browser.close()

    # Filtre les cookies utiles
    relevant = _filter_relevant_cookies(all_cookies, str(site.domains[0]))
    cookie_dict = _cookies_to_dict(relevant)

    if not cookie_dict:
        report.errors.append("Aucun cookie récupéré")
        return report

    # Sauvegarde via CookieManager
    cm = CookieManager(storage_path=get_cookies_path(), key=_load_secret_key())
    cm.update_cookies(site_id, cookie_dict)

    report.cookie_count = len(cookie_dict)
    report.cookie_names = sorted(cookie_dict.keys())
    path = get_cookies_path()
    if path.exists():
        report.bytes_written = path.stat().st_size

    return report


def _filter_relevant_cookies(
    all_cookies: list[dict[str, Any]],
    base_url: str,
) -> list[dict[str, Any]]:
    """Filtre les cookies pertinents pour un domaine.

    Args:
        all_cookies: Tous les cookies Playwright.
        base_url: URL de base du site.

    Returns:
        Cookies dont le domaine matche celui du site.
    """
    from urllib.parse import urlparse

    base_domain = urlparse(base_url).hostname or ""
    # Retire le www. pour matcher le domaine racine
    base_root = base_domain.removeprefix("www.")

    result = []
    for c in all_cookies:
        domain = c.get("domain", "").lstrip(".")
        if base_root in domain or domain in base_root:
            result.append(c)
    return result


# ============================================================================
#  Mode import navigateur
# ============================================================================


def _import_from_browser(
    site_id: str,
    *,
    browser: str,
    domain_filter: str | None,
    include_session: bool,
) -> FetchReport:
    """Importe les cookies depuis un navigateur existant.

    Args:
        site_id: ID du site.
        browser: Nom du navigateur (firefox, chrome, chromium, edge).
        domain_filter: Filtre domaine (sinon, domaines du site).
        include_session: Inclure les cookies de session.

    Returns:
        Rapport de récupération.
    """
    report = FetchReport(mode=FetchMode.IMPORT, site_id=site_id, fetched_at=time.time())

    browser = browser.lower()
    if browser not in BROWSER_COOKIE_PATHS:
        valid = ", ".join(BROWSER_COOKIE_PATHS)
        msg = f"Navigateur inconnu : {browser}. Valides : {valid}"
        raise RuntimeError(msg)

    platform_key = _detect_platform()
    pattern = BROWSER_COOKIE_PATHS[browser][platform_key]
    db_path = _read_cookie_db(browser, pattern)
    if db_path is None:
        report.errors.append(f"Base cookies introuvable pour {browser} sur {platform_key}")
        return report

    logger.info("Base cookies trouvée : {}", db_path)

    # Détermine les domaines cibles
    from nexusdl.core.config import get_settings

    settings = get_settings()
    registry = SiteRegistry.from_settings(settings)
    registry.load()
    site = registry.get_site(site_id)

    if domain_filter:
        domains = [domain_filter]
    else:
        domains = [str(d).replace("https://", "").replace("http://", "").split("/")[0] for d in site.domains]

    # Copie la DB (navigateur peut être ouvert → lock SQLite)
    with contextmanager(lambda: (yield))():  # placeholder — voir _copy_locked_file
        pass
    tmp_db = _copy_locked_file(db_path)
    try:
        cookies = _extract_cookies_from_db(tmp_db, browser, domains, include_session)
    finally:
        tmp_db.unlink(missing_ok=True)

    if not cookies:
        report.warnings.append(f"Aucun cookie trouvé pour les domaines : {domains}")
        return report

    cm = CookieManager(storage_path=get_cookies_path(), key=_load_secret_key())
    cm.update_cookies(site_id, cookies)

    report.cookie_count = len(cookies)
    report.cookie_names = sorted(cookies.keys())
    path = get_cookies_path()
    if path.exists():
        report.bytes_written = path.stat().st_size
    return report


def _extract_cookies_from_db(
    db_path: Path,
    browser: str,
    domains: list[str],
    include_session: bool,
) -> dict[str, str]:
    """Extrait les cookies d'une base SQLite navigateur.

    Firefox stocke en clair dans `moz_cookies`. Chromium chiffre les valeurs
    avec une clé OS-specific — on ne supporte donc **que Firefox** en clair.
    Pour Chrome/Chromium/Edge, on se contente des **noms** avec un warning.

    Args:
        db_path: Chemin de la copie SQLite.
        browser: Nom du navigateur.
        domains: Liste de domaines cibles.
        include_session: Inclure les cookies de session.

    Returns:
        Dict nom → valeur.
    """
    cookies: dict[str, str] = {}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if browser == "firefox":
                cookies = _extract_firefox_cookies(conn, domains, include_session)
            else:
                logger.warning(
                    "{} chiffre les cookies avec une clé OS-specific — import limité aux noms. "
                    "Utilise `interactive` pour capturer les valeurs.",
                    browser,
                )
                cookies = _extract_chromium_cookie_names(conn, domains)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        logger.error("Erreur lecture SQLite : {}", exc)
    return cookies


def _extract_firefox_cookies(
    conn: sqlite3.Connection,
    domains: list[str],
    include_session: bool,
) -> dict[str, str]:
    """Extrait les cookies Firefox (stockés en clair).

    Args:
        conn: Connexion SQLite.
        domains: Domaines cibles.
        include_session: Inclure cookies de session.

    Returns:
        Dict nom → valeur.
    """
    placeholders = " OR ".join("host LIKE ?" for _ in domains)
    params = [f"%{d}%" for d in domains]
    query = f"SELECT name, value, host, isSecure, isHttpOnly FROM moz_cookies WHERE {placeholders}"  # noqa: S608
    rows = conn.execute(query, params).fetchall()

    cookies: dict[str, str] = {}
    for row in rows:
        name = row["name"]
        if not include_session and name in SESSION_COOKIE_NAMES:
            continue
        cookies[name] = row["value"]
    return cookies


def _extract_chromium_cookie_names(
    conn: sqlite3.Connection,
    domains: list[str],
) -> dict[str, str]:
    """Extrait les **noms** de cookies Chromium (valeurs chiffrées).

    Args:
        conn: Connexion SQLite.
        domains: Domaines cibles.

    Returns:
        Dict nom → "" (valeur vide).
    """
    placeholders = " OR ".join("host_key LIKE ?" for _ in domains)
    params = [f"%{d}%" for d in domains]
    query = f"SELECT name FROM cookies WHERE {placeholders}"  # noqa: S608
    rows = conn.execute(query, params).fetchall()
    return {row["name"]: "" for row in rows}


# ============================================================================
#  Mode refresh
# ============================================================================


async def _refresh_cookies(
    site_id: str,
    *,
    headless: bool,
    browser_name: str,
) -> FetchReport:
    """Recharge les cookies dans Playwright headless et vérifie leur validité.

    Args:
        site_id: ID du site.
        headless: Mode headless.
        browser_name: Navigateur à utiliser.

    Returns:
        Rapport de récupération.
    """
    report = FetchReport(mode=FetchMode.REFRESH, site_id=site_id, fetched_at=time.time())

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        msg = "Playwright non installé."
        raise RuntimeError(msg) from exc

    from nexusdl.core.config import get_settings

    settings = get_settings()
    registry = SiteRegistry.from_settings(settings)
    registry.load()
    site = registry.get_site(site_id)

    cm = CookieManager(storage_path=get_cookies_path(), key=_load_secret_key())
    existing = cm.get_cookies(site_id)
    if not existing:
        report.warnings.append("Aucun cookie existant — utiliser `interactive` d'abord")
        return report

    console.print(f"[cyan]Refresh de {len(existing)} cookies pour {site_id}…[/cyan]")

    async with async_playwright() as pw:
        browser_type = getattr(pw, browser_name, None)
        if browser_type is None:
            msg = f"Navigateur inconnu : {browser_name}"
            raise RuntimeError(msg)

        browser = await browser_type.launch(headless=headless)
        context = await browser.new_context()
        base_url = str(site.domains[0])
        from urllib.parse import urlparse

        domain = "." + (urlparse(base_url).hostname or "").removeprefix("www.")
        await context.add_cookies(_cookies_to_playwright(existing, domain))

        page = await context.new_page()
        try:
            resp = await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            if resp and resp.status >= 400:
                report.errors.append(f"HTTP {resp.status} — cookies probablement invalides")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Navigation échouée : {exc}")

        refreshed = await context.cookies()
        await browser.close()

    new_cookies = _cookies_to_dict(_filter_relevant_cookies(refreshed, base_url))
    if not new_cookies:
        report.errors.append("Refresh n'a produit aucun cookie")
        return report

    cm.update_cookies(site_id, new_cookies)
    report.cookie_count = len(new_cookies)
    report.cookie_names = sorted(new_cookies.keys())
    return report


# ============================================================================
#  Commande list
# ============================================================================


def _list_stored_cookies() -> list[CookieInfo]:
    """Liste les cookies stockés sans exposer leurs valeurs.

    Returns:
        Liste d'informations sur chaque cookie.
    """
    path = get_cookies_path()
    if not path.exists():
        return []

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except ImportError:
        console.print("[red]cryptography non installé.[/red]")
        return []

    key = _load_secret_key()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Fichier cookies illisible : {}", exc)
        return []

    infos: list[CookieInfo] = []
    # Le format de fichier dépend de CookieManager — on suppose
    # {"site_id": {"cookies": {"name": "value"}, "metadata": {...}}}
    for site_id, payload in raw.items():
        meta = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        expires = meta.get("expires_at")
        for name in payload.get("cookies", {}):
            status = CookieStatus.OK
            if expires and expires < time.time():
                status = CookieStatus.EXPIRED
            infos.append(
                CookieInfo(
                    site_id=site_id,
                    name=name,
                    domain=meta.get("domain", "—"),
                    expires_at=expires,
                    http_only=meta.get("http_only", False),
                    secure=meta.get("secure", True),
                    status=status,
                ),
            )
    return infos


# ============================================================================
#  Rendu
# ============================================================================


def render_report(report: FetchReport) -> None:
    """Affiche le rapport de récupération.

    Args:
        report: Rapport à afficher.
    """
    console.print()
    if report.errors:
        console.print(
            Panel(
                "\n".join(f"✗ {e}" for e in report.errors),
                title="Erreurs",
                border_style="red",
            ),
        )
    if report.warnings:
        console.print(
            Panel(
                "\n".join(f"⚠ {w}" for w in report.warnings),
                title="Avertissements",
                border_style="yellow",
            ),
        )
    if report.cookie_count:
        console.print(
            Panel(
                f"[bold green]✓ {report.cookie_count} cookie(s) sauvegardé(s) pour [cyan]{report.site_id}[/cyan][/bold green]\n"
                f"Taille chiffrée : {report.bytes_written} octets\n"
                f"Cookies : {', '.join(report.cookie_names)}\n"
                f"[dim]Stockés dans {get_cookies_path()}[/dim]",
                title="Succès",
                border_style="green",
            ),
        )


# ============================================================================
#  CLI
# ============================================================================


@app.command("interactive")
def cmd_interactive(
    site_id: Annotated[str, typer.Argument(help="ID du site (ex: sushiscan, nhentai)")],
    url: Annotated[str | None, typer.Option("--url", "-u", help="URL de départ")] = None,
    timeout: Annotated[float, typer.Option("--timeout", "-t", help="Timeout (s)")] = DEFAULT_LOGIN_TIMEOUT,
    headless: Annotated[bool, typer.Option("--headless", help="Mode headless (déconseillé)")] = False,
    browser: Annotated[str, typer.Option("--browser", "-b", help="chromium|firefox|webkit")] = "chromium",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Login interactif : ouvre un navigateur et capture les cookies."""
    setup_logging(level="DEBUG" if verbose else "INFO")
    try:
        report = asyncio.run(
            _fetch_interactive(site_id, url=url, timeout=timeout, headless=headless, browser_name=browser),
        )
    except NexusDLError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=2) from exc
    render_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("import")
def cmd_import(
    site_id: Annotated[str, typer.Argument(help="ID du site")],
    browser: Annotated[str, typer.Option("--browser", "-b", help="firefox|chrome|chromium|edge")] = "firefox",
    domain: Annotated[str | None, typer.Option("--domain", "-d", help="Filtre domaine")] = None,
    include_session: Annotated[bool, typer.Option("--include-session", help="Inclure cookies de session")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Importe les cookies depuis un navigateur existant."""
    setup_logging(level="DEBUG" if verbose else "INFO")
    try:
        report = _import_from_browser(
            site_id,
            browser=browser,
            domain_filter=domain,
            include_session=include_session,
        )
    except NexusDLError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=2) from exc
    render_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command("refresh")
def cmd_refresh(
    site_id: Annotated[str | None, typer.Argument(help="ID du site (omis si --all)")] = None,
    all_sites: Annotated[bool, typer.Option("--all", help="Rafraîchir tous les sites stockés")] = False,
    headless: Annotated[bool, typer.Option("--headless/--headed")] = True,
    browser: Annotated[str, typer.Option("--browser", "-b")] = "chromium",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Recharge les cookies existants pour vérifier/rafraîchir leur validité."""
    setup_logging(level="DEBUG" if verbose else "INFO")

    if not site_id and not all_sites:
        console.print("[red]Précise un site_id ou --all.[/red]")
        raise typer.Exit(code=2)

    targets: list[str]
    if all_sites:
        path = get_cookies_path()
        if not path.exists():
            console.print("[yellow]Aucun cookie stocké.[/yellow]")
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            targets = list(raw.keys())
        except json.JSONDecodeError:
            targets = []
    else:
        targets = [site_id]  # type: ignore[list-item]

    if not targets:
        console.print("[yellow]Aucun site à rafraîchir.[/yellow]")
        return

    failures = 0
    for target in targets:
        try:
            report = asyncio.run(_refresh_cookies(target, headless=headless, browser_name=browser))
            render_report(report)
            if report.errors:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]✗ {target} : {exc}[/red]")
            failures += 1

    if failures:
        raise typer.Exit(code=1)


@app.command("list")
def cmd_list(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Inclure métadonnées")] = False,
) -> None:
    """Liste les cookies stockés (sans déchiffrer les valeurs)."""
    setup_logging(level="WARNING")
    infos = _list_stored_cookies()
    if not infos:
        console.print("[yellow]Aucun cookie stocké.[/yellow]")
        return

    table = Table(title="Cookies stockés", header_style="bold cyan")
    table.add_column("Site", style="magenta")
    table.add_column("Nom", style="cyan")
    table.add_column("Statut", justify="center")
    if verbose:
        table.add_column("Domaine", style="dim")
        table.add_column("Expire", justify="right", style="dim")
        table.add_column("HttpOnly", justify="center", style="dim")
        table.add_column("Secure", justify="center", style="dim")

    status_icons = {
        CookieStatus.OK: "[green]✓ ok[/green]",
        CookieStatus.EXPIRED: "[red]✗ expiré[/red]",
        CookieStatus.MISSING: "[yellow]? absent[/yellow]",
        CookieStatus.UNKNOWN: "[dim]?[/dim]",
    }

    for info in infos:
        row = [info.site_id, info.name, status_icons[info.status]]
        if verbose:
            expires_str = (
                "session" if info.expires_at is None else _format_relative_time(info.expires_at)
            )
            row.extend(
                [
                    info.domain,
                    expires_str,
                    "✓" if info.http_only else "·",
                    "✓" if info.secure else "·",
                ],
            )
        table.add_row(*row)

    console.print(table)
    console.print(f"[dim]{len(infos)} cookie(s) au total.[/dim]")


def _format_relative_time(ts: float) -> str:
    """Formate un timestamp en durée relative.

    Args:
        ts: Timestamp epoch.

    Returns:
        Chaîne du type "in 2h", "3d ago".
    """
    delta = ts - time.time()
    if delta < 0:
        delta = -delta
        suffix = "ago"
    else:
        suffix = ""
    if delta < 60:
        return f"{int(delta)}s {suffix}".strip()
    if delta < 3600:
        return f"{int(delta / 60)}m {suffix}".strip()
    if delta < 86400:
        return f"{int(delta / 3600)}h {suffix}".strip()
    return f"{int(delta / 86400)}d {suffix}".strip()


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu.[/yellow]")
        raise typer.Exit(code=130) from None


if __name__ == "__main__":
    main()
