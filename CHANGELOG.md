# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `write_pmtiles(layers, output, ...)` public API that writes PMTiles vector
  archives from one or more GeoPandas GeoDataFrames using GDAL's native PMTiles
  vector driver — no subprocesses, no temporary files.
- Support for `pathlib.Path` and binary-stream (`BytesIO`) output modes.
- Multiple named layers via a `dict[str, GeoDataFrame]` mapping.
- Archive-wide `min_zoom` / `max_zoom` options (defaults: 0 / 8).
- `name`, `description`, and `attribution` metadata options.
- Explicit CRS validation: only EPSG:4326 (geographic WGS 84) is accepted;
  `MissingCRSError` and `UnsupportedCRSError` are raised for invalid inputs.
- `EmptyLayerError` for empty layer mappings or GeoDataFrames with no features.
- Deterministic property normalisation: `str`, `bool` (as 0/1 integer),
  `int` / `np.integer` (as Integer64), `float` / `np.float_` (as Real,
  NaN → null), `datetime` (as ISO 8601 string), `list` / `dict` (JSON-encoded),
  `None` / `pd.NA` (null), with `UnsupportedPropertyTypeError` for other types.
- Features are written in input (DataFrame row) order for deterministic output.
- `simplification` keyword argument to enable GDAL-side geometry simplification
  (disabled by default).
- Honest documentation of GDAL PMTiles driver limitations: silent tile-level
  feature drops at high `MAX_SIZE`/`MAX_FEATURES`, duplicate feature counts
  when reading back, and attribution stored only in the TileJSON `CONF` blob.

### Changed

### Fixed

### Removed

### Security
