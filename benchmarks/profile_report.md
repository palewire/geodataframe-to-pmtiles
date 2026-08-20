# PMTiles Point-Layer Benchmark

Recorded 2026-08-20 on macOS arm64 with GDAL 3.12.2, Python 3.14.5, Shapely
2.1.2, and GeoPandas 1.1.4.

## Workload

`benchmarks/bench_write_pmtiles.py` generates deterministic global point
layers (seed 42). Each feature has an integer id, a string label, and a float
value. The benchmark writes z0-8 archives to both a `Path` and `BytesIO`.

The implementation batch-converts geometries to WKB, filters missing and empty
geometries in one operation, and prepares property columns before the
per-feature GDAL write. It does not change the archive format or public API.

## Reproducible fast run

The following is the median of three timed runs after one warm-up from:

```sh
python benchmarks/bench_write_pmtiles.py --fast
```

| scale | output | wall time | Python peak memory | archive | tiles |
| ----: | :----- | --------: | -----------------: | ------: | ----: |
| 1,000 | Path | 0.409 s | 1.0 MiB | 496 KiB | 3,726 |
| 1,000 | BytesIO | 0.307 s | 1.0 MiB | 496 KiB | 3,726 |
| 10,000 | Path | 2.911 s | 7.5 MiB | 3,853 KiB | 20,609 |
| 10,000 | BytesIO | 3.130 s | 7.5 MiB | 3,853 KiB | 20,609 |

`tracemalloc` measures Python allocations, not total process memory. Timing is
environment-dependent, so these figures are a repeatable reference for this
workload, not a cross-machine speedup claim.

## Reproducing

Use a Python environment that has GDAL with the PMTiles driver plus the locked
test dependencies:

```sh
python benchmarks/bench_write_pmtiles.py --fast
python benchmarks/bench_write_pmtiles.py
```
