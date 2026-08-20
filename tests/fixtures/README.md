# Test fixtures

This directory holds tracked inputs and normalized semantic summaries for the
PMTiles conformance tests.

## Climate fixture

- Source: `analysis/daily-anomalies/era5/1982-07-22.parquet`
- Date: `1982-07-22`
- Field: `t2m_max_delta`
- Backend: native `gdal_contour 3.12.2`
- Clip: Europe `[-10, 35, 30, 60]` in `EPSG:4326`
- Provenance sidecar: `era5-1982-07-22-t2m-max-delta.provenance.json`

The GeoJSON fixture is redistributed with permission from the user.

## Tippecanoe fixtures

The inputs under `tippecanoe/` were copied from
[`mapbox/tippecanoe`](https://github.com/mapbox/tippecanoe) test cases pinned to
commit `4f2621186acfec33b63dfd636f665623c0fef2dd`:

- `tests/polygon-winding/in.json`
- `tests/attribute-type/in.json`
- `tests/stable/in.json`

Tippecanoe is BSD-2-Clause licensed; see
[`upstream LICENSE.md`](https://github.com/mapbox/tippecanoe/blob/master/LICENSE.md)
for the full terms. Only the small GeoJSON inputs are tracked here; no upstream
sample tilesets or PMTiles binaries are copied.

## Golden summaries

The accompanying `summary.json` files are normalized semantic expectations.
They intentionally ignore raw PMTiles bytes, protobuf ordering, and exact
quantized coordinates.
