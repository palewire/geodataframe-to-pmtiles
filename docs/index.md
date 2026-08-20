# geodataframe-to-pmtiles

Write [PMTiles](https://protomaps.com/docs/pmtiles) vector archives from one or
more [GeoPandas](https://geopandas.org) GeoDataFrames using GDAL's native
PMTiles vector driver — no subprocesses, no temporary files.

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

> **Note:** The library itself is pure Python, but `write_pmtiles()` needs a
> native GDAL runtime with the PMTiles driver available. The package imports
> without GDAL; calling the writer without it raises a clear `RuntimeError`.
> In CI we install GDAL from conda-forge. Locally, install GDAL separately via
> conda-forge, Homebrew, or your operating system package manager before
> writing PMTiles archives.

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
)
```

Write to a `BytesIO` stream (useful in web servers or pipelines):

```python
import io

buf = io.BytesIO()
write_pmtiles({"points": points}, buf)
buf.seek(0)
# buf.read() contains the raw PMTiles bytes
```

## Test coverage

The test suite includes semantic conformance coverage for a real-world ERA5
climate fixture and a few upstream Tippecanoe cases. Those tests write the
fixtures with `write_pmtiles()`, then decode the PMTiles archive with the
official `pmtiles` reader and `mapbox-vector-tile` so the checks stay focused
on public behavior: header metadata, source-layer names, property schema, hole
preservation, and input order.

## Design notes

### CRS requirement

All GeoDataFrames must be in **EPSG:4326** (geographic WGS 84).  An explicit
source CRS is required — passing a GeoDataFrame with no CRS set raises
:class:`~geodataframe_to_pmtiles.MissingCRSError`.  Reproject before calling
`write_pmtiles`:

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
| `list` / `dict` | String | JSON-encoded; must appear in `json_fields` or `json_fields=None` (auto) |
| `None` / `pd.NA` | null | |
| anything else | — | raises `UnsupportedPropertyTypeError` |

Boolean values are stored as `0` / `1` integers because the MVT specification
does not include a dedicated boolean type.  List- and dict-valued properties are
explicitly JSON-encoded via `json.dumps`; this is intentional and tested.

### json_fields: explicit JSON encoding

By default (`json_fields=None`), all `list` and `dict` columns are
auto-JSON-encoded.  To be more explicit, pass a list of column names:

```python
write_pmtiles(
    {"lyr": gdf},
    output,
    json_fields=["tags", "metadata"],  # only these columns are JSON-encoded
)
```

Any `list`- or `dict`-valued column not covered by `json_fields` raises
:class:`~geodataframe_to_pmtiles.UnsupportedPropertyTypeError`.  This prevents
GDAL's internal list-to-string conversion from leaking through.

### Overflow policy

GDAL's MVT encoder can drop features after `MAX_FEATURES` is reached and
reduce geometry precision after `MAX_SIZE` is exceeded. The writer uses
practical limits (300,000 features and 10 MB per tile) and captures the
encoder's diagnostics during finalization. With the default
`on_overflow="error"`, a reported feature-cap rebuild or size-driven recode
raises :class:`~geodataframe_to_pmtiles.TileOverflowError` before the Path or
stream is changed. The exception lists the limit, configured value, observed
value, and tile coordinate when GDAL reports one.

The 200,001-feature z0 spike remains supported and is independently decoded
in the test suite. This is not a capacity promise: clustered features or dense
geometry can still exceed a tile limit, but the default API cannot publish
that archive after GDAL reports the action.

`on_overflow="unsafe"` is an explicit opt-out. It emits a warning and may
publish an archive with missing features or lower-precision geometry.

### Climate-monitor guidance

Keep the default policy for climate cell layers. If it raises
`TileOverflowError`, split the layer or lower its density at the affected zoom;
do not use `on_overflow="unsafe"` where holes or coordinate changes would
alter reported conditions.

### Known limitations

* **Attribution is not supported in this POC.**  Testing with GDAL 3.12.2 showed
  that the `CONF` creation option does **not** write an `attribution` key to the
  raw archive bytes.  The parameter is intentionally absent from the public API.
  Support will be added when a reliable mechanism is found.
* **Feature count inflation when reading back.**  The MVT format stores features
  in every intersecting tile; read-back feature counts will exceed input counts.
  This is not data loss.
* **No simplification by default.**  Pass `simplification=<float>` to enable GDAL
  geometry simplification (tolerance in tile-coordinate units, 4096 per tile).
