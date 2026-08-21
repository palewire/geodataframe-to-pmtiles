# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `gpm.check()` and `python -m geodataframe_to_pmtiles check` provide
  post-install GDAL and PMTiles diagnostics with a stable structured report.

### Changed

- Package metadata and repository documentation now link to the live
  single-page documentation site.

## [0.1.0] - 2026-08-20

### Added

- `import geodataframe_to_pmtiles as gpm; gpm.write(...)` writes one named
  GeoDataFrame or a mapping of named GeoDataFrames to PMTiles. There is no
  `write_pmtiles` compatibility alias.
- Optional TileJSON attribution, deterministic JSON encoding for structured
  properties, and native MVT booleans (including nullable pandas booleans).
- Automatic reprojection from any explicit, resolvable CRS to EPSG:4326 without
  mutating inputs.

### Changed

- The default overflow policy rejects GDAL-reported feature drops and
  size-driven geometry recoding before a path or stream destination changes;
  `on_overflow="unsafe"` is the explicit lossy opt-out.
- Features wholly outside Web Mercator's ±85.05112877980659° latitude extent
  are warned about and skipped; a layer with no remaining features raises
  `EmptyLayerError`, while boundary-crossing geometries are passed to GDAL for
  clipping.
- The reproducible point benchmark records modest, workload-specific reference
  measurements rather than making a general throughput or capacity claim.
