# Instantiating This Template

This checklist tracks template setup for `geodataframe-to-pmtiles`.

## Package

- [x] Replace placeholder metadata in `pyproject.toml`.
- [x] Create `src/geodataframe_to_pmtiles/` and enable setuptools package discovery.
- [x] Set coverage source (`geodataframe_to_pmtiles`) and ty include path.
- [x] Add `py.typed` — the package exposes a typed public API.

## Documentation

- [x] Replace the distribution placeholder in `docs/conf.py`.
- [x] Consolidate the documentation into the single `docs/index.md` source page.
- [x] Configure the protected `docs-production` environment (restricted to
      `main`), AWS OIDC role `github-geodataframe-to-pmtiles`, `us-east-1`
      region, and S3 target `palewire-docs/geodataframe-to-pmtiles`.
- [x] Keep the public documentation path
      `https://palewi.re/docs/geodataframe-to-pmtiles/` distinct from the S3
      origin prefix `geodataframe-to-pmtiles`: `/docs/` belongs to the public
      URL routing and is not part of `DOCS_AWS_BASE_PATH`.
- [ ] **Deployment activation and first deploy pending:** Keep
      `DOCS_DEPLOY_ENABLED=false` until explicit human approval, then enable it
      and verify the first deployment at the canonical documentation URL.

## Continuous Integration

- [x] Configure the workflow to test against conda-forge GDAL 3.12.2.
- [x] Set the `PACKAGE_IMPORT_NAME=geodataframe_to_pmtiles` repository variable
      to enable wheel-import and coverage CI checks.
- [ ] **External setup pending:** Configure required checks and review rules for
      the default branch.

## Release

- [x] Document setuptools-scm versioning, release validation, trusted-publishing
      prerequisites, and human approval boundaries in `RELEASING.md`.
- [x] Confirm `CHANGELOG.md` and issue/PR templates match the project workflow.
- [x] Configure the protected `pypi` environment for semantic-version tags and
      attach the tag-only PyPI release job to it.
- [x] Configure PyPI Trusted Publishing for
      `palewire/geodataframe-to-pmtiles`,
      `.github/workflows/continuous-deployment.yaml`, and the `pypi`
      environment.
