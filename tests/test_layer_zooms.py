"""Tests for per-layer zoom overrides (layer_zooms parameter).

Unit tests cover validation logic without GDAL; integration tests require the
pinned GDAL 3.12.2 runtime (marked ``@pytest.mark.integration``).
"""

from __future__ import annotations

import importlib
import importlib.util
import io

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point

import geodataframe_to_pmtiles as gpm
from geodataframe_to_pmtiles import InvalidLayerZoomError
from geodataframe_to_pmtiles._writer import (
    LayerZoomSpec,
    _build_conf,
    _validate_layer_zooms,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pts(n: int = 3) -> gpd.GeoDataFrame:
    """Return *n* point features spread across the globe in EPSG:4326."""
    lons = np.linspace(-170.0, 170.0, n)
    lats = np.linspace(-80.0, 80.0, n)
    return gpd.GeoDataFrame(
        {"id": range(n)},
        geometry=[Point(lon, lat) for lon, lat in zip(lons, lats, strict=False)],
        crs="EPSG:4326",
    )


def _lines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(0, 0), (1, 1)])],
        crs="EPSG:4326",
    )


@pytest.fixture(autouse=True)
def _skip_integration_without_gdal(request: pytest.FixtureRequest) -> None:
    if (
        request.node.get_closest_marker("integration")
        and importlib.util.find_spec("osgeo") is None
    ):
        pytest.skip("GDAL Python bindings are not installed")


# ---------------------------------------------------------------------------
# Unit tests — _validate_layer_zooms
# ---------------------------------------------------------------------------


class TestValidateLayerZooms:
    def test_empty_mapping_returns_empty(self) -> None:
        result = _validate_layer_zooms({}, frozenset({"a"}), 0, 8)
        assert result == {}

    def test_both_keys_explicit(self) -> None:
        spec: LayerZoomSpec = {"minzoom": 2, "maxzoom": 6}
        result = _validate_layer_zooms({"lyr": spec}, frozenset({"lyr"}), 0, 8)
        assert result == {"lyr": (2, 6)}

    def test_minzoom_only_inherits_archive_max(self) -> None:
        spec: LayerZoomSpec = {"minzoom": 7}
        result = _validate_layer_zooms({"lyr": spec}, frozenset({"lyr"}), 0, 8)
        assert result == {"lyr": (7, 8)}

    def test_maxzoom_only_inherits_archive_min(self) -> None:
        spec: LayerZoomSpec = {"maxzoom": 5}
        result = _validate_layer_zooms({"lyr": spec}, frozenset({"lyr"}), 0, 8)
        assert result == {"lyr": (0, 5)}

    def test_empty_spec_inherits_both(self) -> None:
        spec: LayerZoomSpec = {}
        result = _validate_layer_zooms({"lyr": spec}, frozenset({"lyr"}), 2, 6)
        assert result == {"lyr": (2, 6)}

    def test_multiple_layers(self) -> None:
        specs: dict[str, LayerZoomSpec] = {
            "contours": {"minzoom": 0, "maxzoom": 8},
            "data": {"minzoom": 7},
        }
        result = _validate_layer_zooms(specs, frozenset({"contours", "data"}), 0, 8)
        assert result == {"contours": (0, 8), "data": (7, 8)}

    def test_unknown_layer_name_raises(self) -> None:
        with pytest.raises(InvalidLayerZoomError, match="unknown layer"):
            _validate_layer_zooms(
                {"ghost": {"minzoom": 0}},
                frozenset({"real"}),
                0,
                8,
            )

    def test_non_integer_minzoom_raises(self) -> None:
        with pytest.raises(InvalidLayerZoomError, match="must be an int"):
            _validate_layer_zooms(
                {"lyr": {"minzoom": "0"}},  # type: ignore[typeddict-item]
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_non_integer_maxzoom_raises(self) -> None:
        with pytest.raises(InvalidLayerZoomError, match="must be an int"):
            _validate_layer_zooms(
                {"lyr": {"maxzoom": 3.5}},  # type: ignore[typeddict-item]
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_bool_zoom_value_raises(self) -> None:
        # bool is a subclass of int; we reject it explicitly.
        with pytest.raises(InvalidLayerZoomError, match="must be an int"):
            _validate_layer_zooms(
                {"lyr": {"minzoom": True}},  # type: ignore[typeddict-item]
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_effective_min_below_zero_raises(self) -> None:
        with pytest.raises(InvalidLayerZoomError, match="out of range"):
            _validate_layer_zooms(
                {"lyr": {"minzoom": -1}},
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_effective_max_above_22_raises(self) -> None:
        with pytest.raises(InvalidLayerZoomError, match="out of range"):
            _validate_layer_zooms(
                {"lyr": {"maxzoom": 23}},
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_effective_min_gt_max_raises(self) -> None:
        # Explicit maxzoom < explicit minzoom
        with pytest.raises(InvalidLayerZoomError, match="exceeds effective maxzoom"):
            _validate_layer_zooms(
                {"lyr": {"minzoom": 8, "maxzoom": 3}},
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_effective_min_gt_archive_max_raises(self) -> None:
        # minzoom=9 but archive_max=8 → effective min > effective max
        with pytest.raises(InvalidLayerZoomError, match="exceeds effective maxzoom"):
            _validate_layer_zooms(
                {"lyr": {"minzoom": 9}},
                frozenset({"lyr"}),
                0,
                8,
            )

    def test_equal_min_max_accepted(self) -> None:
        result = _validate_layer_zooms(
            {"lyr": {"minzoom": 5, "maxzoom": 5}},
            frozenset({"lyr"}),
            0,
            8,
        )
        assert result == {"lyr": (5, 5)}

    def test_boundary_values_accepted(self) -> None:
        result = _validate_layer_zooms(
            {"lyr": {"minzoom": 0, "maxzoom": 22}},
            frozenset({"lyr"}),
            0,
            22,
        )
        assert result == {"lyr": (0, 22)}


# ---------------------------------------------------------------------------
# Unit tests — _build_conf
# ---------------------------------------------------------------------------


class TestBuildConf:
    def test_single_layer(self) -> None:
        conf = _build_conf({"data": (7, 8)})
        import json

        parsed = json.loads(conf)
        assert parsed == {"data": {"minzoom": 7, "maxzoom": 8}}

    def test_multiple_layers_sorted(self) -> None:
        import json

        conf = _build_conf({"zebra": (0, 4), "alpha": (2, 8)})
        parsed = json.loads(conf)
        assert list(parsed) == ["alpha", "zebra"]

    def test_deterministic(self) -> None:
        a = _build_conf({"b": (1, 5), "a": (0, 8)})
        b = _build_conf({"b": (1, 5), "a": (0, 8)})
        assert a == b

    def test_no_extra_whitespace(self) -> None:
        conf = _build_conf({"lyr": (0, 8)})
        assert " " not in conf


# ---------------------------------------------------------------------------
# Unit tests — write() validation (no GDAL required)
# ---------------------------------------------------------------------------


class TestWriteValidatesLayerZooms:
    """write() must raise InvalidLayerZoomError before any GDAL object is created."""

    def test_unknown_layer_name(self) -> None:
        gdf = _pts()
        buf = io.BytesIO()
        with pytest.raises(InvalidLayerZoomError, match="unknown layer"):
            gpm.write(
                {"real": gdf},
                buf,
                layer_zooms={"ghost": {"minzoom": 0}},
            )

    def test_invalid_zoom_type(self) -> None:
        gdf = _pts()
        buf = io.BytesIO()
        with pytest.raises(InvalidLayerZoomError, match="must be an int"):
            gpm.write(
                {"pts": gdf},
                buf,
                layer_zooms={"pts": {"maxzoom": "eight"}},  # type: ignore[typeddict-item]
            )

    def test_min_exceeds_max(self) -> None:
        gdf = _pts()
        buf = io.BytesIO()
        with pytest.raises(InvalidLayerZoomError, match="exceeds effective maxzoom"):
            gpm.write(
                {"pts": gdf},
                buf,
                layer_zooms={"pts": {"minzoom": 9, "maxzoom": 3}},
            )

    def test_no_layer_zooms_backward_compatible(self) -> None:
        """Callers that omit layer_zooms must not see any new errors."""
        # We only check that no exception is raised from our validation;
        # we do NOT call GDAL here (no integration marker needed).
        import importlib.util

        if importlib.util.find_spec("osgeo") is None:
            pytest.skip("GDAL not available")
        gdf = _pts(5)
        buf = io.BytesIO()
        gpm.write({"pts": gdf}, buf, on_overflow="unsafe")
        assert buf.tell() > 0


# ---------------------------------------------------------------------------
# Integration tests — real GDAL 3.12.2 archive
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.timeout(300)
def test_per_layer_zooms_contour_and_data() -> None:
    """Contour layer present z0-z8; data layer restricted to z7-z8.

    This is the production use case: contours are visible at all zooms while
    a >300,000-point data layer is only present at high zooms to avoid tile
    overflow at low zoom levels.

    Absence is verified by decoding specific tiles known to have contour data;
    the ``data`` layer must not appear in those tiles at z0-z6.  Presence is
    verified at z7 and z8 by checking tiles at known point coordinates.
    """
    from tests.pmtiles_semantics import read_pmtiles_bytes

    # --- Build >300,000-point data layer spread globally ---
    n_data = 320_000
    rng = np.random.default_rng(42)
    lons = rng.uniform(-170.0, 170.0, n_data)
    lats = rng.uniform(-80.0, 80.0, n_data)
    data_gdf = gpd.GeoDataFrame(
        {"id": np.arange(n_data)},
        geometry=[Point(lon, lat) for lon, lat in zip(lons, lats, strict=False)],
        crs="EPSG:4326",
    )

    # --- Build a small contour layer (lines, z0-z8) ---
    n_contours = 200
    contour_lons = np.linspace(-160.0, 160.0, n_contours)
    contours_gdf = gpd.GeoDataFrame(
        {"elevation": np.arange(n_contours) * 10},
        geometry=[LineString([(lon, -60.0), (lon, 60.0)]) for lon in contour_lons],
        crs="EPSG:4326",
    )

    buf = io.BytesIO()
    gpm.write(
        {"contours": contours_gdf, "data": data_gdf},
        buf,
        min_zoom=0,
        max_zoom=8,
        layer_zooms={
            "contours": {"minzoom": 0, "maxzoom": 8},
            "data": {"minzoom": 7},
        },
        on_overflow="unsafe",  # dense data layer; overflow expected at high z
    )
    archive_bytes = buf.getvalue()
    assert len(archive_bytes) > 0, "Archive is empty"

    # --- Tiles that must have contours (verifying decode works) and no data ---
    # Tile (0, 0, 0) is the entire world at z0; contours span lon -160 to 160
    # so they must appear there.  Tiles at z1-z6 are checked at a central
    # coordinate (lon≈0, lat≈0 → tile (z, 2^(z-1), 2^(z-1))).
    def _count_layer(z: int, x: int, y: int, layer: str) -> int:
        decoded = read_pmtiles_bytes(archive_bytes, z=z, x=x, y=y)
        if decoded is None:
            return 0
        return len(decoded.get(layer, {}).get("features", []))

    # z0 — the entire world in a single tile
    assert _count_layer(0, 0, 0, "contours") > 0, "Contours must appear in z0/0/0"
    assert _count_layer(0, 0, 0, "data") == 0, "Data must be absent at z0"

    # z1-z6 — one central tile per zoom (covering lon/lat ≈ 0)
    _zoom_center = {
        1: (1, 1),
        2: (2, 2),
        3: (4, 4),
        4: (8, 8),
        5: (16, 16),
        6: (32, 32),
    }
    for z, (x, y) in _zoom_center.items():
        tile_data_count = _count_layer(z, x, y, "data")
        assert tile_data_count == 0, (
            f"Data must be absent at z{z}/{x}/{y} (found {tile_data_count} features)"
        )

    # --- Verify data layer is present at z7 and z8 ---
    # With 320k uniformly distributed points, tile (7, 64, 64) covers lon≈0, lat≈0.
    # Multiple tiles are tried to handle GDAL's tile-boundary effects.
    _z7_candidates = [(64, 64), (57, 91), (64, 78), (87, 80)]
    data_z7_total = sum(_count_layer(7, x, y, "data") for x, y in _z7_candidates)
    assert data_z7_total > 0, (
        f"Data layer must be present at z7 (checked tiles {_z7_candidates})"
    )

    _z8_candidates = [(128, 128), (61, 174), (241, 138), (120, 214)]
    data_z8_total = sum(_count_layer(8, x, y, "data") for x, y in _z8_candidates)
    assert data_z8_total > 0, (
        f"Data layer must be present at z8 (checked tiles {_z8_candidates})"
    )
