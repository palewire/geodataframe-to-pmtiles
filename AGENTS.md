# Agent Guide

This repository implements `geodataframe_to_pmtiles`, a Python library that
writes PMTiles vector archives from one or more GeoPandas GeoDataFrames using
GDAL's native PMTiles vector driver — no subprocesses, no temporary files.

## Repository Structure

- `pyproject.toml`: Package metadata and tool configuration.
- `src/geodataframe_to_pmtiles/`: Library source (src layout).
  - `__init__.py`: Public API exports.
  - `_writer.py`: Core `write` implementation.
  - `exceptions.py`: Custom exception hierarchy.
  - `py.typed`: PEP 561 marker for typed packages.
- `tests/`: pytest tests.
- `docs/`: Sphinx documentation source.
- `.github/workflows/`: CI, docs, and release workflows.
- `Makefile`: Common development and verification commands.

## Development Workflow

Install all dependencies:

```sh
make install
```

While making changes:

```sh
make check   # Fast lint, format, type checks
make verify  # Full suite: checks, tests, build, docs
```

Verify the wheel can be installed and imported:

```sh
make package-check PACKAGE=geodataframe_to_pmtiles
make coverage PACKAGE=geodataframe_to_pmtiles
```

## Public API

The single public entry point is `write`:

```python
from geodataframe_to_pmtiles import write

write(
    layers={
        "layer_name": gdf
    },  # dict[str, GeoDataFrame]; any explicit CRS is accepted and reprojected to EPSG:4326
    output=Path("out.pmtiles"),  # Path or binary stream
    min_zoom=0,
    max_zoom=8,
    name="",
    description="",
    attribution="",
    simplification=None,
)
```

## GDAL Dependency

`write()` needs a native GDAL runtime with the PMTiles driver
available, but the package itself imports without GDAL.  The CI workflow
installs GDAL from conda-forge and runs `uv` against that active environment.
For local development, install GDAL separately via conda-forge, Homebrew, or
your system package manager before running the archive-writing tests.

## Known Design Limitations

- **Silent tile drops**: The GDAL PMTiles driver silently drops features when a
  tile exceeds `MAX_SIZE` or `MAX_FEATURES`.  `write` sets very large
  limits but cannot guarantee an error on overflow.
- **Feature count inflation**: Reading back a PMTiles archive reports more
  features than were written because MVT stores features in every intersecting
  tile.  This is not data loss.
- **Boolean encoding**: MVT has no native boolean type.  Booleans are stored as
  integers (1 / 0).
- **Attribution**: Stored in the TileJSON `CONF` blob, not as a first-class GDAL
  metadata key.

## Changelog

Add a concise entry under the `Unreleased` section in `CHANGELOG.md` for any
user-facing API or behaviour change.  Do not add entries for internal-only
changes.

## Releases

Follow `RELEASING.md`.  Do not create tags, releases, or publish packages
without explicit human approval.
