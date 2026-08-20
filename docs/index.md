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
    on_overflow="warn",  # default: warn about GDAL tile-level drop risk
)
```

Write to a `BytesIO` stream (useful in web servers or pipelines):

```python
import io

buf = io.BytesIO()
write_pmtiles({"points": points}, buf, on_overflow="ignore")
buf.seek(0)
# buf.read() contains the raw PMTiles bytes
```

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

### empty_layer_policy: explicit empty-layer handling

By default (`empty_layer_policy="error"`), any layer with zero features raises
:class:`~geodataframe_to_pmtiles.EmptyLayerError` immediately.  This is the
safe default for callers who control the input.

For the climate-monitor pattern — where a dict of optional layers is assembled
and some may be empty depending on the run — use `empty_layer_policy="skip"`:

```python
result = write_pmtiles(
    {
        "fire_perimeters": fire_gdf,  # may be empty in off-season
        "flood_zones": flood_gdf,  # always has data
    },
    output,
    empty_layer_policy="skip",
    on_overflow="warn",
)
if result.skipped_layers:
    logger.warning("Omitted empty layers: %s", sorted(result.skipped_layers))
```

`skip` mode:

* Omits empty layers from the archive **without raising**.
* Emits a `UserWarning` naming each omitted layer so the omission is never silent.
* Returns their names in :attr:`~geodataframe_to_pmtiles.WriteResult.skipped_layers`.
* Still raises :class:`~geodataframe_to_pmtiles.EmptyLayerError` if *all* layers
  are empty, because an archive with zero layers is unusable.

Do not pre-filter optional layers before calling `write_pmtiles`; use
`empty_layer_policy="skip"` instead so the caller always knows which layers
were written and which were omitted.

### Overflow policy

The GDAL PMTiles driver silently drops features when a tile exceeds its fixed
per-tile caps: **`MAX_FEATURES = 300,000`** and **`MAX_SIZE = 10 MB`**.
These are spike-validated POC values — 200,001 z0 features were preserved
in full (630,430 compressed bytes) — but they are **not unlimited**.  Setting
`MAX_FEATURES=0` does not disable the limit; GDAL clamps it to its internal
minimum.  Dense spatial clustering can produce tiles that exceed these caps,
and GDAL provides no post-write overflow signal.

* `on_overflow="error"` — raises :class:`~geodataframe_to_pmtiles.TileOverflowError`
  before any data is written; the caller must opt in to `"warn"` or `"ignore"`.
* `on_overflow="warn"` (default) — emits a `UserWarning`.
* `on_overflow="ignore"` — writes silently.

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
