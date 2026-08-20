# geodataframe-to-pmtiles

Write [PMTiles](https://protomaps.com/docs/pmtiles) vector archives from one or
more [GeoPandas](https://geopandas.org) GeoDataFrames using
[GDAL](https://gdal.org) directly — no subprocesses, no temporary files.

```{toctree}
:maxdepth: 2
:hidden:

api
changelog
```

## Installation

```sh
pip install geodataframe-to-pmtiles
```

> **Note:** The `gdal` Python package is an sdist-only build.  It requires the
> system GDAL development headers to compile.  On Debian/Ubuntu install
> `libgdal-dev`; on macOS use `brew install gdal`.  The installed GDAL version
> must match the `gdal` Python package version exactly.

## Quick start

```python
import geopandas as gpd
from pathlib import Path
from geodataframe_to_pmtiles import write_pmtiles

points = gpd.read_file("points.geojson").to_crs("EPSG:4326")
lines = gpd.read_file("lines.geojson").to_crs("EPSG:4326")

write_pmtiles(
    {"points": points, "lines": lines},
    Path("output.pmtiles"),
    min_zoom=0,
    max_zoom=8,
    name="my map",
    description="Points and lines layer",
    attribution="© My Organisation",
)
```

You can also write to a `BytesIO` stream — useful in web servers or pipelines
that avoid touching the filesystem:

```python
import io

buf = io.BytesIO()
write_pmtiles({"points": points}, buf)
buf.seek(0)
# buf.read() contains the raw PMTiles bytes
```

## Design notes

### CRS requirement

All GeoDataFrames must be in **EPSG:4326** (geographic WGS 84).  Reproject
before calling `write_pmtiles`:

```python
gdf = gdf.to_crs("EPSG:4326")
```

### Property normalisation

| Python / pandas type | MVT field type | Notes |
|---|---|---|
| `str` | String | |
| `bool` / `np.bool_` | Integer (0 or 1) | MVT has no native bool |
| `int` / `np.integer` | Integer64 | |
| `float` / `np.float_` | Real | `NaN` → null |
| `datetime` | String | ISO 8601 |
| `list` / `dict` | String | JSON-encoded explicitly |
| `None` / `pd.NA` | null | |
| anything else | — | raises `UnsupportedPropertyTypeError` |

Boolean values are stored as `0` / `1` integers because the MVT specification
does not include a dedicated boolean type.  List- and dict-valued properties are
JSON-encoded to strings; this encoding is deliberate and tested.

### Known limitations

* **Silent tile-level drops.**  The GDAL PMTiles driver can silently drop
  features when a single tile exceeds its `MAX_SIZE` (bytes) or `MAX_FEATURES`
  limit.  `write_pmtiles` sets both limits very high (500 MB / 2 000 000
  features per tile) to reduce the chance of loss, but there is no guaranteed
  error on overflow.  For large datasets, verify the output feature distribution
  independently.
* **Duplicate features when reading back.**  The MVT format stores features in
  every tile they intersect, so feature counts reported by a reader will exceed
  the input counts.  This is expected behaviour, not data loss.
* **No simplification by default.**  Pass `simplification=<float>` to enable
  geometry simplification (tolerance in tile-coordinate units, 4 096 per tile).
* **Attribution is not a first-class GDAL metadata key.**  It is embedded in the
  archive's TileJSON `CONF` blob and may not be surfaced by all readers.
