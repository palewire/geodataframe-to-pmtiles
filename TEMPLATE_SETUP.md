# Instantiating This Template

This checklist tracks template setup for `geodataframe-to-pmtiles`.

## Package

- [x] Replace placeholder metadata in `pyproject.toml`.
- [x] Create `src/geodataframe_to_pmtiles/` and enable setuptools package discovery.
- [x] Set coverage source (`geodataframe_to_pmtiles`) and ty include path.
- [x] Add `py.typed` — the package exposes a typed public API.

## Documentation

- [x] Replace the distribution placeholder in `docs/conf.py`.
- [x] Add API reference page (`docs/api.md`) with autosummary.
- [ ] Configure S3 deployment through the protected `docs-production`
      environment, AWS OIDC variables, and `DOCS_DEPLOY_ENABLED=true`.

## Continuous Integration

- [ ] Set the `PACKAGE_IMPORT_NAME` repository variable to
      `geodataframe_to_pmtiles` to enable wheel-import and coverage CI checks.
- [ ] Configure required checks and review rules for the default branch.

## Release

- [ ] Review `RELEASING.md` and verify the PyPI publication configuration.
- [ ] Confirm `CHANGELOG.md` and issue/PR templates match the project workflow.
