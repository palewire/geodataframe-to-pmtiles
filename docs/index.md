# geodataframe-to-pmtiles

Write PMTiles archives from GeoPandas GeoDataFrames with a Python API.

## Installation

Install the package from PyPI:

```console
pip install geodataframe-to-pmtiles
```

Writing an archive also requires a native GDAL runtime with the PMTiles driver.
The package can be imported without GDAL, but `gpm.write()` raises a
`RuntimeError` when that runtime is unavailable. Install GDAL separately with
conda-forge, Homebrew, or your operating system's package manager before
writing archives.

## Diagnostics and troubleshooting

Run the post-install diagnostic when GDAL setup prevents writing:

```console
python -m geodataframe_to_pmtiles check
python -m geodataframe_to_pmtiles check --json
```

`gpm.check()` returns the same typed `CheckReport` in Python. Its stable check
names cover Python bindings, binding/native version compatibility, the supported
GDAL range (3.8 or later), the PMTiles driver and capabilities, and a real
in-memory write followed by an independent reopen and decode. `report.ok` is
true only when every check passes. Ordinary setup failures return failed results
instead of raising exceptions.

The command is runtime verification only, never an install hook. Conda-forge is
the most reliable cross-platform option because it installs matching native
GDAL and Python bindings together. On macOS, install GDAL with Homebrew and use
bindings built for that installation. On Linux, install matching GDAL runtime
and Python packages from one system package source. If the PMTiles driver,
vector creation capability, or smoke check fails, install a complete GDAL build
with PMTiles, GEOS, and SQLite support. Include the small `--json` output in a
bug report; it excludes credentials, home paths, and full environment dumps.

## Quick start

Write one GeoDataFrame by passing it first and supplying the layer name:

```python
from pathlib import Path

import geopandas as gpd
import geodataframe_to_pmtiles as gpm

points = gpd.read_file("points.geojson")

gpm.write(
    points,
    Path("points.pmtiles"),
    layer="points",
)
```

## Multiple named layers

To write more than one layer, pass a mapping. Its keys become the names stored
in the archive:

```python
boundaries = gpd.read_file("boundaries.geojson")

gpm.write(
    {"points": points, "boundaries": boundaries},
    Path("map.pmtiles"),
)
```

Every input GeoDataFrame needs an explicit CRS. Inputs in any resolvable CRS
are reprojected to EPSG:4326 without changing the original frame. Assign a CRS
first when a source file does not provide one:

```python
points = points.set_crs("EPSG:4326")
```

`output` can be a path or a binary stream. This is useful when an application
needs archive bytes instead of a file:

```python
from io import BytesIO

archive = BytesIO()
gpm.write({"points": points}, archive)
archive.seek(0)
```

## `gpm.write` options

The two positional inputs are `layers` and `output`; all other inputs are
keyword-only. Use `layer` with the single-GeoDataFrame form, not the mapping
form.

| Option | Description |
| --- | --- |
| `layers` | A single `GeoDataFrame`, or a non-empty mapping of string layer names to `GeoDataFrame` objects. Every frame must be non-empty and have an explicit, resolvable CRS. |
| `output` | A string or `Path` destination, or a binary-writable stream such as `BytesIO`. Path output is written to a temporary file and atomically replaces the destination only on success. |
| `layer` | Required for a single GeoDataFrame and omitted for a mapping. It must be a non-empty string without null characters. |
| `min_zoom` | Archive-wide minimum zoom, from 0 through 22; defaults to `0` and cannot exceed `max_zoom`. |
| `max_zoom` | Archive-wide maximum zoom, from 0 through 22; defaults to `8` and cannot be below `min_zoom`. |
| `name` | Optional tileset name, stored in archive metadata when non-empty. Defaults to an empty string. |
| `description` | Optional human-readable description, stored in archive metadata when non-empty. Defaults to an empty string. |
| `attribution` | Optional string stored as TileJSON `attribution`; the default empty string omits that key. Unicode and HTML are preserved. A non-string raises `TypeError`. |
| `json_fields` | `None` by default, which JSON-encodes every list- or dictionary-valued column. A collection limits that treatment to named columns; other list or dictionary columns raise `UnsupportedPropertyTypeError`. |
| `on_overflow` | `"error"` by default, which raises `TileOverflowError` before changing the destination when GDAL reports a tile limit action. `"unsafe"` warns and writes despite possible dropped features or reduced precision. |
| `simplification` | Optional geometry simplification factor in tile-coordinate units (4,096 per tile). The default, `None`, disables simplification. |

## Archive behavior

PMTiles uses the Web Mercator latitude range (±85.05112877980659°). Features
entirely outside that range are warned about and skipped; if no features remain,
the writer raises `EmptyLayerError`. Features that cross the boundary continue
to GDAL for clipping.

## Errors and limitations

The principal exceptions are `MissingCRSError` for a GeoDataFrame without a
CRS, `UnsupportedCRSError` or `CRSTransformError` for reprojection failures,
`UnsupportedPropertyTypeError` for values that cannot be written as MVT
properties, `EmptyLayerError` for empty layers, and `TileOverflowError` for a
detected safe-overflow rejection.

MVT stores a feature in every tile it intersects, so feature counts read back
from an archive can be higher than the source count. That duplication is not
data loss. GDAL's tile limits can still be reached by dense data; keep the
default overflow policy unless accepting the resulting loss is intentional.

## Links

- [Source code](https://github.com/palewire/geodataframe-to-pmtiles)
- [Issue tracker](https://github.com/palewire/geodataframe-to-pmtiles/issues)
- [Changelog](https://github.com/palewire/geodataframe-to-pmtiles/blob/main/CHANGELOG.md)
- [PyPI package](https://pypi.org/project/geodataframe-to-pmtiles/)

## About

[Ben Welsh](https://github.com/benwelsh) created this module in August 2026 as
a spinoff of the
[Reuters Climate Monitor](https://www.reuters.com/graphics/CLIMATE-AUTOMATED/MONITOR/akpeykqqapr/).
[GitHub Copilot](https://github.com/features/copilot), an AI-powered coding
assistant, helped design, implement, test, and document the project.
