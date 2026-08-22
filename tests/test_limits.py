"""Tests for the ``max_features`` / ``max_size`` lossless-limit controls.

Unit tests run without GDAL and cover validation, option generation, and
diagnostic behaviour.  Integration tests require a pinned GDAL runtime (3.8+
with the PMTiles driver) and are marked ``@pytest.mark.integration``.

GDAL semantics note
-------------------
``MAX_FEATURES`` and ``MAX_SIZE`` are ``unsigned int`` creation options stored
in ``OGRMVTWriteDataset``.  GDAL clamps them to ``max(1, atoi(value))`` and
``max(100, atoi(value))`` respectively.  Passing
``_GDAL_NO_LIMIT = 2_147_483_647`` (INT_MAX) makes the overflow condition
(``nFeaturesInTile >= m_nMaxFeatures``) effectively unreachable: no realistic
tile can contain 2 billion features or exceed 2 GB.  This is GDAL 3.8 source
behaviour confirmed in ``ogr/ogrsf_frmts/mvt/ogrmvtdataset.cpp`` (master).
"""

from __future__ import annotations

import io
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from geodataframe_to_pmtiles import TileOverflowError, write
from geodataframe_to_pmtiles import _writer as _wm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _points_gdf(n: int = 3) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": list(range(n))},
        geometry=[
            Point(float(i % 360) - 180.0, float((i * 13) % 170) - 85.0)
            for i in range(n)
        ],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Unit tests — validation (no GDAL required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_max_features_none_does_not_raise_validation_error() -> None:
    """None passes validation for max_features (no TypeError/ValueError)."""
    # Validation raises TypeError/ValueError immediately before touching GDAL.
    # Any other exception (RuntimeError from missing GDAL, etc.) means validation passed.
    try:
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=None)
    except (TypeError, ValueError) as exc:
        # Only re-raise if the message mentions max_features (our validation).
        if "max_features" in str(exc):
            raise


@pytest.mark.unit
def test_max_size_none_does_not_raise_validation_error() -> None:
    """None passes validation for max_size (no TypeError/ValueError)."""
    try:
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size=None)
    except (TypeError, ValueError) as exc:
        if "max_size" in str(exc):
            raise


@pytest.mark.unit
def test_max_features_positive_int_does_not_raise_validation_error() -> None:
    """A positive integer passes validation for max_features."""
    try:
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=500_000)
    except (TypeError, ValueError) as exc:
        if "max_features" in str(exc):
            raise


@pytest.mark.unit
def test_max_size_positive_int_does_not_raise_validation_error() -> None:
    """A positive integer passes validation for max_size."""
    try:
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size=20_000_000)
    except (TypeError, ValueError) as exc:
        if "max_size" in str(exc):
            raise


@pytest.mark.unit
def test_max_features_zero_raises_value_error() -> None:
    """Zero is rejected for max_features."""
    with pytest.raises(ValueError, match="max_features"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=0)


@pytest.mark.unit
def test_max_size_zero_raises_value_error() -> None:
    """Zero is rejected for max_size."""
    with pytest.raises(ValueError, match="max_size"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size=0)


@pytest.mark.unit
def test_max_features_negative_raises_value_error() -> None:
    """Negative int is rejected for max_features."""
    with pytest.raises(ValueError, match="max_features"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=-1)


@pytest.mark.unit
def test_max_size_negative_raises_value_error() -> None:
    """Negative int is rejected for max_size."""
    with pytest.raises(ValueError, match="max_size"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size=-100)


@pytest.mark.unit
def test_max_features_bool_raises_type_error() -> None:
    """bool is rejected for max_features (bool is a subclass of int)."""
    with pytest.raises(TypeError, match="max_features"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=True)  # type: ignore[arg-type]


@pytest.mark.unit
def test_max_size_bool_raises_type_error() -> None:
    """bool is rejected for max_size (bool is a subclass of int)."""
    with pytest.raises(TypeError, match="max_size"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size=False)  # type: ignore[arg-type]


@pytest.mark.unit
def test_max_features_float_raises_type_error() -> None:
    """float is rejected for max_features."""
    with pytest.raises(TypeError, match="max_features"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_features=1.0)  # type: ignore[arg-type]


@pytest.mark.unit
def test_max_size_string_raises_type_error() -> None:
    """str is rejected for max_size."""
    with pytest.raises(TypeError, match="max_size"):
        write(_points_gdf(), io.BytesIO(), layer="lyr", max_size="10MB")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests — GDAL option generation and diagnostic semantics
# (inspect module-level constants)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gdal_no_limit_constant_is_int_max() -> None:
    """_GDAL_NO_LIMIT is INT_MAX (2^31 - 1), the atoi-safe sentinel."""
    assert _wm._GDAL_NO_LIMIT == 2_147_483_647


@pytest.mark.unit
def test_default_max_features_constant() -> None:
    """Default max_features is 300,000."""
    assert _wm._MAX_FEATURES == 300_000


@pytest.mark.unit
def test_default_max_size_constant() -> None:
    """Default max_size is 10,000,000 bytes (10 MB)."""
    assert _wm._MAX_SIZE == 10_000_000


@pytest.mark.unit
def test_disabled_feature_limit_produces_no_violation() -> None:
    """_GDALDiagnosticCapture.violations() ignores feature diagnostics when max_features=None."""

    class _FakeGdal:
        CE_Failure = 3

    capture = _wm._GDALDiagnosticCapture(
        _FakeGdal(), max_features=None, max_size=10_000_000
    )
    # Inject a synthetic GDAL MVT feature-count diagnostic.
    capture._events.append(
        (
            2,
            "MVT: For tile 0/0/0, feature count limit of 300000 is reached",
        )
    )
    # With max_features=None the violation must be suppressed.
    assert capture.violations() == ()


@pytest.mark.unit
def test_disabled_size_limit_produces_no_violation() -> None:
    """_GDALDiagnosticCapture.violations() ignores size diagnostics when max_size=None."""

    class _FakeGdal:
        CE_Failure = 3

    capture = _wm._GDALDiagnosticCapture(
        _FakeGdal(), max_features=300_000, max_size=None
    )
    capture._events.append(
        (
            2,
            "MVT: Recoding tile 0/0/0 with extent = 2048. From 11000000 to 9000000 bytes",
        )
    )
    assert capture.violations() == ()


@pytest.mark.unit
def test_finite_feature_limit_produces_accurate_violation() -> None:
    """A finite max_features is reflected accurately in TileLimitViolation.requested."""
    from geodataframe_to_pmtiles import TileLimitViolation

    class _FakeGdal:
        CE_Failure = 3

    capture = _wm._GDALDiagnosticCapture(
        _FakeGdal(), max_features=500, max_size=10_000_000
    )
    capture._events.append(
        (
            2,
            "MVT: For tile 0/0/0, feature count limit of 500 is reached",
        )
    )
    violations = capture.violations()
    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, TileLimitViolation)
    assert v.limit == "MAX_FEATURES"
    assert v.requested == 500
    assert v.observed == 500
    assert v.tile == (0, 0, 0)


@pytest.mark.unit
def test_finite_size_limit_produces_accurate_violation() -> None:
    """A finite max_size is reflected accurately in TileLimitViolation.requested."""
    from geodataframe_to_pmtiles import TileLimitViolation

    class _FakeGdal:
        CE_Failure = 3

    capture = _wm._GDALDiagnosticCapture(
        _FakeGdal(), max_features=300_000, max_size=200
    )
    capture._events.append(
        (
            2,
            "MVT: Recoding tile 0/0/0 with extent = 2048. From 500 to 300 bytes",
        )
    )
    violations = capture.violations()
    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, TileLimitViolation)
    assert v.limit == "MAX_SIZE"
    assert v.requested == 200
    assert v.observed == 500


@pytest.mark.unit
def test_both_limits_none_produces_no_violations_from_diagnostics() -> None:
    """With both limits disabled, any incidentally emitted diagnostics are suppressed."""

    class _FakeGdal:
        CE_Failure = 3

    capture = _wm._GDALDiagnosticCapture(_FakeGdal(), max_features=None, max_size=None)
    capture._events.extend(
        [
            (2, "MVT: For tile 0/0/0, feature count limit of 999 is reached"),
            (
                2,
                "MVT: Recoding tile 0/0/0 with extent = 2048. From 99999 to 50000 bytes",
            ),
        ]
    )
    assert capture.violations() == ()


@pytest.mark.unit
def test_write_defaults_unchanged() -> None:
    """Omitting max_features / max_size preserves the 300,000 / 10 MB defaults."""
    import inspect

    sig = inspect.signature(write)
    assert sig.parameters["max_features"].default == 300_000
    assert sig.parameters["max_size"].default == 10_000_000


# ---------------------------------------------------------------------------
# Integration tests — require GDAL ≥ 3.8 with the PMTiles driver
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_both_limits_disabled_writes_without_overflow(tmp_path: Path) -> None:
    """No TileOverflowError is raised when both limits are disabled (small sanity dataset)."""
    out = tmp_path / "no_limits.pmtiles"
    gdf = gpd.GeoDataFrame(
        {"id": list(range(10))},
        geometry=[Point(float(i), 0.0) for i in range(10)],
        crs="EPSG:4326",
    )
    write({"lyr": gdf}, out, min_zoom=0, max_zoom=0, max_features=None, max_size=None)
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.integration
def test_max_features_none_independently_disabled(tmp_path: Path) -> None:
    """max_features=None disables only the feature cap; max_size retains its value."""
    out = tmp_path / "no_feature_limit.pmtiles"
    clustered = gpd.GeoDataFrame(
        {"id": list(range(5))},
        geometry=[Point(0.0, 0.0)] * 5,
        crs="EPSG:4326",
    )
    # max_features=None should not raise; a finite max_size remains in effect.
    write(
        {"lyr": clustered},
        out,
        min_zoom=0,
        max_zoom=0,
        max_features=None,
        max_size=10_000_000,
    )
    assert out.exists()


@pytest.mark.integration
def test_max_size_none_independently_disabled(tmp_path: Path) -> None:
    """max_size=None disables only the byte cap; max_features retains its value."""
    from shapely.geometry import LineString

    out = tmp_path / "no_size_limit.pmtiles"
    coords = [
        (float(i % 360) - 180.0, float((i * 17) % 170) - 85.0) for i in range(500)
    ]
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString(coords)],
        crs="EPSG:4326",
    )
    write(
        {"lyr": gdf}, out, min_zoom=0, max_zoom=0, max_features=300_000, max_size=None
    )
    assert out.exists()


@pytest.mark.integration
def test_lossless_300k_polygon_z0_with_both_limits_disabled(tmp_path: Path) -> None:
    """Pinned-GDAL 3.12.2: 359,206 z0 polygons write and decode with all unique IDs.

    This test proves the production use-case described in the goal: a
    ``newsapps_reuters-climate-monitor`` closed-streak cell map after Web
    Mercator clipping at z0 has more than 359,000 polygons, which exceeds the
    default ``MAX_FEATURES=300,000``.  With both limits disabled, every source
    feature is retained in the decoded z0 tile.

    The unique-ID check ensures this cannot pass via MVT tile duplication:
    each feature carries a unique integer ``id``, and the set of decoded IDs
    must equal the set of source IDs.  An archive that merely inflates feature
    counts via tile-overlap repetition would still contain only the original
    IDs (or fewer), not more.

    Geometry design
    ---------------
    At z=0 with ``EXTENT=4096``, GDAL quantizes coordinates to integer tile
    units where 1 unit ≈ 360°/4096 ≈ 0.0879°.  Polygons narrower than 1
    tile unit are quantized to zero-area shapes and silently dropped.  We
    therefore build a 600-column grid with spacing = 3 tile units ≈ 0.264°
    per cell and polygon side = 1.5 tile units ≈ 0.132°.  This ensures every
    polygon survives quantization while keeping all 359,206 features in the
    single z0 tile (0, 0, 0).
    """
    import gzip as _gzip

    from pmtiles.reader import MemorySource, Reader

    try:
        from mapbox_vector_tile import decode
    except ImportError:
        pytest.skip("mapbox_vector_tile not installed")

    n_target = 359_206  # Production input size (climate-monitor PR #715)

    # Grid geometry: 3-tile-unit cell spacing, 1.5-tile-unit polygon side.
    # 1 tile unit = 360° / 4096 ≈ 0.0879°.
    tile_unit = 360.0 / 4096
    spacing = 3 * tile_unit  # ≈ 0.264° per cell
    side = 1.5 * tile_unit  # ≈ 0.132° polygon side

    cols = 600
    polygons: list[Polygon] = []
    ids: list[int] = []
    idx = 0
    for row in range(n_target // cols + 2):
        for col in range(cols):
            if idx >= n_target:
                break
            lon = col * spacing - 180.0 + spacing
            lat = row * spacing - 80.0 + spacing
            if lat + side > 83.0:
                break
            polygons.append(
                Polygon(
                    [
                        (lon, lat),
                        (lon + side, lat),
                        (lon + side, lat + side),
                        (lon, lat + side),
                        (lon, lat),
                    ]
                )
            )
            ids.append(idx)
            idx += 1
        else:
            continue
        break

    n = len(polygons)
    assert n > 300_000, f"Expected >300,000 polygons, got {n:,}"

    gdf = gpd.GeoDataFrame({"id": ids}, geometry=polygons, crs="EPSG:4326")

    out = tmp_path / "lossless_359k.pmtiles"
    write(
        {"cells": gdf},
        out,
        min_zoom=0,
        max_zoom=0,
        max_features=None,
        max_size=None,
    )

    assert out.exists(), "archive was not written"
    assert out.stat().st_size > 0

    # Decode the z0 tile and verify every source ID is present.
    data = out.read_bytes()
    reader = Reader(MemorySource(data))
    header = reader.header()
    raw_tile = reader.get(0, 0, 0)
    assert raw_tile is not None, "z0 tile 0/0/0 is missing from the archive"

    if header["tile_compression"].name.lower() == "gzip":
        raw_tile = _gzip.decompress(raw_tile)

    layers = decode(raw_tile)
    assert "cells" in layers, f"layer 'cells' not found; got {list(layers)}"

    features = layers["cells"]["features"]
    # MVT allows a feature to appear in multiple tiles; at z0 there is only
    # one tile, so each source feature appears exactly once.  Use a set to
    # guard against any unexpected duplication.
    decoded_ids = {f["properties"].get("id") for f in features}

    source_ids = set(ids)
    missing = source_ids - decoded_ids
    assert not missing, (
        f"{len(missing):,} source IDs are absent from the decoded z0 tile "
        f"(e.g. {sorted(missing)[:5]}). "
        "GDAL may have silently dropped features — check the limit configuration."
    )
    # Every decoded ID must also be a valid source ID (no phantom features).
    phantom = decoded_ids - source_ids
    assert not phantom, (
        f"{len(phantom):,} unexpected IDs in decoded tile (e.g. {sorted(phantom)[:5]})."
    )


@pytest.mark.integration
def test_default_limits_still_raise_for_301k_features(tmp_path: Path) -> None:
    """Regression: omitting max_features still triggers TileOverflowError at 301k."""
    n = 301_000
    gdf = gpd.GeoDataFrame(
        {"id": range(n)},
        geometry=[
            Point(float(i % 360) - 180.0, float((i // 360) % 170) - 85.0)
            for i in range(n)
        ],
        crs="EPSG:4326",
    )
    out = tmp_path / "default_overflow.pmtiles"

    with pytest.raises(TileOverflowError) as caught:
        write({"lyr": gdf}, out, min_zoom=0, max_zoom=0)

    assert caught.value.violations[0].limit == "MAX_FEATURES"
    assert caught.value.violations[0].requested == 300_000
    assert not out.exists()
