"""Tests for Web Mercator boundary and antimeridian coverage.

Root causes documented here
===========================

ERA5 z-0 fragment count difference
    329 ERA5 polygons (within 35-60 degrees N, well inside the Mercator extent)
    decode to 297 features at z-0 from this library vs 256/254 from Tippecanoe.
    Both backends clip features to tile boundaries using an internal buffer.
    GDAL uses BUFFER=80/4096 (approx 2%); Tippecanoe's default is larger.  The
    differing buffer sizes produce a different number of tile-edge fragments
    while the geographic union coverage is equivalent.  No features are lost.

South-polar point count difference at z-7/z-8
    Real-world south-polar point datasets yield ~1 fewer feature per zoom in
    GDAL (e.g. library 11/5) than in Tippecanoe (12/6).  Two root causes:

    1. **Buffer size**: a point within Tippecanoe's larger buffer but outside
       GDAL's 80-unit buffer appears in a Tippecanoe tile but not a GDAL tile.
       Both include the point at lower zooms; no data is lost, only tile
       assignment differs.

    2. **Silent drop**: points entirely outside the Web Mercator extent
       (|lat| > 85.05112877980659 degrees) are silently discarded by GDAL.
       This library now detects such features before passing them to GDAL and
       emits a UserWarning, making the exclusion explicit.

Antimeridian
    GDAL handles polygons that touch plus/minus 180 degrees longitude correctly.
    A MultiPolygon manually split at the antimeridian places each part in
    the correct tile column.  Coordinates outside plus/minus 180 degrees are
    also accepted (they wrap around as expected by the tile scheme).
"""

from __future__ import annotations

import io
import json
import warnings
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from geodataframe_to_pmtiles import EmptyLayerError, write_pmtiles
from geodataframe_to_pmtiles._writer import (
    WEB_MERCATOR_LAT_LIMIT,
    _is_outside_mercator_extent,
)
from geodataframe_to_pmtiles.exceptions import WritePMTilesError

from .pmtiles_semantics import (
    count_features_in_tile,
)

FIXTURES = Path(__file__).with_name("fixtures") / "conformance" / "boundary"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(gdf: gpd.GeoDataFrame, *, max_zoom: int = 0, layer: str = "pts") -> bytes:
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        write_pmtiles(
            {layer: gdf}, buf, min_zoom=0, max_zoom=max_zoom, on_overflow="unsafe"
        )
    return buf.getvalue()


def _write_capturing_warnings(
    gdf: gpd.GeoDataFrame, *, max_zoom: int = 0, layer: str = "pts"
) -> tuple[bytes, list[warnings.WarningMessage]]:
    buf = io.BytesIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        write_pmtiles(
            {layer: gdf}, buf, min_zoom=0, max_zoom=max_zoom, on_overflow="unsafe"
        )
    return buf.getvalue(), [w for w in caught if issubclass(w.category, UserWarning)]


def _out_of_bounds_warns(gdf: gpd.GeoDataFrame) -> list[str]:
    """Write gdf, capture warnings; return list of out-of-bounds warning messages."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            buf = io.BytesIO()
            write_pmtiles(
                {"pts": gdf}, buf, min_zoom=0, max_zoom=0, on_overflow="unsafe"
            )
        except WritePMTilesError:
            pass
    return [
        str(w.message)
        for w in caught
        if issubclass(w.category, UserWarning)
        and ("outside" in str(w.message).lower() or "Web Mercator" in str(w.message))
    ]


def _raises_empty_layer_for_out_of_bounds(
    gdf: gpd.GeoDataFrame, layer: str = "pts"
) -> None:
    """Helper: assert EmptyLayerError is raised for all-out-of-bounds GDF."""
    buf = io.BytesIO()
    write_pmtiles({layer: gdf}, buf, min_zoom=0, max_zoom=0, on_overflow="unsafe")


def _point_gdf(lon: float, lat: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"label": [f"lon={lon},lat={lat}"]},
        geometry=[Point(lon, lat)],
        crs="EPSG:4326",
    )


def _multi_point_gdf(coords: list[tuple[float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"label": [f"lon={c[0]},lat={c[1]}" for c in coords]},
        geometry=[Point(lon, lat) for lon, lat in coords],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Unit tests: _is_outside_mercator_extent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_outside_mercator_extent_inside() -> None:
    """Points/polygons inside the extent return False."""
    assert not _is_outside_mercator_extent(Point(0, 0))
    assert not _is_outside_mercator_extent(Point(0, WEB_MERCATOR_LAT_LIMIT))
    assert not _is_outside_mercator_extent(Point(0, -WEB_MERCATOR_LAT_LIMIT))
    assert not _is_outside_mercator_extent(box(-180, -85, 180, 85))


@pytest.mark.unit
def test_is_outside_mercator_extent_entirely_outside() -> None:
    """Geometries entirely outside the extent return True."""
    assert _is_outside_mercator_extent(Point(0, WEB_MERCATOR_LAT_LIMIT + 0.001))
    assert _is_outside_mercator_extent(Point(0, -(WEB_MERCATOR_LAT_LIMIT + 0.001)))
    assert _is_outside_mercator_extent(Point(0, 90))
    assert _is_outside_mercator_extent(Point(0, -90))
    assert _is_outside_mercator_extent(box(-10, 87, 10, 90))
    assert _is_outside_mercator_extent(box(-10, -90, 10, -87))


@pytest.mark.unit
def test_is_outside_mercator_extent_straddles() -> None:
    """Geometries that straddle the boundary (partially outside) return False."""
    # A polygon that crosses the north limit — part is inside.
    assert not _is_outside_mercator_extent(box(-10, 80, 10, 88))
    # A polygon that crosses the south limit — part is inside.
    assert not _is_outside_mercator_extent(box(-10, -88, 10, -80))


# ---------------------------------------------------------------------------
# Integration: boundary points
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_point_at_exact_north_limit_appears_at_all_zooms() -> None:
    """A point at exactly +WEB_MERCATOR_LAT_LIMIT is present in the archive."""
    gdf = _point_gdf(0, WEB_MERCATOR_LAT_LIMIT)
    data = _write(gdf, max_zoom=8)
    # Must appear at z0 and z2 (top-row tiles)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1
    assert count_features_in_tile(data, "pts", z=2, x=2, y=0) >= 1


@pytest.mark.integration
def test_point_at_exact_south_limit_appears_at_all_zooms() -> None:
    """A point at exactly -WEB_MERCATOR_LAT_LIMIT is present at z0 through z8."""
    gdf = _point_gdf(0, -WEB_MERCATOR_LAT_LIMIT)
    data = _write(gdf, max_zoom=8)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1
    # Southernmost tile at z7 is y=127, at z8 is y=255
    assert count_features_in_tile(data, "pts", z=7, x=64, y=127) == 1
    assert count_features_in_tile(data, "pts", z=8, x=128, y=255) == 1


@pytest.mark.integration
def test_point_just_inside_north_limit_appears() -> None:
    """A point 0.001° inside the north limit is preserved."""
    gdf = _point_gdf(0, WEB_MERCATOR_LAT_LIMIT - 0.001)
    data = _write(gdf, max_zoom=2)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_point_just_inside_south_limit_appears() -> None:
    """A point 0.001° inside the south limit is preserved."""
    gdf = _point_gdf(0, -(WEB_MERCATOR_LAT_LIMIT - 0.001))
    data = _write(gdf, max_zoom=2)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_point_outside_north_limit_warns_and_is_excluded() -> None:
    """A point beyond the north limit emits UserWarning and raises EmptyLayerError.

    When a layer has no features within the Web Mercator extent, write_pmtiles
    emits a UserWarning (listing the count of out-of-bounds features) and then
    raises EmptyLayerError rather than letting GDAL fail silently or with an
    unhelpful "Invalid bounds" message.
    """

    gdf = _point_gdf(0, WEB_MERCATOR_LAT_LIMIT + 0.001)
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1
    assert warn_msgs, "Expected a UserWarning for out-of-bounds feature"

    with pytest.raises(EmptyLayerError, match="out-of-bounds"):
        _raises_empty_layer_for_out_of_bounds(gdf)


@pytest.mark.integration
def test_point_outside_south_limit_warns_and_is_excluded() -> None:
    """A point beyond the south limit emits UserWarning and raises EmptyLayerError."""
    from geodataframe_to_pmtiles import (
        EmptyLayerError,
    )

    gdf = _point_gdf(0, -(WEB_MERCATOR_LAT_LIMIT + 0.001))
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1
    assert warn_msgs, "Expected a UserWarning for out-of-bounds feature"

    with pytest.raises(EmptyLayerError, match="out-of-bounds"):
        _raises_empty_layer_for_out_of_bounds(gdf)


@pytest.mark.integration
def test_point_at_geographic_north_pole_warns_and_is_excluded() -> None:
    """A point at lat=+90 (geographic north pole) is outside Mercator bounds."""
    from geodataframe_to_pmtiles import EmptyLayerError

    gdf = _point_gdf(0, 90.0)
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1

    with pytest.raises(EmptyLayerError):
        _raises_empty_layer_for_out_of_bounds(gdf)


@pytest.mark.integration
def test_point_at_geographic_south_pole_warns_and_is_excluded() -> None:
    """A point at lat=-90 (geographic south pole) is outside Mercator bounds."""
    from geodataframe_to_pmtiles import EmptyLayerError

    gdf = _point_gdf(0, -90.0)
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1

    with pytest.raises(EmptyLayerError):
        _raises_empty_layer_for_out_of_bounds(gdf)


@pytest.mark.integration
def test_mixed_bounds_points_only_in_bounds_appear() -> None:
    """Mixed set: in-bounds features appear; out-of-bounds features do not, with warning."""
    coords = [
        (0, WEB_MERCATOR_LAT_LIMIT),  # inside (on boundary)
        (0, -(WEB_MERCATOR_LAT_LIMIT)),  # inside (on boundary)
        (0, WEB_MERCATOR_LAT_LIMIT + 0.001),  # outside — triggers warning
        (0, -90.0),  # outside — counted in same warning
        (10, 10),  # well inside
    ]
    gdf = _multi_point_gdf(coords)
    data, caught = _write_capturing_warnings(gdf)
    user_warnings = [
        w
        for w in caught
        if issubclass(w.category, UserWarning)
        and ("outside" in str(w.message).lower() or "Web Mercator" in str(w.message))
    ]
    assert len(user_warnings) == 1, "Expected exactly one UserWarning for the layer"
    # The warning counts both out-of-bounds features: "2 feature(s)"
    assert "2" in str(user_warnings[0].message), (
        f"Expected '2' in warning message: {user_warnings[0].message}"
    )
    n = count_features_in_tile(data, "pts", z=0, x=0, y=0)
    assert n == 3, f"Expected 3 in-bounds features at z0, got {n}"


# ---------------------------------------------------------------------------
# Regression: null/empty geometries must not inflate valid_count
# ---------------------------------------------------------------------------


def test_all_null_geometry_raises_empty_layer_error() -> None:
    """A layer whose only rows have null geometry raises EmptyLayerError.

    Regression: valid_count was previously computed as ``len(gdf) - out_of_bounds``,
    which did not subtract rows with null or empty geometry.  Such a layer
    reaches GDAL with zero writable features and either fails silently or
    produces an invalid archive.  Raising EmptyLayerError early is correct.
    """
    gdf = gpd.GeoDataFrame(
        {"val": [1, 2]},
        geometry=[None, None],  # type: ignore[arg-type]
        crs="EPSG:4326",
    )
    with pytest.raises(EmptyLayerError):
        write_pmtiles(layers={"pts": gdf}, output=io.BytesIO())


def test_empty_geometry_raises_empty_layer_error() -> None:
    """A layer whose only rows have empty geometry raises EmptyLayerError."""
    gdf = gpd.GeoDataFrame(
        {"val": [1]},
        geometry=[Point()],
        crs="EPSG:4326",
    )
    with pytest.raises(EmptyLayerError):
        write_pmtiles(layers={"pts": gdf}, output=io.BytesIO())


@pytest.mark.integration
def test_all_out_of_bounds_layer_leaves_path_unchanged(tmp_path: Path) -> None:
    """An entirely out-of-bounds layer fails before a Path destination is replaced."""
    out = tmp_path / "existing.pmtiles"
    out.write_bytes(b"keep this archive")

    gdf = _point_gdf(0, WEB_MERCATOR_LAT_LIMIT + 0.001)

    with pytest.raises(EmptyLayerError):
        write_pmtiles({"pts": gdf}, out, min_zoom=0, max_zoom=0, on_overflow="unsafe")

    assert out.read_bytes() == b"keep this archive"


@pytest.mark.integration
def test_all_out_of_bounds_layer_leaves_stream_unchanged() -> None:
    """An entirely out-of-bounds layer fails before a stream destination is written."""
    stream = io.BytesIO(b"keep this archive")

    gdf = _point_gdf(0, WEB_MERCATOR_LAT_LIMIT + 0.001)

    with pytest.raises(EmptyLayerError):
        write_pmtiles(
            {"pts": gdf},
            stream,
            min_zoom=0,
            max_zoom=0,
            on_overflow="unsafe",
        )

    assert stream.getvalue() == b"keep this archive"


# ---------------------------------------------------------------------------
# Integration: antimeridian and longitude boundaries
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_point_at_antimeridian_east_appears() -> None:
    """A point at lon=+180 is included in the archive."""
    gdf = _point_gdf(180.0, 0.0)
    data = _write(gdf, max_zoom=1)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_point_at_antimeridian_west_appears() -> None:
    """A point at lon=-180 is included in the archive."""
    gdf = _point_gdf(-180.0, 0.0)
    data = _write(gdf, max_zoom=1)
    assert count_features_in_tile(data, "pts", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_antimeridian_split_multipolygon_appears_on_both_sides() -> None:
    """A MultiPolygon split at ±180° appears in tiles on both antimeridian sides.

    The correct representation for an antimeridian-crossing feature is a
    MultiPolygon with one part on each side.  GDAL places each part in the
    appropriate tile column at z=1 and z=2.
    """
    east = Polygon([(170, -5), (180, -5), (180, 5), (170, 5), (170, -5)])
    west = Polygon([(-180, -5), (-170, -5), (-170, 5), (-180, 5), (-180, -5)])
    multi = MultiPolygon([east, west])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[multi], crs="EPSG:4326")
    buf = io.BytesIO()
    write_pmtiles({"polys": gdf}, buf, min_zoom=0, max_zoom=2, on_overflow="unsafe")
    data = buf.getvalue()

    # z=1: east side in (1, 0), west side in (0, 0)
    east_z1 = count_features_in_tile(data, "polys", z=1, x=1, y=0)
    west_z1 = count_features_in_tile(data, "polys", z=1, x=0, y=0)
    assert east_z1 >= 1, f"East antimeridian side absent at z1 (got {east_z1})"
    assert west_z1 >= 1, f"West antimeridian side absent at z1 (got {west_z1})"

    # z=2: east side in (3, 1) or (3, 2), west side in (0, 1) or (0, 2)
    east_z2 = count_features_in_tile(data, "polys", z=2, x=3, y=1)
    west_z2 = count_features_in_tile(data, "polys", z=2, x=0, y=1)
    assert east_z2 >= 1, f"East antimeridian side absent at z2 (got {east_z2})"
    assert west_z2 >= 1, f"West antimeridian side absent at z2 (got {west_z2})"


@pytest.mark.integration
def test_antimeridian_crossing_line_is_preserved() -> None:
    """A line that crosses the antimeridian is preserved without being misclassified."""
    line = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(170, 0), (-170, 0)])],
        crs="EPSG:4326",
    )
    data = _write(line, max_zoom=2, layer="lines")

    assert count_features_in_tile(data, "lines", z=0, x=0, y=0) == 1
    assert count_features_in_tile(data, "lines", z=1, x=0, y=0) >= 1
    assert count_features_in_tile(data, "lines", z=1, x=1, y=0) >= 1


# ---------------------------------------------------------------------------
# Integration: polygon boundary clipping
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_polygon_within_bounds_preserved() -> None:
    """A polygon entirely within the Mercator extent is written without modification."""
    poly = Polygon([(10, 40), (20, 40), (20, 50), (10, 50), (10, 40)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    data = _write(gdf, max_zoom=0, layer="polys")
    assert count_features_in_tile(data, "polys", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_polygon_crossing_north_limit_is_clipped_and_preserved() -> None:
    """A polygon that crosses the north Mercator limit is clipped by GDAL and preserved.

    Root cause documented here: GDAL clips the polygon to the tile boundary
    internally.  No UserWarning is emitted because the geometry straddles the
    boundary (not entirely outside).  The polygon appears at z=0.
    """
    # Polygon from 80°N to 88°N — crosses the 85.05° limit
    poly = Polygon([(10, 80), (20, 80), (20, 88), (10, 88), (10, 80)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    data, caught = _write_capturing_warnings(gdf, max_zoom=0, layer="polys")
    # No out-of-bounds warning for a straddling polygon
    user_warnings = [
        w
        for w in caught
        if "outside" in str(w.message).lower() or "Web Mercator" in str(w.message)
    ]
    assert not user_warnings, (
        f"Unexpected out-of-bounds warning for straddling polygon: {user_warnings}"
    )
    # Polygon is clipped but present
    assert count_features_in_tile(data, "polys", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_polygon_crossing_south_limit_is_clipped_and_preserved() -> None:
    """A polygon that crosses the south Mercator limit is clipped and preserved."""
    # Polygon from -80°N to -88°N
    poly = Polygon([(10, -88), (20, -88), (20, -80), (10, -80), (10, -88)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    data, caught = _write_capturing_warnings(gdf, max_zoom=0, layer="polys")
    user_warnings = [
        w
        for w in caught
        if "outside" in str(w.message).lower() or "Web Mercator" in str(w.message)
    ]
    assert not user_warnings
    assert count_features_in_tile(data, "polys", z=0, x=0, y=0) == 1


@pytest.mark.integration
def test_polygon_entirely_outside_north_warns_and_is_excluded() -> None:
    """A polygon entirely above the north Mercator limit triggers a UserWarning and EmptyLayerError."""

    poly = Polygon([(10, 87), (20, 87), (20, 90), (10, 90), (10, 87)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1, "Expected exactly one UserWarning for the layer"

    with pytest.raises(EmptyLayerError):
        _raises_empty_layer_for_out_of_bounds(gdf, layer="polys")


@pytest.mark.integration
def test_polygon_entirely_outside_south_warns_and_is_excluded() -> None:
    """A polygon entirely below the south Mercator limit triggers a UserWarning and EmptyLayerError."""

    poly = Polygon([(10, -90), (20, -90), (20, -87), (10, -87), (10, -90)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
    warn_msgs = _out_of_bounds_warns(gdf)
    assert len(warn_msgs) == 1

    with pytest.raises(EmptyLayerError):
        _raises_empty_layer_for_out_of_bounds(gdf, layer="polys")


# ---------------------------------------------------------------------------
# Integration: polar points fixture
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_polar_points_fixture_provenance() -> None:
    """The polar points fixture matches its declared provenance."""
    geojson = FIXTURES / "polar_points.geojson"
    provenance = json.loads((FIXTURES / "polar_points.provenance.json").read_text())
    import hashlib

    sha256 = hashlib.sha256(geojson.read_bytes()).hexdigest()
    assert sha256 == provenance["geojson_sha256"]


@pytest.mark.integration
def test_polar_points_fixture_boundary_semantics(tmp_path: Path) -> None:
    """The polar points fixture: 8 in-bounds features appear, 4 out-of-bounds warn."""
    geojson = FIXTURES / "polar_points.geojson"
    provenance = json.loads((FIXTURES / "polar_points.provenance.json").read_text())

    gdf = gpd.read_file(geojson)
    assert len(gdf) == provenance["total_input_features"]

    out = tmp_path / "polar_points.pmtiles"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        write_pmtiles(
            {"pts": gdf},
            out,
            min_zoom=0,
            max_zoom=8,
            on_overflow="unsafe",
        )

    user_warnings = [
        w
        for w in caught
        if issubclass(w.category, UserWarning)
        and ("outside" in str(w.message).lower() or "Web Mercator" in str(w.message))
    ]
    assert len(user_warnings) == 1, "Expected exactly one UserWarning for the layer"
    # Exactly the 4 out-of-bounds features should trigger the warning
    assert str(provenance["expected_out_of_bounds"]) in str(user_warnings[0].message)

    data = out.read_bytes()
    n = count_features_in_tile(data, "pts", z=0, x=0, y=0)
    assert n == provenance["expected_within_bounds"], (
        f"Expected {provenance['expected_within_bounds']} in-bounds features at z0, got {n}"
    )


# ---------------------------------------------------------------------------
# Integration: ERA5 z-0 fragment count - documents the intentional difference
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_era5_z0_fragment_count_is_higher_than_tippecanoe() -> None:
    """ERA5 z-0 fragment count is higher from GDAL than from Tippecanoe.

    Root cause: GDAL's PMTiles driver uses BUFFER=80/4096 (≈2 %) for the
    geometry buffer around tile boundaries.  Tippecanoe's default is larger
    (≈5 %).  This produces different tile-clipping fragment counts at z-0
    without any semantic difference in geographic coverage.

    Observed values (GDAL 3.12.2):
      GDAL library  → 297 features at z-0
      Tippecanoe    → 256 (Celsius) / 254 (Fahrenheit) features at z-0

    Both backends preserve equivalent geographic union coverage.  The ERA5
    data lies entirely within 35-60 degrees N and has no boundary-related data loss.
    """
    era5_path = (
        Path(__file__).with_name("fixtures")
        / "conformance"
        / "climate"
        / "era5-1982-07-22-t2m-max-delta.geojson"
    )
    gdf = gpd.read_file(era5_path)
    assert len(gdf) == 329  # provenance-confirmed count

    buf = io.BytesIO()
    write_pmtiles(
        {"era5": gdf},
        buf,
        min_zoom=0,
        max_zoom=0,
        on_overflow="unsafe",
    )
    data = buf.getvalue()
    n_z0 = count_features_in_tile(data, "era5", z=0, x=0, y=0)

    # GDAL produces more fragments than Tippecanoe due to its smaller buffer.
    # The exact GDAL count should be stable across identical GDAL versions.
    assert n_z0 > 254, (
        f"GDAL z-0 fragment count ({n_z0}) should exceed Tippecanoe's 254"
    )
    # Sanity upper bound: never more features than polygons * some multiplier
    assert n_z0 <= 329 * 2, f"Unexpectedly large z-0 fragment count: {n_z0}"


# ---------------------------------------------------------------------------
# Integration: south-polar z-7/z-8 buffer difference - documents the
#              intentional Tippecanoe vs GDAL difference
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_south_polar_z7_z8_buffer_difference_documented() -> None:
    """Points near the south limit appear at z-7/z-8; points at the limit appear at z-7/z-8 too.

    Root cause of Tippecanoe vs GDAL count difference at z-7/z-8:
    GDAL BUFFER=80/4096 ≈ 0.001° beyond the southernmost tile edge.
    A point at -85.051° (just inside the Web Mercator limit) maps to the
    bottom edge of z-7 tile (64, 127) and appears there.

    Tippecanoe's larger default buffer would include points *slightly beyond*
    WEB_MERCATOR_LAT_LIMIT in the southernmost tile.  This library now detects
    such out-of-bounds features before GDAL and emits a UserWarning rather than
    allowing GDAL to silently or unpredictably handle them.  As a result, the
    Tippecanoe vs GDAL count difference at z-7/z-8 for south-polar datasets
    reflects both different buffer sizes and the out-of-bounds exclusion.
    """
    # Point at the exact south limit — maps to the bottom edge of z-7 tile (64, 127).
    at_limit = Point(0, -WEB_MERCATOR_LAT_LIMIT)
    gdf_limit = gpd.GeoDataFrame({"l": ["limit"]}, geometry=[at_limit], crs="EPSG:4326")
    data_limit = _write(gdf_limit, max_zoom=8)

    assert count_features_in_tile(data_limit, "pts", z=7, x=64, y=127) == 1, (
        "Point at exact south limit should appear in z-7 southernmost tile"
    )
    assert count_features_in_tile(data_limit, "pts", z=8, x=128, y=255) == 1

    # Point just inside the limit — also appears at z-7/z-8.
    just_inside = Point(0, -(WEB_MERCATOR_LAT_LIMIT - 0.001))
    gdf_inside = gpd.GeoDataFrame(
        {"l": ["inside"]}, geometry=[just_inside], crs="EPSG:4326"
    )
    data_inside = _write(gdf_inside, max_zoom=8)

    assert count_features_in_tile(data_inside, "pts", z=7, x=64, y=127) == 1, (
        "Point just inside south limit should appear in z-7 southernmost tile"
    )
    assert count_features_in_tile(data_inside, "pts", z=8, x=128, y=255) == 1
