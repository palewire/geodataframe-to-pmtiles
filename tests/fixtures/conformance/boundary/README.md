# Boundary Condition Test Fixtures

Synthetic fixtures for Web Mercator boundary and antimeridian coverage testing.
All files are public-domain synthetic data created for this test suite.

## `polar_points.geojson` / `polar_points.provenance.json`

Twelve synthetic point features placed at:

- The **exact** Web Mercator latitude limit (±85.05112877980659°)
- 0.001° **inside** the limit on each side
- 0.001° **outside** the limit on each side (expected: `UserWarning` + dropped)
- At the **geographic poles** (±90°, expected: `UserWarning` + dropped)
- At the **antimeridian** (±180° longitude, equator)
- Near-corner points at (±179.999°, ±84.999°)

**Expected behaviour:** 8 features within bounds → present in the archive;
4 features outside bounds → `UserWarning` emitted, excluded from archive.

## `boundary_polygons.geojson` / `boundary_polygons.provenance.json`

Six synthetic polygon features testing the four key cases:

| Feature                   | Expected outcome                                 |
|---------------------------|--------------------------------------------------|
| `within_bounds`           | Included at z0, unchanged                        |
| `crosses_north_limit`     | Included, clipped by GDAL at tile boundary       |
| `crosses_south_limit`     | Included, clipped by GDAL at tile boundary       |
| `entirely_outside_north`  | `UserWarning` emitted; excluded from archive     |
| `entirely_outside_south`  | `UserWarning` emitted; excluded from archive     |
| `antimeridian_multipolygon` | Included; appears in tiles on both sides of ±180° |

## Intentional differences from Tippecanoe

GDAL's PMTiles driver uses `BUFFER=80/4096` (≈1.95% of a tile) for the
geometry buffer around each tile boundary.  Tippecanoe's default buffer is
larger (≈5%).  This means that a feature within the Tippecanoe buffer but
outside the GDAL buffer will appear in high-zoom Tippecanoe tiles but not in
GDAL tiles — without any data loss at lower zooms.  This is expected and is
**not** a bug.
