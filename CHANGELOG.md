# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **Renamed public API**: `write_pmtiles` is replaced by `gpm.write` (usable
  as `import geodataframe_to_pmtiles as gpm`).  No compatibility alias is
  provided.  `gpm.write` accepts either a `Mapping[str, GeoDataFrame]` or a
  single `GeoDataFrame` with an explicit `layer=` name; the mapping form
  rejects `layer`, and the single-frame form requires it.  All existing
  options and behaviors are preserved.  Closes #16.

### Added

- `TileLimitViolation` context on `TileOverflowError`, including the GDAL
  limit, configured and observed values, and tile coordinate when available.
- `gpm.write` now emits a `UserWarning` and raises `EmptyLayerError` when
  features lie entirely outside the Web Mercator latitude extent
  (beyond ±85.05112877980659°), replacing the previous silent drop by the GDAL
  PMTiles driver.  Features that straddle the boundary are still passed through
  to GDAL for clipping as before.
- `WEB_MERCATOR_LAT_LIMIT` constant exported from
  `geodataframe_to_pmtiles._writer` for downstream use.
- Compact offline fixtures and boundary/antimeridian semantic tests covering
  polar-point exclusion, polygon clipping, antimeridian split MultiPolygon,
  ERA5 z-0 tile-fragment counts, and south-polar z-7/z-8 buffer differences.
- `gpm.write` now accepts GeoDataFrames in **any explicit, resolvable
  CRS** — not only EPSG:4326.  Each layer is automatically reprojected to
  EPSG:4326 with traditional GIS X/Y axis order before writing; input
  GeoDataFrames and geometries are never mutated.  Mixed-CRS layer mappings
  are fully supported.
- `CRSTransformError` — raised (chained from the original exception) when a
  coordinate transformation to EPSG:4326 fails at runtime.

### Changed

- `UnsupportedCRSError` is no longer raised for non-EPSG:4326 inputs; it is
  now raised only when the CRS definition itself cannot be resolved by the
  installed geospatial stack.  Callers that previously called
  `gdf.to_crs("EPSG:4326")` before `gpm.write` can keep doing so — the
  behaviour is unchanged — but the call is no longer required.
- `gpm.write(layers, output, ...)` public API that writes PMTiles vector
  archives from one or more GeoPandas GeoDataFrames using GDAL's native PMTiles
  vector driver - no subprocesses, no temporary files.
- Real-world ERA5 climate and upstream Tippecanoe semantic conformance tests
  with tracked fixture provenance and normalized PMTiles summaries.
- Support for `pathlib.Path` and binary-stream (`BytesIO`) output modes.
- Multiple named layers via a `dict[str, GeoDataFrame]` mapping; layer names
  are passed through from the mapping keys exactly.
- Archive-wide `min_zoom` / `max_zoom` options (defaults: 0 / 8, matching the
  climate-monitor contract of z0-8 for general data).
- `name` and `description` metadata options stored in the archive.
- `attribution: str` parameter for `gpm.write`; stores a TileJSON-compliant
  attribution string in the archive's metadata block under the `"attribution"`
  key.  Unicode and HTML are preserved exactly.  When omitted or set to `""`
  (the default) the key is not added.  Attribution is injected after GDAL
  writes the archive using the official `pmtiles.reader` / `pmtiles.tile` APIs:
  only the metadata section is re-encoded; all MVT tile payloads are preserved
  byte-for-byte.  Closes #5.
- `json_fields: Collection[str] | None` parameter for explicit JSON encoding of
  list/dict columns.  `None` (default) auto-encodes all such columns;
  an explicit set restricts JSON treatment to named columns and raises
  `UnsupportedPropertyTypeError` for unlisted list/dict columns.  GDAL's
  internal list handling is never allowed to leak through.
- `on_overflow: Literal["error", "unsafe"]` parameter (default
  `"error"`) for the GDAL tile-level drop policy.  Fixed spike-validated
  per-tile caps: `MAX_FEATURES=300,000` and `MAX_SIZE=10 MB`. `"error"`
  raises `TileOverflowError` before writing; `"unsafe"` keeps writing after
  GDAL reports a tile-limit action.
- `TileOverflowError` exception documenting tested POC caps, GDAL's
  silent-drop limitation, and why post-write enforcement is not possible.
- Explicit CRS validation: `MissingCRSError` is raised when a source CRS is
  absent, and `UnsupportedCRSError` is raised when its definition cannot be
  resolved.
- `EmptyLayerError` for empty layer mappings or GeoDataFrames with no features.
- Deterministic property normalisation: numpy scalars and
  `datetime`/`pd.Timestamp` normalised to Python native types before OGR
  `SetField` (which rejects them raw); `str`, `bool` (0/1 integer),
  `int`/`np.integer` (Integer64), `float`/`np.float_` (Real, NaN to null),
  `datetime` → ISO 8601 string, `list`/`dict` → JSON-encoded string,
  `None`/`pd.NA` (null field).  `UnsupportedPropertyTypeError` for other types.
- Features are written in input (DataFrame row) order for deterministic output.
- `simplification` keyword argument to enable GDAL-side geometry simplification
  (disabled by default).
- Honest documentation of known limitations: silent tile-level feature drops
  (GDAL cannot signal overflow) and feature count inflation when reading back
  (MVT stores features per tile).

### Changed

- `gpm.write()` now rejects GDAL feature-cap rebuilds and size-driven
  geometry recoding by default, before changing a Path or stream destination.
  `on_overflow="unsafe"` is the explicit warned opt-out for lossy GDAL output.
- Point-layer export batches geometry conversion and property preparation to
  reduce Python overhead while preserving output semantics.
- Removed GDAL as a hard pip dependency, made `gpm.write()` importable
  without GDAL, and switched CI test jobs to provision native GDAL separately.

### Fixed

- Boolean properties, including NumPy and pandas nullable booleans, now encode
  as native MVT booleans. Nulls remain absent, including all-null pandas
  `BooleanDtype` columns; numeric `0`/`1` columns remain numeric; and mixed
  scalar boolean/non-boolean columns raise an error instead of being silently
  coerced.
- Structured list/dict columns now force String fields before writing, so mixed
  columns round-trip as explicit JSON strings instead of being silently coerced
  into numeric zeroes by OGR.

### Removed

### Security
