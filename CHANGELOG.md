# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `write_pmtiles(layers, output, ...)` public API that writes PMTiles vector
  archives from one or more GeoPandas GeoDataFrames using GDAL's native PMTiles
  vector driver - no subprocesses, no temporary files.
- Support for `pathlib.Path` and binary-stream (`BytesIO`) output modes.
- Multiple named layers via a `dict[str, GeoDataFrame]` mapping; layer names
  are passed through from the mapping keys exactly.
- Archive-wide `min_zoom` / `max_zoom` options (defaults: 0 / 8, matching the
  climate-monitor contract of z0-8 for general data).
- `name` and `description` metadata options stored in the archive.
- `json_fields: Collection[str] | None` parameter for explicit JSON encoding of
  list/dict columns.  `None` (default) auto-encodes all such columns;
  an explicit set restricts JSON treatment to named columns and raises
  `UnsupportedPropertyTypeError` for unlisted list/dict columns.  GDAL's
  internal list handling is never allowed to leak through.
- `on_overflow: Literal["error", "warn", "ignore"]` parameter (default
  `"warn"`) for the GDAL tile-level drop policy.  Per-tile `MAX_FEATURES` is
  derived from input (`max(300_000, total_features)`); `MAX_SIZE` is 10 MB.
  `"error"` raises `TileOverflowError` before writing so callers must opt in.
- `TileOverflowError` exception (subclass of `WritePMTilesError`).
- Explicit CRS validation: only EPSG:4326 is accepted; `MissingCRSError`
  (explicit source CRS required) and `UnsupportedCRSError` are raised for
  invalid inputs.
- `EmptyLayerError` for empty layer mappings or GeoDataFrames with no features.
- Deterministic property normalisation: `str`, `bool` (as 0/1 integer),
  `int` / `np.integer` (Integer64), `float` / `np.float_` (Real, NaN to null),
  `datetime` (ISO 8601 string), `list` / `dict` (JSON-encoded, explicit),
  `None` / `pd.NA` (null), with `UnsupportedPropertyTypeError` for other types.
- Features are written in input (DataFrame row) order for deterministic output.
- `simplification` keyword argument to enable GDAL-side geometry simplification
  (disabled by default).
- Honest documentation of known limitations: silent tile-level feature drops
  (GDAL cannot signal overflow), feature count inflation when reading back (MVT
  stores features per tile), attribution unsupported (tested: GDAL 3.12.2 does
  not write `attribution` from `CONF` option to archive bytes).

### Changed

- Removed GDAL as a hard pip dependency, made `write_pmtiles()` importable
  without GDAL, and switched CI test jobs to provision native GDAL separately.

### Fixed

### Removed

### Security
