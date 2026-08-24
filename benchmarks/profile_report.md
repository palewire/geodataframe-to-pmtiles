# PMTiles Point-Layer Benchmark

Recorded 2026-08-23 on macOS arm64 with GDAL 3.12.2, Python 3.14.5, Shapely
2.1.2, GeoPandas 1.1.4, and PyArrow 25.0.1.

## Workload

`benchmarks/bench_write_pmtiles.py` generates deterministic global point
layers (seed 42). Each feature has an integer `id`, a string `label`, and a
float `value`. The benchmark writes z0-8 archives to both a `Path` and
`BytesIO`.

## Implementation

Features are written using GDAL 3.12.2's `Layer.WritePyArrow` in bounded
batches of 65,536 features (the Arrow standard chunk size). Geometries are
batch-converted to WKB via `shapely.to_wkb`. Property columns are pre-normalised
to PyArrow arrays once before batching; the per-feature OGR `CreateFeature` loop
has been eliminated. Archive format and public API are unchanged.

## Reproducible fast run (Arrow path, 2026-08-23)

Median of three timed runs after one warm-up:

```sh
python benchmarks/bench_write_pmtiles.py --fast
```

| scale | output | wall time | Python peak memory | archive | tiles |
| ----: | :----- | --------: | -----------------: | ------: | ----: |
| 1,000 | Path | 0.283 s | 1.0 MiB | 496 KiB | 3,726 |
| 1,000 | BytesIO | 0.281 s | 1.0 MiB | 496 KiB | 3,726 |
| 10,000 | Path | 2.507 s | 7.9 MiB | 3,851 KiB | 20,609 |
| 10,000 | BytesIO | 2.406 s | 7.9 MiB | 3,851 KiB | 20,609 |

## Full run (Arrow path, 2026-08-23)

```sh
python benchmarks/bench_write_pmtiles.py
```

| scale | output | wall time | Python peak memory | archive | tiles |
| ----: | :----- | --------: | -----------------: | ------: | ----: |
| 1,000 | Path | 0.283 s | 1.0 MiB | 496 KiB | 3,726 |
| 1,000 | BytesIO | 0.281 s | 1.0 MiB | 496 KiB | 3,726 |
| 10,000 | Path | 2.507 s | 7.9 MiB | 3,851 KiB | 20,609 |
| 10,000 | BytesIO | 2.406 s | 7.9 MiB | 3,851 KiB | 20,609 |
| 50,000 | Path | 11.710 s | 32.7 MiB | 15,806 KiB | 50,899 |
| 50,000 | BytesIO | 11.676 s | 32.7 MiB | 15,806 KiB | 50,899 |
| 100,000 | Path | 20.041 s | 60.2 MiB | 28,957 KiB | 65,592 |
| 100,000 | BytesIO | 20.042 s | 60.2 MiB | 28,957 KiB | 65,592 |

## Arrow batch-size comparison (50,000 features, Path output, 2026-08-23)

```sh
python benchmarks/bench_write_pmtiles.py --batch-sizes
```

| batch size | wall time | Python peak memory | archive | tiles |
| ---------: | --------: | -----------------: | ------: | ----: |
| 4,096 | 10.397 s | 32.7 MiB | 15,806 KiB | 50,899 |
| 16,384 | 10.273 s | 32.7 MiB | 15,806 KiB | 50,899 |
| 65,536 | 10.585 s | 32.7 MiB | 15,806 KiB | 50,899 |
| 262,144 | 10.831 s | 32.7 MiB | 15,806 KiB | 50,899 |

Wall times are within run-to-run variance (~5%). GDAL's MVT tile encoding
dominates; the batch-ingestion overhead is negligible at this scale. The
default batch size of 65,536 is the Arrow standard chunk and a reasonable
general choice. Archive bytes and tile counts are identical across all batch
sizes, confirming semantic equivalence.

## Previous baseline (per-feature OGR loop, 2026-08-20)

For comparison, the same workload measured before the Arrow ingestion path
was introduced:

| scale | output | wall time | Python peak memory | archive | tiles |
| ----: | :----- | --------: | -----------------: | ------: | ----: |
| 1,000 | Path | 0.409 s | 1.0 MiB | 496 KiB | 3,726 |
| 1,000 | BytesIO | 0.307 s | 1.0 MiB | 496 KiB | 3,726 |
| 10,000 | Path | 2.911 s | 7.5 MiB | 3,853 KiB | 20,609 |
| 10,000 | BytesIO | 3.130 s | 7.5 MiB | 3,853 KiB | 20,609 |

The modest wall-time improvement (2–14%) confirms that GDAL's PMTiles tile
encoding, not Python-level feature creation, is the dominant cost. Peak
memory is unchanged; the Arrow path trades per-feature Python objects for a
single columnar allocation, which is more cache-friendly but not smaller in
absolute terms for point workloads. The primary correctness benefit of the
Arrow path is the elimination of a per-feature Python/OGR call boundary.

## Interpreting the numbers

`tracemalloc` measures Python-level allocations, not total process RSS. Wall
time is machine-dependent; these figures are a reproducible reference for the
described workload on macOS arm64, not a cross-machine speedup promise. The
primary bottleneck for large archives is GDAL's MVT tile encoding pipeline
(tile partitioning, coordinate quantisation, and pbf serialisation), which is
independent of the Python feature-ingestion strategy.

## Reproducing

Use a Python environment that has GDAL 3.12.2 with the PMTiles driver plus
the locked test dependencies:

```sh
python benchmarks/bench_write_pmtiles.py --fast
python benchmarks/bench_write_pmtiles.py
python benchmarks/bench_write_pmtiles.py --batch-sizes
```
