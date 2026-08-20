#!/usr/bin/env python
"""Deterministic benchmark for ``write``.

Generates reproducible point-layer workloads at multiple scales and
measures wall time, peak memory, tile count, and archive size.  Results
are printed to stdout.  No network access or private data required.

Usage
-----
Run with the project's Python environment (needs GDAL with the PMTiles
driver and geopandas installed)::

    # Recommended: use the environment that has GDAL available
    python benchmarks/bench_write_pmtiles.py

    # Scale selection (skip large scales for a fast sanity check)
    python benchmarks/bench_write_pmtiles.py --fast

Output columns
--------------
scale       : number of input point features
output      : "Path" or "BytesIO"
wall_s      : median wall-clock time in seconds (3 timed runs after 1 warm-up)
peak_mb     : tracemalloc peak memory in MiB for the first timed run
archive_kb  : compressed PMTiles archive size in KiB
tile_count  : number of tiles in the archive (from PMTiles header)

Interpreting results
--------------------
``wall_s`` is the end-to-end cost including GDAL vsimem write, mvt tile
encoding, and output (Path write or BytesIO fill).  The GDAL import is
amortised after the first call, so the benchmark warms GDAL once before
timing.

``peak_mb`` reflects Python-level allocations (tracemalloc), not total
process RSS.  It captures the in-Python WKB buffer, pre-normalised
column lists, and the vsimem-read buffer.

The compact, deterministic workloads below are reproducible reference points,
not a general throughput, memory, or capacity promise.
"""

from __future__ import annotations

import argparse
import io
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

try:
    import geopandas as gpd
    from shapely.geometry import Point
except ImportError as exc:
    sys.exit(f"geopandas / shapely not available: {exc}")

try:
    from geodataframe_to_pmtiles import write
except ImportError as exc:
    sys.exit(f"geodataframe_to_pmtiles not installed: {exc}")

try:
    from pmtiles.reader import MmapSource, Reader  # type: ignore[import-untyped]

    _HAS_PMTILES = True
except ImportError:
    _HAS_PMTILES = False

# ---------------------------------------------------------------------------
# Deterministic data generator
# ---------------------------------------------------------------------------

_RNG_SEED = 42


def make_point_gdf(n: int) -> gpd.GeoDataFrame:
    """Return a deterministic GeoDataFrame of *n* global point features.

    The same seed always produces the same coordinates and property
    values, so results are reproducible across runs and machines.

    Columns
    -------
    id      : sequential integer index
    label   : string label  "pt_<i>"
    value   : float64 uniform in [0, 1)
    """
    rng = np.random.default_rng(_RNG_SEED)
    lons = rng.uniform(-180.0, 180.0, n)
    lats = rng.uniform(-90.0, 90.0, n)
    return gpd.GeoDataFrame(
        {
            "id": np.arange(n, dtype=np.int64),
            "label": [f"pt_{i}" for i in range(n)],
            "value": rng.uniform(0.0, 1.0, n),
        },
        geometry=[Point(x, y) for x, y in zip(lons, lats, strict=True)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Tile-count helper
# ---------------------------------------------------------------------------


def count_tiles(path: Path) -> int:
    """Return the number of tiles in the archive, or -1 if pmtiles is absent."""
    if not _HAS_PMTILES:
        return -1
    with path.open("r+b") as fh:
        reader = Reader(MmapSource(fh))
        hdr = reader.header()
        return int(hdr.get("tile_entries_count", -1))


# ---------------------------------------------------------------------------
# Single benchmark run
# ---------------------------------------------------------------------------

_REPEATS = 3  # number of timed repetitions; median is reported
_WARMUP = 1  # warm-up call before timing (amortises GDAL import)


def _run_path(gdf: gpd.GeoDataFrame, tmp: Path) -> tuple[float, float, int, int]:
    """Benchmark Path output.  Returns (wall_s, peak_mb, archive_kb, ntiles)."""
    out = tmp / "out.pmtiles"

    # warm-up
    for _ in range(_WARMUP):
        write({"pts": gdf[:10]}, out, on_overflow="unsafe")

    times: list[float] = []
    peak_mb = 0.0
    for i in range(_REPEATS):
        tracemalloc.start()
        t0 = time.perf_counter()
        write({"pts": gdf}, out, on_overflow="unsafe")
        wall = time.perf_counter() - t0
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        times.append(wall)
        if i == 0:
            peak_mb = peak / 1024**2

    archive_kb = out.stat().st_size // 1024
    ntiles = count_tiles(out)
    return statistics.median(times), peak_mb, archive_kb, ntiles


def _run_bytesio(gdf: gpd.GeoDataFrame, tmp: Path) -> tuple[float, float, int, int]:
    """Benchmark BytesIO output.  Returns (wall_s, peak_mb, archive_kb, ntiles)."""
    # warm-up using a small slice
    write({"pts": gdf[:10]}, io.BytesIO(), on_overflow="unsafe")

    times: list[float] = []
    peak_mb = 0.0
    last_data: bytes = b""
    for i in range(_REPEATS):
        buf = io.BytesIO()
        tracemalloc.start()
        t0 = time.perf_counter()
        write({"pts": gdf}, buf, on_overflow="unsafe")
        wall = time.perf_counter() - t0
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        times.append(wall)
        if i == 0:
            peak_mb = peak / 1024**2
            last_data = buf.getvalue()

    archive_kb = len(last_data) // 1024

    # Write to disk briefly to count tiles (needs MmapSource)
    if _HAS_PMTILES:
        tmp_out = tmp / "bytesio.pmtiles"
        tmp_out.write_bytes(last_data)
        ntiles = count_tiles(tmp_out)
    else:
        ntiles = -1

    return statistics.median(times), peak_mb, archive_kb, ntiles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_SCALES_FULL = [1_000, 10_000, 50_000, 100_000]
_SCALES_FAST = [1_000, 10_000]

_HEADER = (
    f"{'scale':>8}  {'output':>7}  {'wall_s':>7}  "
    f"{'peak_mb':>8}  {'archive_kb':>11}  {'tile_count':>10}"
)
_SEP = "-" * len(_HEADER)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Only run the two smallest scales (faster CI-friendly mode).",
    )
    args = parser.parse_args()

    scales = _SCALES_FAST if args.fast else _SCALES_FULL

    print(_HEADER)
    print(_SEP)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        for n in scales:
            gdf = make_point_gdf(n)
            for label, runner in [("Path", _run_path), ("BytesIO", _run_bytesio)]:
                wall, peak, kb, ntiles = runner(gdf, tmp)
                tile_str = str(ntiles) if ntiles >= 0 else "n/a"
                print(
                    f"{n:>8,}  {label:>7}  {wall:>7.3f}  "
                    f"{peak:>8.1f}  {kb:>11,}  {tile_str:>10}"
                )


if __name__ == "__main__":
    main()
