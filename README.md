# geodataframe-to-pmtiles

Write [PMTiles](https://protomaps.com/docs/pmtiles) vector archives from one or
more [GeoPandas](https://geopandas.org) GeoDataFrames using GDAL directly — no
subprocesses, no temporary files.

## Install

```sh
pip install geodataframe-to-pmtiles
```

> **Note:** The library itself is pure Python, but `write_pmtiles()` needs a
> native GDAL runtime with the PMTiles driver available. The package imports
> without GDAL; calling the writer without it raises a clear `RuntimeError`.
> In CI we install GDAL from conda-forge. Locally, install GDAL separately via
> conda-forge, Homebrew, or your operating system package manager before
> writing PMTiles archives.

## Usage

```python
import geopandas as gpd
from pathlib import Path
from geodataframe_to_pmtiles import write_pmtiles

points = gpd.read_file("points.geojson").to_crs("EPSG:4326")
polygons = gpd.read_file("polys.geojson").to_crs("EPSG:4326")

write_pmtiles(
    {"points": points, "polygons": polygons},
    Path("output.pmtiles"),
    min_zoom=0,
    max_zoom=8,
    name="my map",
    description="Points and polygons",
    on_overflow="warn",  # default: warn about GDAL tile-level drop risk
)
```

Write to a `BytesIO` stream instead of a file:

```python
import io

buf = io.BytesIO()
write_pmtiles({"points": points}, buf, on_overflow="ignore")
```

## Test coverage

The test suite includes semantic conformance checks that write tracked climate
and Tippecanoe fixtures through GDAL, then decode the resulting PMTiles
archives with the official `pmtiles` reader and `mapbox-vector-tile`. The tests
assert header metadata, source-layer names, property schemas, hole
preservation, and feature order while ignoring raw bytes and protobuf ordering.

## API

See the [documentation](https://palewire.github.io/geodataframe-to-pmtiles/) for
the full API reference.

### `write_pmtiles(layers, output, *, min_zoom, max_zoom, name, description, json_fields, on_overflow, simplification)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `layers` | `dict[str, GeoDataFrame]` | required | Layer name → GeoDataFrame mapping. All GDFs must be **EPSG:4326**. |
| `output` | `Path \| BinaryIO` | required | Destination path or binary stream. |
| `min_zoom` | `int` | `0` | Archive-wide minimum zoom level (0-22). |
| `max_zoom` | `int` | `8` | Archive-wide maximum zoom level (0-22). |
| `name` | `str` | `""` | Tileset name stored in archive metadata. |
| `description` | `str` | `""` | Human-readable description in archive metadata. |
| `json_fields` | `Collection[str] \| None` | `None` | Columns to JSON-encode (list/dict values). `None` auto-encodes all; explicit set restricts to named columns only. |
| `on_overflow` | `"error" \| "warn" \| "ignore"` | `"warn"` | Policy for GDAL tile-level drop risk. See overflow notes below. |
| `simplification` | `float \| None` | `None` | Geometry simplification tolerance (tile units). `None` = disabled. |

### Property normalisation

| Python / pandas type | MVT field | Notes |
|---|---|---|
| `str` | String | |
| `bool` / `np.bool_` | Integer 0/1 | MVT has no native bool |
| `int` / `np.integer` | Integer64 | |
| `float` / `np.float_` | Real | NaN → null |
| `datetime` | String | ISO 8601 |
| `list` / `dict` | String | JSON-encoded; column must be in `json_fields` or `json_fields=None` (auto) |
| `None` / `pd.NA` | null | |
| other | — | `UnsupportedPropertyTypeError` |

### Exceptions

| Exception | When raised |
|---|---|
| `EmptyLayerError` | `layers` is empty or a GDF has no features. |
| `MissingCRSError` | A GDF has no CRS set (explicit source CRS required). |
| `UnsupportedCRSError` | A GDF's CRS is not EPSG:4326. |
| `UnsupportedPropertyTypeError` | A column has an unrecognised type, or a list/dict column not in `json_fields`. |
| `TileOverflowError` | `on_overflow='error'` — raised before writing; caller must acknowledge limitation. |

## Overflow policy

The GDAL PMTiles driver silently drops features when a tile exceeds its fixed
per-tile caps: **`MAX_FEATURES = 300,000`** and **`MAX_SIZE = 10 MB`**.
These are spike-validated POC values (200,001 z0 features preserved in
630,430 compressed bytes) — not unlimited.  Setting `MAX_FEATURES=0` does
not disable the limit; GDAL clamps it to its internal minimum.  Dense spatial
clustering can still produce tiles that exceed the caps, and GDAL provides no
post-write overflow signal.

* `on_overflow="error"` — refuses to write; caller must opt in to `"warn"` or
  `"ignore"` after acknowledging the limitation.
* `on_overflow="warn"` (default) — emits a `UserWarning` before writing.
* `on_overflow="ignore"` — writes silently.

## Known limitations

* **Attribution is not supported** in this POC.  Testing with GDAL 3.12.2 showed
  that the `CONF` creation option does not write `attribution` to the archive
  bytes.  The parameter is intentionally absent from the API.  Support will be
  added when a reliable mechanism is found.
* **Feature count inflation when reading back**: MVT stores features in every
  intersecting tile; read-back counts exceed input counts.  This is not data loss.
* **Simplification disabled by default**: pass `simplification=<float>` to enable.

## Development

```sh
make install
make check   # lint, format, type checks
make verify  # full suite: checks, tests, build, docs
```

See [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
