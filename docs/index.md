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

After version 0.1.0 is published to PyPI, install it with:

```sh
pip install geodataframe-to-pmtiles
```

Until then, install a source checkout with `uv sync`.

> **Note:** The library itself is pure Python, but `gpm.write()` needs a
> native GDAL runtime with the PMTiles driver available. The package imports
> without GDAL; calling the writer without it raises a clear `RuntimeError`.
> In CI we install GDAL from conda-forge. Locally, install GDAL separately via
> conda-forge, Homebrew, or your operating system package manager before
> writing PMTiles archives.

> **Deployment status:** This documentation is built in CI. Its deployment
> infrastructure is configured, but activation and the first deployment remain
> pending. After explicit approval and a successful first deployment, it will
> be available at `https://palewi.re/docs/geodataframe-to-pmtiles/`.

## Quick start

```python
import geopandas as gpd
import geodataframe_to_pmtiles as gpm
from pathlib import Path

points = gpd.read_file("points.geojson")
lines = gpd.read_file("lines.geojson")

# Write a mapping of named GeoDataFrames
gpm.write(
    {"points": points, "lines": lines},
    Path("output.pmtiles"),
    min_zoom=0,
    max_zoom=8,
    name="my map",
    description="Points and lines layer",
    attribution="© OpenStreetMap contributors",  # optional TileJSON attribution
    on_overflow="error",  # default: reject reported tile-level data loss
)

# Or write a single GeoDataFrame with an explicit layer name
gpm.write(points, Path("output.pmtiles"), layer="points")
```

GeoDataFrames passed to `gpm.write()` must already carry a CRS. If your
source format does not store CRS metadata, set one before writing:

```python
points = points.set_crs("EPSG:4326")
lines = lines.set_crs("EPSG:4326")
```

Write to a `BytesIO` stream (useful in web servers or pipelines):

```python
import io
import geodataframe_to_pmtiles as gpm

buf = io.BytesIO()
gpm.write({"points": points}, buf)
buf.seek(0)
# buf.read() contains the raw PMTiles bytes
```

## Test coverage

The test suite includes semantic conformance coverage for a real-world ERA5
climate fixture and a few upstream Tippecanoe cases. Those tests write the
fixtures with `gpm.write()`, then decode the PMTiles archive with the
official `pmtiles` reader and `mapbox-vector-tile` so the checks stay focused
on public behavior: header metadata, source-layer names, property schema, hole
preservation, and input order.

## Design notes

### CRS requirement

All GeoDataFrames must carry an explicit CRS.  Non-EPSG:4326 inputs are
auto-reprojected to WGS 84, while a GeoDataFrame with no CRS set raises
:class:`~geodataframe_to_pmtiles.MissingCRSError`.  If your source format does
not store CRS metadata, set one before calling `gpm.write()`:

```python
gdf = gdf.set_crs("EPSG:4326")
```

### Property normalisation

| Python / pandas type | MVT field type | Notes |
|---|---|---|
| `str` | String | |
| `bool` / `np.bool_` | Boolean | Native MVT `bool_value` |
| `int` / `np.integer` | Integer64 | |
| `float` / `np.float_` | Real | `NaN` → null |
| `datetime` | String | ISO 8601 |
| `list` / `dict` | String | JSON-encoded; must appear in `json_fields` or `json_fields=None` (auto) |
| `None` / `pd.NA` | null | |
| anything else | — | raises `UnsupportedPropertyTypeError` |

Boolean values are stored with MVT's native `bool_value` encoding. Columns may
contain nulls, including pandas `BooleanDtype` values even if every value is
null. A scalar column cannot mix booleans with numeric `0` or `1`, because
those are integers and remain numeric; such mixed columns raise
:class:`~geodataframe_to_pmtiles.UnsupportedPropertyTypeError` instead of being
silently coerced. List- and dict-valued properties are explicitly JSON-encoded
via `json.dumps`; this is intentional and tested.

### json_fields: explicit JSON encoding

By default (`json_fields=None`), all `list` and `dict` columns are
auto-JSON-encoded.  To be more explicit, pass a list of column names:

```python
gpm.write(
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

* **Feature count inflation when reading back.**  The MVT format stores features
  in every intersecting tile; read-back feature counts will exceed input counts.
  This is not data loss.
* **No simplification by default.**  Pass `simplification=<float>` to enable GDAL
  geometry simplification (tolerance in tile-coordinate units, 4096 per tile).

## Performance

### Running the benchmark

A deterministic benchmark is included in `benchmarks/bench_write_pmtiles.py`.
It generates reproducible global point workloads at multiple scales and
measures wall time, peak memory, tile count, and archive size for both `Path`
and `BytesIO` output modes.

```sh
# Install the test group (includes the pmtiles Python reader for tile-count reporting)
uv sync --group test --group test-extras --locked

# Full suite (1 k, 10 k, 50 k, 100 k features)
python benchmarks/bench_write_pmtiles.py

# Fast subset (1 k and 10 k only)
python benchmarks/bench_write_pmtiles.py --fast
```

A reference profile and before/after comparison are in
`benchmarks/profile_report.md`.

### Interpreting results

The checked-in reference profile records a seeded, z0-8 global-point workload
on one macOS arm64 environment with GDAL 3.12.2. Its fast run completed 1,000
features in 0.31–0.41 seconds and 10,000 features in 2.91–3.13 seconds,
depending on whether output was a `Path` or `BytesIO`. These are modest,
reproducible reference measurements for that workload—not a general
throughput, memory, or archive-capacity guarantee.
