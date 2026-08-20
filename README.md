# geodataframe-to-pmtiles

Write [PMTiles](https://protomaps.com/docs/pmtiles) vector archives from one or
more [GeoPandas](https://geopandas.org) GeoDataFrames using GDAL directly — no
subprocesses, no temporary files.

## Install

```sh
pip install geodataframe-to-pmtiles
```

> **Note:** The `gdal` Python package requires the system GDAL development
> headers.  On Debian/Ubuntu: `apt install libgdal-dev`.  On macOS:
> `brew install gdal`.  The `gdal` PyPI package version must match the
> system GDAL version exactly (e.g. `pip install gdal==3.12.2`).

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
    attribution="© My Organisation",
)
```

Write to a `BytesIO` stream instead of a file:

```python
import io

buf = io.BytesIO()
write_pmtiles({"points": points}, buf)
```

## API

See the [documentation](https://palewire.github.io/geodataframe-to-pmtiles/) for
the full API reference.

### `write_pmtiles(layers, output, *, min_zoom, max_zoom, name, description, attribution, simplification)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `layers` | `dict[str, GeoDataFrame]` | required | Layer name → GeoDataFrame mapping.  All GDFs must be EPSG:4326. |
| `output` | `Path \| BinaryIO` | required | Destination path or binary stream. |
| `min_zoom` | `int` | `0` | Archive-wide minimum zoom level (0–22). |
| `max_zoom` | `int` | `8` | Archive-wide maximum zoom level (0–22). |
| `name` | `str` | `""` | Tileset name stored in archive metadata. |
| `description` | `str` | `""` | Human-readable description in archive metadata. |
| `attribution` | `str` | `""` | Attribution stored in the TileJSON `CONF` blob. |
| `simplification` | `float \| None` | `None` | Geometry simplification tolerance (tile units).  `None` = disabled. |

### Exceptions

| Exception | When raised |
|---|---|
| `EmptyLayerError` | `layers` is empty or a GDF has no features. |
| `MissingCRSError` | A GDF has no CRS set. |
| `UnsupportedCRSError` | A GDF's CRS is not EPSG:4326. |
| `UnsupportedPropertyTypeError` | A column has a value that cannot be encoded as an MVT property. |

## Known limitations

* The GDAL PMTiles driver silently drops features when a tile exceeds its
  per-tile `MAX_SIZE` / `MAX_FEATURES` limit.  `write_pmtiles` sets generous
  limits (500 MB / 2 000 000 per tile) but cannot guarantee an error on overflow.
* Reading back a PMTiles archive reports higher feature counts than the input
  because the MVT format stores features in every intersecting tile.  This is
  not data loss.
* Attribution is stored only in the TileJSON `CONF` blob inside the archive,
  not as a first-class GDAL metadata key; some readers may not surface it.

## Development

```sh
make install
make check   # lint, format, type checks
make verify  # full suite: checks, tests, build, docs
```

See [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
