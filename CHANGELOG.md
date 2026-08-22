# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `gpm.check()` and `python -m geodataframe_to_pmtiles check` provide
  post-install GDAL and PMTiles diagnostics with a stable structured report.
- `layer_zooms` parameter on `gpm.write()` accepts a
  `Mapping[str, LayerZoomSpec]` for per-layer minimum and maximum zoom
  overrides.  Omitted keys inherit the archive-wide `min_zoom` / `max_zoom`
  defaults.  Overrides are translated into a deterministic GDAL `CONF`
  creation option.  Unknown layer names, non-integer zoom values,
  out-of-range zooms, and effective min > max all raise the new
  `InvalidLayerZoomError` before any GDAL object is created.
- `InvalidLayerZoomError` is a new public exception raised when a
  `layer_zooms` entry is invalid.
- `LayerZoomSpec` is the typed per-layer override dict exported from the
  package for use in type annotations.

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
