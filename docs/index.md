# geodataframe-to-pmtiles

Create [PMTiles](https://protomaps.com/docs/pmtiles) vector archives from
[GeoPandas](https://geopandas.org/) GeoDataFrames with GDAL's native PMTiles
driver. `geodataframe-to-pmtiles` writes directly through GDAL, without
subprocesses or intermediate data files.

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

## Quick start

Pass a mapping to create multiple named layers:

```python
from pathlib import Path

import geopandas as gpd
import geodataframe_to_pmtiles as gpm

points = gpd.read_file("points.geojson")
boundaries = gpd.read_file("boundaries.geojson")

gpm.write(
    {"points": points, "boundaries": boundaries},
    Path("map.pmtiles"),
    min_zoom=0,
    max_zoom=8,
    name="Example map",
    description="Points and boundaries",
    attribution="© OpenStreetMap contributors",
)
```

Or pass one GeoDataFrame and give its layer a name:

```python
gpm.write(points, Path("points.pmtiles"), layer="points")
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

## Archive behavior

`min_zoom` and `max_zoom` set archive-wide zoom levels from 0 through 22
(defaulting to 0 and 8). `name` and `description` become archive metadata, and
a non-empty `attribution` is stored in the archive's TileJSON metadata.

List and dictionary properties are JSON-encoded by default. To limit that
behavior to known columns, pass their names with `json_fields`; an unlisted
list or dictionary property then raises `UnsupportedPropertyTypeError`.
Geometry simplification is off by default. Pass `simplification` only when a
loss of geometric detail is appropriate.

For a path destination, the completed archive replaces the destination
atomically. The default `on_overflow="error"` rejects an archive before either
a path or stream destination changes when GDAL reports a tile feature limit or
size-driven geometry recoding. `on_overflow="unsafe"` is an explicit lossy
opt-out: it warns and may write an archive with missing features or reduced
precision.

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
