# Releasing

This project follows [Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). It is release-ready
only after the repository checks below pass and the required external services
are configured.

## Approval boundary

Preparing code, documentation, and artifacts does **not** authorize a release.
Only a maintainer with explicit human approval may create a tag, create a
GitHub Release, or publish to PyPI. Automation and agents may run the
validation commands and update this repository, but must stop before those
actions.

## Versioning and tags

Versions are derived solely from Git tags by
[setuptools-scm](https://setuptools-scm.readthedocs.io/). Do not edit a version
file or add a static `version` field to `pyproject.toml`.

- A clean commit tagged `0.1.0` builds package version `0.1.0`.
- An untagged commit after that tag builds a setuptools-scm development version
  containing the next version and local Git revision information.
- The tag must be an approved, exact semantic version such as `0.1.0`; do not
  prepend `v` unless the project's tag convention and version checks are
  changed together.

Before requesting approval, confirm that the intended tag points to the exact
reviewed commit and that the changelog has a matching dated section.

## Repository validation

Run the following from a clean checkout:

```sh
make check
make verify
make docs-check
make package-check PACKAGE=geodataframe_to_pmtiles
make coverage PACKAGE=geodataframe_to_pmtiles
```

`make build` creates both a wheel and source distribution and runs `twine
check`. Inspect their contents before release: the source distribution must
include the package, `py.typed`, license, README, and changelog. The wheel must
include the package, `py.typed`, its license file, and README-derived metadata.
Neither artifact should include tests, fixtures, benchmarks, local
environments, or generated archives.

`geodataframe_to_pmtiles` deliberately has no PyPI GDAL dependency: the
package imports without GDAL, while `gpm.write()` requires a native GDAL
runtime with the PMTiles vector driver.

The `PACKAGE_IMPORT_NAME=geodataframe_to_pmtiles` repository variable is
configured, so CI runs its wheel-import and coverage gates. Run the commands
above locally before seeking release approval as well.

## Native GDAL validation

The supported CI runtime is **GDAL 3.12.2 from conda-forge**. Reproduce the
integration test lane in an activated conda-forge environment:

```sh
gdal-config --version
# Expected: 3.12.2
make install-test GDAL_PYTHON="$CONDA_PREFIX/bin/python"
make test GDAL_PYTHON="$CONDA_PREFIX/bin/python"
```

This test run exercises real `gpm.write()` calls through GDAL's native PMTiles
driver. Do not treat a successful import-only package check as a substitute for
this runtime validation.

## Trusted publishing and release workflow

The `release` job in `.github/workflows/continuous-deployment.yaml` publishes
the build artifact from a pushed tag using PyPI's OIDC trusted-publishing
action. It uses the protected `pypi` environment and receives only an OIDC
`id-token: write` permission plus read-only access to repository contents. The
release job reuses the build artifact and checks its metadata before publishing.

The external publishing setup is complete:

- `PACKAGE_IMPORT_NAME=geodataframe_to_pmtiles` enables the wheel-import and
  coverage CI gates.
- The `pypi` environment allows only semantic-version tags.
- PyPI Trusted Publishing is configured for owner `palewire`, repository
  `geodataframe-to-pmtiles`, workflow
  `.github/workflows/continuous-deployment.yaml`, and environment `pypi`.

Never replace the trusted publisher with a long-lived PyPI token in repository
secrets.

After explicit approval and only then, a maintainer should create the approved
tag, monitor the workflow, verify the published version and metadata on PyPI,
and create any GitHub Release required by project policy.

## Documentation deployment

`.github/workflows/docs.yaml` builds Sphinx documentation on pushes and pull
requests, and deploys successful `main` builds to the live site:
[`https://palewi.re/docs/geodataframe-to-pmtiles/`](https://palewi.re/docs/geodataframe-to-pmtiles/).
The `docs-production` environment is restricted to `main`; AWS OIDC uses
`arn:aws:iam::989419493461:role/github-geodataframe-to-pmtiles` in `us-east-1`;
and the environment secrets target the `palewire-docs` bucket under
`geodataframe-to-pmtiles`. Cloudflare's public route includes `/docs/`
(`https://palewi.re/docs/geodataframe-to-pmtiles/`), but that route segment is
not part of the S3 origin prefix (`DOCS_AWS_BASE_PATH=geodataframe-to-pmtiles`).
