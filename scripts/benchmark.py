#!/usr/bin/env python3
"""Benchmark des performances des composants NexusDL.

Mesure les latences et débits des briques critiques sur des sites réels
ou des fixtures locales. Produit un rapport Rich + export JSON optionnel.

Example:
    Benchmark d'un site unique en mode live::

        python scripts/benchmark.py site --site mangadex --iterations 5

    Benchmark complet de tous les sites FR::

        python scripts/benchmark.py search --group fr --iterations 3

    Benchmark offline (CI, fixtures)::

        python scripts/benchmark.py --mode offline all
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Final, Literal

# Ajoute le dossier src/ au PYTHONPATH pour permettre l'exécution directe
# `python scripts/benchmark.py` sans installation préalable.
_SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
_ROOT_DIR: Final[Path] = _SCRIPT_DIR.parent
_SRC_DIR: Final[Path] = _ROOT_DIR / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.progress import (  # noqa: E402
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table  # noqa: E402

from nexusdl.core.config import get_settings  # noqa: E402
from nexusdl.core.constants import PackagingFormat  # noqa: E402
from nexusdl.core.exceptions import NexusDLError  # noqa: E402
from nexusdl.core.logger import setup_logging  # noqa: E402
from nexusdl.core.packaging.base import BasePackager  # noqa: E402
from nexusdl.core.packaging.cbz_packager import CbzPackager  # noqa: E402
from nexusdl.core.packaging.pdf_packager import PdfPackager  # noqa: E402
from nexusdl.core.packaging.zip_packager import ZipPackager  # noqa: E402
from nexusdl.core.registry.site_registry import SiteRegistry  # noqa: E402
from nexusdl.core.session.http_session import HttpSession  # noqa: E402
from nexusdl.core.utils.hash import sha256_file  # noqa: E402
from nexusdl.core.utils.time import utcnow  # noqa: E402
from nexusdl.version import __version__  # noqa: E402

if TYPE_CHECKING:
    from nexusdl.core.models.manga import Manga
    from nexusdl.core.models.site import SiteConfig
    from nexusdl.parsers.base import BaseParser

# ============================================================================
#  Constantes
# ============================================================================

console: Final[Console] = Console()
app: Final[typer.Typer] = typer.Typer(
    name="benchmark",
    help="Benchmark des composants NexusDL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

DEFAULT_ITERATIONS: Final[int] = 5
DEFAULT_WARMUP: Final[int] = 1
DEFAULT_TIMEOUT: Final[float] = 60.0
BENCH_TMP_DIR: Final[Path] = Path(os.environ.get("NEXUSDL_BENCH_TMP", "/tmp/nexusdl-bench"))  # noqa: S108


# ============================================================================
#  Modèles de résultats
# ============================================================================


class BenchMode(str, Enum):
    """Mode d'exécution du benchmark."""

    LIVE = "live"
    OFFLINE = "offline"


class BenchStatus(str, Enum):
    """Statut d'un point de mesure."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class Sample:
    """Un point de mesure unique.

    Attributes:
        name: Identifiant du benchmark (ex: "search", "get_manga").
        site_id: Site testé, ou "local" pour les composants internes.
        status: Résultat du sample.
        duration_ms: Durée en millisecondes.
        bytes_processed: Volume traité (0 si non applicable).
        items_count: Nombre d'éléments traités (résultats, pages, etc.).
        error: Message d'erreur si `status != OK`.
        extra: Données additionnelles spécifiques au benchmark.
    """

    name: str
    site_id: str
    status: BenchStatus
    duration_ms: float
    bytes_processed: int = 0
    items_count: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Aggregate:
    """Agrégation statistique d'une série de samples.

    Attributes:
        name: Nom du benchmark.
        site_id: Site testé.
        count: Nombre de samples valides.
        failures: Nombre d'échecs.
        min_ms: Durée minimum.
        max_ms: Durée maximum.
        mean_ms: Moyenne arithmétique.
        median_ms: Médiane (p50).
        p95_ms: 95e percentile.
        p99_ms: 99e percentile.
        stdev_ms: Écart-type.
        throughput_ops: Opérations par seconde (basé sur la médiane).
        throughput_mbps: Débit en MiB/s si `bytes_processed > 0`.
    """

    name: str
    site_id: str
    count: int
    failures: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    stdev_ms: float
    throughput_ops: float
    throughput_mbps: float


@dataclass(slots=True)
class BenchReport:
    """Rapport complet d'une session de benchmark.

    Attributes:
        version: Version de NexusDL.
        mode: Mode d'exécution (live/offline).
        started_at: Horodatage de début (UTC).
        finished_at: Horodatage de fin (UTC).
        duration_seconds: Durée totale.
        samples: Tous les samples individuels.
        aggregates: Agrégations par (name, site_id).
        config_snapshot: Extrait de la config (sans secrets).
    """

    version: str
    mode: BenchMode
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    samples: list[Sample]
    aggregates: list[Aggregate]
    config_snapshot: dict[str, Any]


# ============================================================================
#  Statistiques
# ============================================================================


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Calcule un percentile par interpolation linéaire.

    Args:
        sorted_values: Liste triée de valeurs numériques (non vide).
        pct: Percentile désiré, entre 0 et 100.

    Returns:
        La valeur du percentile.

    Raises:
        ValueError: Si `sorted_values` est vide ou `pct` hors [0, 100].
    """
    if not sorted_values:
        msg = "sorted_values must be non-empty"
        raise ValueError(msg)
    if not 0.0 <= pct <= 100.0:
        msg = f"pct must be in [0, 100], got {pct}"
        raise ValueError(msg)
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(k)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = k - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def aggregate_samples(samples: list[Sample]) -> list[Aggregate]:
    """Agrège une liste de samples par couple (name, site_id).

    Args:
        samples: Samples bruts issus d'une session de benchmark.

    Returns:
        Liste d'agrégats triés par (site_id, name), un par couple.
    """
    buckets: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        buckets.setdefault((s.site_id, s.name), []).append(s)

    aggregates: list[Aggregate] = []
    for (site_id, name), group in sorted(buckets.items()):
        ok_durations = sorted(s.duration_ms for s in group if s.status == BenchStatus.OK)
        failures = sum(1 for s in group if s.status != BenchStatus.OK)
        total_bytes = sum(s.bytes_processed for s in group if s.status == BenchStatus.OK)

        if not ok_durations:
            # Aucun sample valide — agrégat avec zéros pour tracer l'échec.
            aggregates.append(
                Aggregate(
                    name=name,
                    site_id=site_id,
                    count=0,
                    failures=failures,
                    min_ms=0.0,
                    max_ms=0.0,
                    mean_ms=0.0,
                    median_ms=0.0,
                    p95_ms=0.0,
                    p99_ms=0.0,
                    stdev_ms=0.0,
                    throughput_ops=0.0,
                    throughput_mbps=0.0,
                ),
            )
            continue

        median = statistics.median(ok_durations)
        throughput_ops = 1000.0 / median if median > 0 else 0.0
        throughput_mbps = (total_bytes / (1024 * 1024)) / (sum(ok_durations) / 1000.0) if sum(ok_durations) > 0 else 0.0

        aggregates.append(
            Aggregate(
                name=name,
                site_id=site_id,
                count=len(ok_durations),
                failures=failures,
                min_ms=ok_durations[0],
                max_ms=ok_durations[-1],
                mean_ms=statistics.fmean(ok_durations),
                median_ms=median,
                p95_ms=_percentile(ok_durations, 95.0),
                p99_ms=_percentile(ok_durations, 99.0),
                stdev_ms=statistics.pstdev(ok_durations) if len(ok_durations) > 1 else 0.0,
                throughput_ops=throughput_ops,
                throughput_mbps=throughput_mbps,
            ),
        )
    return aggregates


# ============================================================================
#  Infrastructure de mesure
# ============================================================================


@asynccontextmanager
async def timed_sample(
    name: str,
    site_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> AsyncIterator[dict[str, Any]]:
    """Context manager mesurant la durée d'un bloc et capturant les erreurs.

    Le bloc doit peupler le dict `ctx` retourné avec :
        - `bytes_processed` (int)
        - `items_count` (int)
        - `extra` (dict)

    Args:
        name: Nom du benchmark.
        site_id: Site testé.
        timeout: Timeout maximal en secondes.

    Yields:
        Dictionnaire mutable à peupler dans le bloc.

    Raises:
        asyncio.TimeoutError: Si le bloc dépasse `timeout`.
    """
    ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            yield ctx
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        # Le sample est ajouté par l'appelant — on stocke juste le résultat ici.
        ctx["_duration_ms"] = elapsed_ms


def _record(
    samples: list[Sample],
    name: str,
    site_id: str,
    ctx: dict[str, Any],
    *,
    status: BenchStatus = BenchStatus.OK,
    error: str | None = None,
) -> None:
    """Enregistre un sample à partir du contexte de `timed_sample`.

    Args:
        samples: Liste cible où appendre le sample.
        name: Nom du benchmark.
        site_id: Site testé.
        ctx: Contexte issu de `timed_sample`.
        status: Statut du sample.
        error: Message d'erreur optionnel.
    """
    samples.append(
        Sample(
            name=name,
            site_id=site_id,
            status=status,
            duration_ms=float(ctx.get("_duration_ms", 0.0)),
            bytes_processed=int(ctx.get("bytes_processed", 0)),
            items_count=int(ctx.get("items_count", 0)),
            error=error,
            extra=dict(ctx.get("extra", {})),
        ),
    )


# ============================================================================
#  Benchmarks — Parsers
# ============================================================================


async def bench_search(
    parser: BaseParser,
    site_id: str,
    query: str,
    samples: list[Sample],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Benchmark la méthode `search` d'un parser.

    Args:
        parser: Parser instancié pour le site.
        site_id: Identifiant du site (pour le rapport).
        query: Requête de recherche.
        samples: Liste où appendre le sample.
        timeout: Timeout en secondes.
    """
    try:
        async with timed_sample("search", site_id, timeout=timeout) as ctx:
            results = await parser.search(query)
            ctx["items_count"] = len(results)
            ctx["extra"]["query"] = query
        _record(samples, "search", site_id, ctx)
    except TimeoutError:
        _record(samples, "search", site_id, ctx, status=BenchStatus.TIMEOUT, error="timeout")
    except NexusDLError as exc:
        _record(samples, "search", site_id, ctx, status=BenchStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — benchmark : on veut tout capturer
        logger.exception("search benchmark failed for {}", site_id)
        _record(samples, "search", site_id, ctx, status=BenchStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


async def bench_get_manga(
    parser: BaseParser,
    site_id: str,
    url: str,
    samples: list[Sample],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Manga | None:
    """Benchmark `get_manga` et retourne le Manga pour les benchmarks suivants.

    Args:
        parser: Parser instancié.
        site_id: Identifiant du site.
        url: URL ou ID du manga.
        samples: Liste où appendre le sample.
        timeout: Timeout en secondes.

    Returns:
        Le Manga récupéré, ou None en cas d'échec.
    """
    manga: Manga | None = None
    ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
    try:
        async with timed_sample("get_manga", site_id, timeout=timeout) as ctx:
            manga = await parser.get_manga(url)
            ctx["items_count"] = 1
            ctx["extra"]["title"] = manga.title
            ctx["extra"]["chapters_count"] = len(manga.chapters)
        _record(samples, "get_manga", site_id, ctx)
        return manga
    except TimeoutError:
        _record(samples, "get_manga", site_id, ctx, status=BenchStatus.TIMEOUT, error="timeout")
    except NexusDLError as exc:
        _record(samples, "get_manga", site_id, ctx, status=BenchStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_manga benchmark failed for {}", site_id)
        _record(samples, "get_manga", site_id, ctx, status=BenchStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
    return None


async def bench_get_chapters(
    parser: BaseParser,
    site_id: str,
    manga: Manga,
    samples: list[Sample],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Benchmark `get_chapters` d'un parser.

    Args:
        parser: Parser instancié.
        site_id: Identifiant du site.
        manga: Manga sur lequel travailler.
        samples: Liste où appendre le sample.
        timeout: Timeout en secondes.
    """
    ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
    try:
        async with timed_sample("get_chapters", site_id, timeout=timeout) as ctx:
            chapters = await parser.get_chapters(manga)
            ctx["items_count"] = len(chapters)
        _record(samples, "get_chapters", site_id, ctx)
    except TimeoutError:
        _record(samples, "get_chapters", site_id, ctx, status=BenchStatus.TIMEOUT, error="timeout")
    except NexusDLError as exc:
        _record(samples, "get_chapters", site_id, ctx, status=BenchStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_chapters benchmark failed for {}", site_id)
        _record(samples, "get_chapters", site_id, ctx, status=BenchStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


async def bench_get_pages(
    parser: BaseParser,
    site_id: str,
    manga: Manga,
    samples: list[Sample],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Benchmark `get_pages` sur le premier chapitre du manga.

    Args:
        parser: Parser instancié.
        site_id: Identifiant du site.
        manga: Manga avec chapitres déjà chargés.
        samples: Liste où appendre le sample.
        timeout: Timeout en secondes.
    """
    if not manga.chapters:
        logger.warning("No chapters for {} — skipping get_pages", site_id)
        return

    chapter = manga.chapters[0]
    ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
    try:
        async with timed_sample("get_pages", site_id, timeout=timeout) as ctx:
            pages = await parser.get_pages(chapter)
            ctx["items_count"] = len(pages)
            ctx["extra"]["chapter"] = str(chapter.number)
        _record(samples, "get_pages", site_id, ctx)
    except TimeoutError:
        _record(samples, "get_pages", site_id, ctx, status=BenchStatus.TIMEOUT, error="timeout")
    except NexusDLError as exc:
        _record(samples, "get_pages", site_id, ctx, status=BenchStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_pages benchmark failed for {}", site_id)
        _record(samples, "get_pages", site_id, ctx, status=BenchStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


# ============================================================================
#  Benchmarks — Session HTTP
# ============================================================================


async def bench_http_get(
    session: HttpSession,
    url: str,
    site_id: str,
    samples: list[Sample],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Benchmark un GET HTTP simple (latence réseau + parsing headers).

    Args:
        session: Session HTTP configurée.
        url: URL à requêter.
        site_id: Site associé (pour le rapport).
        samples: Liste où appendre le sample.
        timeout: Timeout en secondes.
    """
    ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
    try:
        async with timed_sample("http_get", site_id, timeout=timeout) as ctx:
            response = await session.get(url)
            ctx["bytes_processed"] = len(response.content)
            ctx["extra"]["status"] = response.status_code
            ctx["extra"]["url"] = url
        _record(samples, "http_get", site_id, ctx)
    except TimeoutError:
        _record(samples, "http_get", site_id, ctx, status=BenchStatus.TIMEOUT, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("http_get benchmark failed for {}", url)
        _record(samples, "http_get", site_id, ctx, status=BenchStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


# ============================================================================
#  Benchmarks — Packaging
# ============================================================================


async def bench_packaging(
    packager: BasePackager,
    pages: list[Path],
    site_id: str,
    samples: list[Sample],
    *,
    iterations: int,
) -> None:
    """Benchmark l'empaquetage d'une liste de pages.

    Args:
        packager: Instance du packager à tester.
        pages: Liste de fichiers image à empaqueter.
        site_id: Identifiant (ici "local").
        samples: Liste où appendre les samples.
        iterations: Nombre d'itérations.
    """
    BENCH_TMP_DIR.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(p.stat().st_size for p in pages if p.exists())
    fmt_name = getattr(packager, "format", "unknown")

    for i in range(iterations):
        output = BENCH_TMP_DIR / f"pack-{fmt_name}-{i}.{fmt_name}"
        ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
        try:
            async with timed_sample(f"packaging:{fmt_name}", site_id) as ctx:
                await packager.package(pages, output)
                if output.exists():
                    ctx["bytes_processed"] = output.stat().st_size
                ctx["items_count"] = len(pages)
                ctx["extra"]["input_bytes"] = total_bytes
            _record(samples, f"packaging:{fmt_name}", site_id, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("packaging benchmark failed for {}", fmt_name)
            _record(samples, f"packaging:{fmt_name}", site_id, ctx, status=BenchStatus.FAILED, error=str(exc))
        finally:
            output.unlink(missing_ok=True)


# ============================================================================
#  Orchestrateur
# ============================================================================


async def run_site_benchmark(
    registry: SiteRegistry,
    site_id: str,
    *,
    query: str,
    iterations: int,
    warmup: int,
    timeout: float,
    samples: list[Sample],
) -> None:
    """Exécute tous les benchmarks d'un site.

    Args:
        registry: Registre de sites chargé.
        site_id: Site à tester.
        query: Requête de recherche.
        iterations: Nombre d'itérations par benchmark.
        warmup: Nombre d'itérations de warmup (non comptabilisées).
        timeout: Timeout par opération.
        samples: Liste où appendre les samples.
    """
    site: SiteConfig = registry.get_site(site_id)
    logger.info("Benchmarking site {} ({})", site_id, site.name)

    parser = registry.get_parser(site_id)
    async with parser:  # suppose que BaseParser est async-context-manager
        # --- Warmup (ignore les résultats) ---
        for _ in range(warmup):
            try:
                await parser.search(query)
            except Exception:  # noqa: BLE001, S110 — warmup best-effort
                pass

        # --- search ---
        for _ in range(iterations):
            await bench_search(parser, site_id, query, samples, timeout=timeout)

        # --- get_manga (1 seule fois, réutilisé) ---
        results = await parser.search(query)
        if not results:
            logger.warning("No search results for {} — skipping manga benchmarks", site_id)
            return

        manga = await bench_get_manga(parser, site_id, str(results[0].url), samples, timeout=timeout)
        if manga is None:
            return

        # --- get_chapters (1 fois, pas d'itération : c'est déjà inclus dans get_manga) ---
        await bench_get_chapters(parser, site_id, manga, samples, timeout=timeout)

        # --- get_pages ---
        for _ in range(iterations):
            await bench_get_pages(parser, site_id, manga, samples, timeout=timeout)


async def run_local_benchmark(
    samples: list[Sample],
    *,
    iterations: int,
    fixtures_dir: Path | None,
) -> None:
    """Benchmarks des composants locaux (packaging, hashing).

    Args:
        samples: Liste où appendre les samples.
        iterations: Nombre d'itérations par benchmark.
        fixtures_dir: Dossier de fixtures images. Si None, génère des images.
    """
    logger.info("Benchmarking local components")
    pages = await _prepare_test_pages(fixtures_dir)
    if not pages:
        logger.warning("No test pages available — skipping packaging benchmarks")
        return

    packagers: list[BasePackager] = [ZipPackager(), CbzPackager(), PdfPackager()]
    for packager in packagers:
        await bench_packaging(packager, pages, "local", samples, iterations=iterations)

    # --- Hashing ---
    for _ in range(iterations):
        ctx: dict[str, Any] = {"bytes_processed": 0, "items_count": 0, "extra": {}}
        try:
            async with timed_sample("hash:sha256", "local") as ctx:
                for p in pages:
                    await asyncio.to_thread(sha256_file, p)
                    ctx["bytes_processed"] += p.stat().st_size
                ctx["items_count"] = len(pages)
            _record(samples, "hash:sha256", "local", ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("hash benchmark failed")
            _record(samples, "hash:sha256", "local", ctx, status=BenchStatus.FAILED, error=str(exc))


async def _prepare_test_pages(fixtures_dir: Path | None) -> list[Path]:
    """Prépare une liste de pages test (fixtures ou générées).

    Args:
        fixtures_dir: Dossier de fixtures. Si None ou vide, génère 20 JPEG 1 MiB.

    Returns:
        Liste de chemins vers des images valides.
    """
    from PIL import Image  # import local : dépendance lourde, chargée à la demande

    BENCH_TMP_DIR.mkdir(parents=True, exist_ok=True)
    pages_dir = BENCH_TMP_DIR / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    if fixtures_dir and fixtures_dir.exists():
        existing = sorted(p for p in fixtures_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        if existing:
            return existing[:50]

    # Génère 20 images 1200x1800 (taille typique d'une page de manga)
    pages: list[Path] = []
    for i in range(20):
        path = pages_dir / f"page_{i:03d}.jpg"
        if not path.exists():
            img = Image.new("RGB", (1200, 1800), color=(i * 10 % 256, 128, 200))
            # Ajoute un peu de bruit pour éviter une compression triviale
            pixels = img.load()
            assert pixels is not None
            for x in range(0, 1200, 50):
                for y in range(0, 1800, 50):
                    pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
            img.save(path, "JPEG", quality=85)
        pages.append(path)
    return pages


async def run_all(
    *,
    mode: BenchMode,
    sites: list[str],
    query: str,
    iterations: int,
    warmup: int,
    timeout: float,
    include_local: bool,
    fixtures_dir: Path | None,
) -> BenchReport:
    """Point d'entrée principal — exécute tous les benchmarks demandés.

    Args:
        mode: Mode live ou offline.
        sites: Liste d'IDs de sites à tester.
        query: Requête de recherche.
        iterations: Itérations par benchmark.
        warmup: Itérations de warmup.
        timeout: Timeout par opération.
        include_local: Inclure les benchmarks locaux.
        fixtures_dir: Dossier de fixtures pour les benchmarks locaux.

    Returns:
        Rapport complet.
    """
    started_at = utcnow()
    t0 = time.perf_counter()
    samples: list[Sample] = []

    settings = get_settings()
    config_snapshot = {
        "profile": settings.profile,
        "include_adult": settings.registry.include_adult,
        "max_concurrent_tasks": settings.download.max_concurrent_tasks,
        "max_concurrent_pages": settings.download.max_concurrent_pages,
        "http2": settings.network.http2,
        "proxy_enabled": settings.proxy.enabled,
        "playwright_enabled": settings.session.playwright.enabled,
        "flaresolverr_enabled": settings.session.flaresolverr.enabled,
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # --- Benchmarks locaux ---
        if include_local:
            task = progress.add_task("Benchmarks locaux", total=1)
            await run_local_benchmark(samples, iterations=iterations, fixtures_dir=fixtures_dir)
            progress.update(task, completed=1)

        # --- Benchmarks sites ---
        if mode == BenchMode.LIVE and sites:
            registry = SiteRegistry.from_settings(settings)
            registry.load()
            task = progress.add_task(f"Sites ({len(sites)})", total=len(sites))

            for site_id in sites:
                try:
                    await run_site_benchmark(
                        registry,
                        site_id,
                        query=query,
                        iterations=iterations,
                        warmup=warmup,
                        timeout=timeout,
                        samples=samples,
                    )
                except KeyError:
                    logger.error("Unknown site: {}", site_id)
                    samples.append(
                        Sample(
                            name="site_lookup",
                            site_id=site_id,
                            status=BenchStatus.SKIPPED,
                            duration_ms=0.0,
                            error="site not found in registry",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Site benchmark crashed for {}", site_id)
                    samples.append(
                        Sample(
                            name="site_benchmark",
                            site_id=site_id,
                            status=BenchStatus.FAILED,
                            duration_ms=0.0,
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                    )
                finally:
                    progress.update(task, advance=1)

    finished_at = utcnow()
    duration = time.perf_counter() - t0

    return BenchReport(
        version=__version__,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        samples=samples,
        aggregates=aggregate_samples(samples),
        config_snapshot=config_snapshot,
    )


# ============================================================================
#  Rendu Rich
# ============================================================================


def render_aggregates_table(report: BenchReport) -> Table:
    """Construit la table Rich des agrégats.

    Args:
        report: Rapport de benchmark.

    Returns:
        Table Rich prête à afficher.
    """
    table = Table(
        title=f"NexusDL Benchmark Report — v{report.version} — mode={report.mode.value}",
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column("Site", style="magenta", no_wrap=True)
    table.add_column("Benchmark", style="cyan", no_wrap=True)
    table.add_column("N", justify="right")
    table.add_column("Fail", justify="right", style="red")
    table.add_column("min (ms)", justify="right")
    table.add_column("median (ms)", justify="right", style="green")
    table.add_column("p95 (ms)", justify="right", style="yellow")
    table.add_column("max (ms)", justify="right")
    table.add_column("ops/s", justify="right")
    table.add_column("MiB/s", justify="right")

    for agg in report.aggregates:
        table.add_row(
            agg.site_id,
            agg.name,
            str(agg.count),
            str(agg.failures) if agg.failures else "·",
            f"{agg.min_ms:.1f}",
            f"{agg.median_ms:.1f}",
            f"{agg.p95_ms:.1f}",
            f"{agg.max_ms:.1f}",
            f"{agg.throughput_ops:.2f}" if agg.throughput_ops else "·",
            f"{agg.throughput_mbps:.2f}" if agg.throughput_mbps else "·",
        )
    return table


def render_summary(report: BenchReport) -> None:
    """Affiche un résumé textuel après la table.

    Args:
        report: Rapport de benchmark.
    """
    total = len(report.samples)
    ok = sum(1 for s in report.samples if s.status == BenchStatus.OK)
    failed = sum(1 for s in report.samples if s.status == BenchStatus.FAILED)
    timeouts = sum(1 for s in report.samples if s.status == BenchStatus.TIMEOUT)
    skipped = sum(1 for s in report.samples if s.status == BenchStatus.SKIPPED)

    console.print()
    console.print(f"[bold]Durée totale :[/bold] {report.duration_seconds:.2f}s")
    console.print(
        f"[bold]Samples :[/bold] {total} "
        f"([green]{ok} ok[/green], "
        f"[red]{failed} failed[/red], "
        f"[yellow]{timeouts} timeouts[/yellow], "
        f"[dim]{skipped} skipped[/dim])",
    )
    if failed or timeouts:
        console.print("[yellow]⚠ Certains benchmarks ont échoué — voir la colonne Fail.[/yellow]")


# ============================================================================
#  Export
# ============================================================================


def export_report(report: BenchReport, path: Path, fmt: Literal["json", "md"]) -> None:
    """Exporte le rapport au format demandé.

    Args:
        report: Rapport à exporter.
        path: Chemin de destination.
        fmt: Format (json ou md).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        payload = {
            "version": report.version,
            "mode": report.mode.value,
            "started_at": report.started_at.isoformat(),
            "finished_at": report.finished_at.isoformat(),
            "duration_seconds": report.duration_seconds,
            "config_snapshot": report.config_snapshot,
            "samples": [
                {
                    **asdict(s),
                    "status": s.status.value,
                }
                for s in report.samples
            ],
            "aggregates": [asdict(a) for a in report.aggregates],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    elif fmt == "md":
        lines: list[str] = [
            f"# NexusDL Benchmark Report — v{report.version}",
            "",
            f"- **Mode** : `{report.mode.value}`",
            f"- **Démarré** : {report.started_at.isoformat()}",
            f"- **Terminé** : {report.finished_at.isoformat()}",
            f"- **Durée totale** : {report.duration_seconds:.2f}s",
            "",
            "## Config snapshot",
            "",
            "```yaml",
            *[f"{k}: {v}" for k, v in report.config_snapshot.items()],
            "```",
            "",
            "## Agrégats",
            "",
            "| Site | Benchmark | N | Fail | min (ms) | median (ms) | p95 (ms) | max (ms) | ops/s | MiB/s |",
            "|------|-----------|---|------|----------|-------------|----------|----------|-------|-------|",
        ]
        for a in report.aggregates:
            lines.append(
                f"| {a.site_id} | {a.name} | {a.count} | {a.failures} | "
                f"{a.min_ms:.1f} | {a.median_ms:.1f} | {a.p95_ms:.1f} | {a.max_ms:.1f} | "
                f"{a.throughput_ops:.2f} | {a.throughput_mbps:.2f} |",
            )
        path.write_text("\n".join(lines), encoding="utf-8")

    logger.info("Report exported to {}", path)


# ============================================================================
#  CLI
# ============================================================================


@app.command("all")
def cmd_all(
    mode: Annotated[BenchMode, typer.Option("--mode", "-m", help="Mode live ou offline")] = BenchMode.LIVE,
    sites: Annotated[str, typer.Option("--sites", "-s", help="Sites séparés par virgule (vide = tous les FR)")] = "",
    group: Annotated[str | None, typer.Option("--group", "-g", help="Groupe de sites (fr, en, adult, api)")] = None,
    query: Annotated[str, typer.Option("--query", "-q", help="Requête de recherche")] = "one piece",
    iterations: Annotated[int, typer.Option("--iterations", "-n", help="Itérations par benchmark")] = DEFAULT_ITERATIONS,
    warmup: Annotated[int, typer.Option("--warmup", "-w", help="Itérations de warmup")] = DEFAULT_WARMUP,
    timeout: Annotated[float, typer.Option("--timeout", "-t", help="Timeout par opération (s)")] = DEFAULT_TIMEOUT,
    include_local: Annotated[bool, typer.Option("--local/--no-local", help="Inclure les benchmarks locaux")] = True,
    fixtures: Annotated[Path | None, typer.Option("--fixtures", help="Dossier de fixtures images")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Fichier de sortie JSON")] = None,
    output_md: Annotated[Path | None, typer.Option("--output-md", help="Fichier de sortie Markdown")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Logs DEBUG")] = False,
) -> None:
    """Exécute tous les benchmarks (sites + locaux)."""
    setup_logging(level="DEBUG" if verbose else "INFO")

    site_list = _resolve_sites(sites, group)

    report = asyncio.run(
        run_all(
            mode=mode,
            sites=site_list,
            query=query,
            iterations=iterations,
            warmup=warmup,
            timeout=timeout,
            include_local=include_local,
            fixtures_dir=fixtures,
        ),
    )

    console.print(render_aggregates_table(report))
    render_summary(report)

    if output:
        export_report(report, output, "json")
    if output_md:
        export_report(report, output_md, "md")

    if any(s.status in {BenchStatus.FAILED, BenchStatus.TIMEOUT} for s in report.samples):
        raise typer.Exit(code=1)


@app.command("site")
def cmd_site(
    site: Annotated[str, typer.Argument(help="ID du site à benchmarker")],
    query: Annotated[str, typer.Option("--query", "-q", help="Requête")] = "one piece",
    iterations: Annotated[int, typer.Option("--iterations", "-n")] = DEFAULT_ITERATIONS,
    timeout: Annotated[float, typer.Option("--timeout", "-t")] = DEFAULT_TIMEOUT,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Benchmark d'un seul site."""
    setup_logging(level="DEBUG" if verbose else "INFO")

    report = asyncio.run(
        run_all(
            mode=BenchMode.LIVE,
            sites=[site],
            query=query,
            iterations=iterations,
            warmup=DEFAULT_WARMUP,
            timeout=timeout,
            include_local=False,
            fixtures_dir=None,
        ),
    )
    console.print(render_aggregates_table(report))
    render_summary(report)


@app.command("local")
def cmd_local(
    iterations: Annotated[int, typer.Option("--iterations", "-n")] = DEFAULT_ITERATIONS,
    fixtures: Annotated[Path | None, typer.Option("--fixtures")] = None,
) -> None:
    """Benchmark uniquement les composants locaux (offline)."""
    setup_logging(level="INFO")

    report = asyncio.run(
        run_all(
            mode=BenchMode.OFFLINE,
            sites=[],
            query="",
            iterations=iterations,
            warmup=0,
            timeout=DEFAULT_TIMEOUT,
            include_local=True,
            fixtures_dir=fixtures,
        ),
    )
    console.print(render_aggregates_table(report))
    render_summary(report)


def _resolve_sites(sites_csv: str, group: str | None) -> list[str]:
    """Résout la liste de sites depuis les options CLI.

    Args:
        sites_csv: Liste séparée par virgule (peut être vide).
        group: Nom de groupe optionnel.

    Returns:
        Liste d'IDs de sites.
    """
    if sites_csv:
        return [s.strip() for s in sites_csv.split(",") if s.strip()]
    if group:
        # Charge les groupes depuis sites_overrides.yaml via le registry
        settings = get_settings()
        registry = SiteRegistry.from_settings(settings)
        registry.load()
        return [s.id for s in registry.list_sites(group=group)]
    # Défaut : sites FR non-adultes
    settings = get_settings()
    registry = SiteRegistry.from_settings(settings)
    registry.load()
    return [s.id for s in registry.list_sites(language="fr", include_adult=False)][:5]


# ============================================================================
#  Entrée
# ============================================================================


def main() -> None:
    """Point d'entrée CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompu par l'utilisateur.[/yellow]")
        raise typer.Exit(code=130) from None
    except NexusDLError as exc:
        console.print(f"[red]Erreur NexusDL :[/red] {exc}")
        raise typer.Exit(code=2) from None


if __name__ == "__main__":
    main()
