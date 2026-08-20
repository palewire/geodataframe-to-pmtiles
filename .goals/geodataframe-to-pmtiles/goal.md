# Goal: geodataframe to pmtiles

## User Request

Build the initial proof of concept for the new public repository
`palewire/geodataframe-to-pmtiles`, created from
`palewire/python-open-source-template`. Work autonomously in your branch and
deliver a focused, reviewable implementation with a commit and pull request.

Product goal: a generally useful Python API that writes PMTiles vector archives
from one or more GeoPandas GeoDataFrames without shelling out to Tippecanoe. It
must also be shaped so the Reuters climate monitor can later replace its
`dataframes_to_pmtiles(dict[str, GeoDataFrame], ...)` helper.

Required scope:
- Fully adapt all template metadata and `TEMPLATE_SETUP.md` for distribution
  name `geodataframe-to-pmtiles` and import package `geodataframe_to_pmtiles`;
  use `src` layout.
- Provide a small typed public API, preferably `write_pmtiles(layers, output,
  ...)`, where `layers` is a mapping of layer names to GeoDataFrames and
  `output` accepts a `pathlib.Path` or binary stream/BytesIO.
- Use GDAL's native PMTiles vector driver through Python bindings. No
  subprocesses and no temporary GeoJSON files. Prefer GDAL Memory and
  `/vsimem` for intermediates/output where feasible.
- Support multiple named layers, explicit min/max zoom, no simplification by
  default for this POC, metadata including name/description/attribution if
  feasible, explicit CRS validation, empty-input errors, and clear exceptions.
- Define deterministic property normalization for MVT-supported scalar values.
  Reject unsupported values clearly rather than silently losing data; if JSON
  encoding of list/dict properties is included, make it explicit and tested.
- Avoid silent feature dropping. Investigate GDAL MAX_FEATURES/MAX_SIZE
  behavior and expose a safe POC policy or explicit limits/errors; document any
  remaining limitation honestly.
- Add focused tests that create and reopen a PMTiles archive, verify layer
  names, zoom metadata, properties, and both Path and BytesIO output. Include at
  least a two-layer archive.
- Add concise README usage and API documentation, CHANGELOG Unreleased entry,
  and ensure existing template quality gates are configured for the actual
  package.
- Use existing template tooling: uv, Ruff, ty, pytest, pre-commit. Do not add
  redundant tools.
- Run the smallest relevant checks and then the full template verification if
  practical.
- Commit with the required Copilot co-author trailer and open a PR. Do not
  publish packages, tags, or releases.

Important design guidance: keep the public API backend-neutral even though the
first implementation uses GDAL. Do not attempt a wholesale Tippecanoe clone.
Optimize for a coherent proof of concept and truthful errors over broad option
coverage. Message the creator with blockers or major design decisions and send
the final PR URL plus a short capability/limitation summary.

## Refined Goal

Create a first, reviewable implementation of `geodataframe_to_pmtiles` that can
write PMTiles vector archives from one or more GeoPandas GeoDataFrames using
GDAL directly, with no shelling out or temp GeoJSON files. The library should
feel like a small, typed Python API that accepts a layer mapping and either a
filesystem path or binary output stream, validates CRS explicitly, preserves
feature order deterministically, and makes data normalization rules obvious.

The proof of concept should support multiple named layers, archive-wide zoom
ranges, clear handling of empty inputs, and honest treatment of unsupported or
lossy cases. It should be documented, tested, wired into the template's quality
gates, and delivered in a single focused commit plus pull request.

## Acceptance Criteria

- [ ] The package metadata, source layout, and template setup are fully renamed
      to `geodataframe-to-pmtiles` / `geodataframe_to_pmtiles`.
- [ ] `write_pmtiles(...)` exists as a typed public API and can write a
      multi-layer PMTiles archive to both `Path` and `BytesIO` outputs.
- [ ] The implementation uses GDAL's PMTiles vector driver directly and does
      not shell out or write temp GeoJSON files.
- [ ] Tests cover a two-layer archive, reopen the archive, and verify layer
      names, zoom metadata, property normalization, feature order, and both
      output modes.
- [ ] Empty inputs, missing CRS, and unsupported property values fail with
      clear exceptions; JSON list/dict normalization is explicit if supported.
- [ ] README, docs, and CHANGELOG explain the public API and any remaining
      limitations honestly.
- [ ] The repository builds and passes the relevant template quality gates.

## Scope Boundaries

**In scope:**
- A small backend-neutral Python API for writing PMTiles from GeoDataFrames.
- GDAL-backed implementation details, tests, and documentation.
- Template metadata and tooling updates needed for this package.

**Out of scope:**
- Full Tippecanoe parity.
- Publishing to PyPI, creating tags, or releasing documentation.
- A broad compatibility layer for future backends beyond the POC shape.

## Applicable Project Conventions

**Quality gate command:**
- `make check`
- `make verify`
- `make package-verify PACKAGE=geodataframe_to_pmtiles`

**Commit convention:**
- conventional commits (default)
- Assisted-by trailer required: `Assisted-by: Claude:Sonnet-4.6`

**Guidelines:**
- `AGENTS.md`
- `TEMPLATE_SETUP.md`
- `README.md`
- `CHANGELOG.md`

**Rules:**
- Use `make install`, `make check`, and `make verify` from the template.
- Keep changes aligned across code, tests, docs, and packaging metadata.
- Do not publish packages, tags, or releases.
