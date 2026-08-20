# Contributing

Install the development dependencies from a clone:

```sh
make install
```

Install the local hooks if desired:

```sh
uv run pre-commit install
```

Run the fast, non-mutating checks while developing:

```sh
make check
```

Run the complete local verification suite before opening a pull request:

```sh
make verify
```

`gpm.write()` needs a native GDAL runtime with the PMTiles driver. For
integration testing, use conda-forge GDAL 3.12.2 as described in
[RELEASING.md](RELEASING.md).

## Documentation

The documentation source is in `docs/` and is built by the repository's
documentation workflow. Build it locally with:

```sh
make docs-check
```

Deployment infrastructure is configured, but deployment remains disabled while
`DOCS_DEPLOY_ENABLED=false`. After its approved first deployment, the public
site will be available at
`https://palewi.re/docs/geodataframe-to-pmtiles/`. See
[`TEMPLATE_SETUP.md`](TEMPLATE_SETUP.md) and [`RELEASING.md`](RELEASING.md).

## Releasing

Follow [`RELEASING.md`](RELEASING.md). It documents the tag-derived version
scheme, conda-forge GDAL 3.12.2 validation, external PyPI Trusted Publishing
prerequisite, and the actions that require explicit human approval.
