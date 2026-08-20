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
- [x] Configure the protected `docs-production` environment (restricted to
      `main`), AWS OIDC role `github-geodataframe-to-pmtiles`, `us-east-1`
      region, and S3 target `palewire-docs/docs/geodataframe-to-pmtiles`.
- [ ] **Deployment activation and first deploy pending:** Keep
      `DOCS_DEPLOY_ENABLED=false` until explicit human approval, then enable it
      and verify the first deployment at the canonical documentation URL.

## Continuous Integration

- [x] Configure the workflow to test against conda-forge GDAL 3.12.2.
- [ ] **External setup pending:** Set the `PACKAGE_IMPORT_NAME` repository variable to
      `geodataframe_to_pmtiles` to enable wheel-import and coverage CI checks.
- [ ] **External setup pending:** Configure required checks and review rules for
      the default branch.

## Release

- [x] Document setuptools-scm versioning, release validation, trusted-publishing
      prerequisites, and human approval boundaries in `RELEASING.md`.
- [x] Confirm `CHANGELOG.md` and issue/PR templates match the project workflow.
- [ ] **External setup pending:** Configure PyPI Trusted Publishing for
      `palewire/geodataframe-to-pmtiles` and
      `.github/workflows/continuous-deployment.yaml`.
