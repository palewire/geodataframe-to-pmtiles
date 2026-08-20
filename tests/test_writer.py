"""Tests for geodataframe_to_pmtiles.write."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from geodataframe_to_pmtiles import (
    CRSTransformError,
    EmptyLayerError,
    MissingCRSError,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
    write,
)

from .pmtiles_semantics import read_pmtiles_archive

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _points_gdf(**extra_cols: object) -> gpd.GeoDataFrame:
    """Return a tiny 3-feature point GeoDataFrame in EPSG:4326."""
    data: dict[str, object] = {
        "name": ["alpha", "beta", "gamma"],
        "value": [1.0, 2.0, 3.0],
    }
    data.update(extra_cols)
    return gpd.GeoDataFrame(
        data,
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )


def _lines_gdf() -> gpd.GeoDataFrame:
    """Return a 2-feature line GeoDataFrame in EPSG:4326."""
    return gpd.GeoDataFrame(
        {"id": [1, 2], "label": ["river", "road"]},
        geometry=[
            LineString([(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]),
            LineString([(10.0, 10.0), (11.0, 11.0)]),
        ],
        crs="EPSG:4326",
    )


def _open_pmtiles_bytes(data: bytes) -> tuple[gdal.Dataset, str]:  # noqa: F821
    """Write *data* to a vsimem path and return (datasource, path)."""
    from osgeo import gdal

    gdal.UseExceptions()
    path = f"/vsimem/test_open_{id(data)}.pmtiles"
    vf = gdal.VSIFOpenL(path, "wb")
    gdal.VSIFWriteL(data, 1, len(data), vf)
    gdal.VSIFCloseL(vf)
    ds = gdal.OpenEx(path, gdal.OF_VECTOR)
    return ds, path


def _write_safe(layers, output, **kwargs):
    """Write an archive through the default safe overflow policy."""
    write(layers, output, **kwargs)


def _write_ignore(layers, output, **kwargs):
    """Write an archive suppressing tile-overflow errors (unsafe policy).

    Used in semantic tests where the small feature count may cause GDAL to
    report overflow at coarse zoom levels; the test focus is output semantics,
    not overflow behaviour.
    """
    write(layers, output, on_overflow="unsafe", **kwargs)


@pytest.fixture(autouse=True)
def _skip_integration_without_gdal(request: pytest.FixtureRequest) -> None:
    """Skip integration tests when the native GDAL runtime is unavailable."""
    if (
        request.node.get_closest_marker("integration")
        and importlib.util.find_spec("osgeo") is None
    ):
        pytest.skip("GDAL Python bindings are not installed")


# ---------------------------------------------------------------------------
# Two-layer archive: core round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_two_layer_archive_path(tmp_path: Path) -> None:
    """write writes a two-layer archive to a Path and can be reopened."""
    from osgeo import gdal

    out = tmp_path / "two_layers.pmtiles"
    _write_safe(
        {"points": _points_gdf(), "lines": _lines_gdf()},
        out,
        min_zoom=0,
        max_zoom=4,
        name="test",
        description="two-layer test archive",
    )

    assert out.exists()
    assert out.stat().st_size > 0

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None

    layer_names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    assert "points" in layer_names
    assert "lines" in layer_names

    meta = ds.GetMetadata()
    assert meta.get("minzoom") == "0"
    assert meta.get("maxzoom") == "4"
    assert meta.get("name") == "test"

    ds = None


@pytest.mark.integration
def test_two_layer_archive_bytesio() -> None:
    """write writes a two-layer archive to a BytesIO stream."""
    buf = io.BytesIO()
    _write_safe(
        {"points": _points_gdf(), "lines": _lines_gdf()},
        buf,
        min_zoom=0,
        max_zoom=4,
    )
    buf.seek(0)
    data = buf.read()
    assert len(data) > 0

    ds, path = _open_pmtiles_bytes(data)
    try:
        assert ds is not None
        layer_names = {
            ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())
        }
        assert "points" in layer_names
        assert "lines" in layer_names
    finally:
        ds = None
        from osgeo import gdal

        gdal.Unlink(path)


@pytest.mark.integration
def test_layer_names_are_exact_source_names(tmp_path: Path) -> None:
    """Layer names in the archive match the keys from the layers mapping exactly."""
    from osgeo import gdal

    out = tmp_path / "named.pmtiles"
    _write_safe({"climate_zones": _points_gdf(), "admin_boundaries": _lines_gdf()}, out)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    assert "climate_zones" in names
    assert "admin_boundaries" in names
    ds = None


# ---------------------------------------------------------------------------
# Feature order determinism
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_feature_order_preserved(tmp_path: Path) -> None:
    """Features are written in input order; first-occurrence order matches insertion order."""
    from osgeo import gdal

    names = [f"feat_{i}" for i in range(5)]
    gdf = gpd.GeoDataFrame(
        {"name": names, "idx": list(range(5))},
        geometry=[Point(float(i), float(i)) for i in range(5)],
        crs="EPSG:4326",
    )
    out = tmp_path / "order.pmtiles"
    _write_safe({"ordered": gdf}, out, min_zoom=0, max_zoom=4)

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    seen = [feat.GetField("name") for feat in lyr]
    # MVT duplicates features across tiles; unique first-occurrence order should
    # match insertion order.
    unique_seen = list(dict.fromkeys(seen))
    assert unique_seen == names
    ds = None


# ---------------------------------------------------------------------------
# Property normalisation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_string_properties(tmp_path: Path) -> None:
    """String columns round-trip as strings."""
    from osgeo import gdal

    gdf = _points_gdf()
    out = tmp_path / "strings.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    names = {feat.GetField("name") for feat in lyr}
    assert {"alpha", "beta", "gamma"}.issubset(names)
    ds = None


@pytest.mark.integration
def test_integer_properties(tmp_path: Path) -> None:
    """Integer columns round-trip as integer values.

    Note: The PMTiles/MVT format stores all integer types in protocol-buffer
    varint fields.  GDAL's PMTiles reader reports them as OFTInteger (not
    OFTInteger64) regardless of the input type.
    """
    from osgeo import gdal, ogr

    gdf = gpd.GeoDataFrame(
        {"count": [10, 20, 30]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "int.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("count"))
    # PMTiles reader may return OFTInteger or OFTInteger64.
    assert fld.GetType() in (ogr.OFTInteger, ogr.OFTInteger64)
    values = {feat.GetField("count") for feat in lyr}
    assert {10, 20, 30}.issubset(values)
    ds = None


@pytest.mark.integration
def test_float_properties(tmp_path: Path) -> None:
    """Float columns are stored as Real fields."""
    from osgeo import gdal, ogr

    gdf = gpd.GeoDataFrame(
        {"ratio": [0.1, 0.5, 0.9]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "float.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("ratio"))
    assert fld.GetType() == ogr.OFTReal
    ds = None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        pytest.param([True, False, True], [True, False, True], id="python-bool"),
        pytest.param(
            np.array([True, False, True], dtype=np.bool_),
            [True, False, True],
            id="numpy-bool",
        ),
        pytest.param(
            pd.array([True, False, pd.NA], dtype="boolean"),
            [True, False, None],
            id="pandas-nullable-boolean",
        ),
        pytest.param(
            pd.Series([True, None, False], dtype=object),
            [True, None, False],
            id="object-bool-null",
        ),
        pytest.param(
            pd.array([pd.NA, pd.NA, pd.NA], dtype="boolean"),
            [None, None, None],
            id="all-null-pandas-nullable-boolean",
        ),
        pytest.param([True, True, True], [True, True, True], id="all-true"),
        pytest.param([False, False, False], [False, False, False], id="all-false"),
    ],
)
def test_boolean_properties_decode_as_native_mvt_booleans(
    tmp_path: Path,
    values: object,
    expected: list[bool | None],
) -> None:
    """Official PMTiles and MVT decoders return Python booleans, not integers."""
    from osgeo import gdal, ogr

    out = tmp_path / "bool.pmtiles"
    _write_safe({"lyr": _points_gdf(flag=values)}, out, min_zoom=0, max_zoom=0)

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("flag"))
    assert fld.GetSubType() == ogr.OFSTBoolean
    ds = None

    _, _, layers = read_pmtiles_archive(out)
    decoded = [
        feature["properties"].get("flag") for feature in layers["lyr"]["features"]
    ]

    assert decoded == expected
    assert all(type(value) is bool for value in decoded if value is not None)


@pytest.mark.integration
def test_numeric_zero_one_properties_remain_integers(tmp_path: Path) -> None:
    """Numeric binary columns do not acquire the Boolean field subtype."""
    from osgeo import gdal, ogr

    out = tmp_path / "numeric-binary.pmtiles"
    _write_safe({"lyr": _points_gdf(binary=[0, 1, 0])}, out, min_zoom=0, max_zoom=0)

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("binary"))
    assert fld.GetSubType() == ogr.OFSTNone
    ds = None

    _, _, layers = read_pmtiles_archive(out)
    decoded = [
        feature["properties"].get("binary") for feature in layers["lyr"]["features"]
    ]

    assert decoded == [0, 1, 0]
    assert all(type(value) is int for value in decoded)


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        [True, 1, False],
        [1, True, False],
        [np.bool_(True), np.int64(0), np.bool_(False)],
        [True, "yes", False],
        ["yes", True, False],
        [True, 1, "yes"],
        ["yes", 1, True],
    ],
)
def test_mixed_boolean_properties_raise(values: list[object]) -> None:
    """Mixed Boolean values cannot silently acquire either meaning."""
    with pytest.raises(UnsupportedPropertyTypeError, match="mixes boolean"):
        write(
            {"lyr": _points_gdf(flag=values)},
            io.BytesIO(),
            on_overflow="unsafe",
        )


@pytest.mark.integration
def test_nullable_float_nan_is_null(tmp_path: Path) -> None:
    """float NaN values are stored as null fields (returned as None when read back)."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"val": [1.0, float("nan"), 3.0]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "nan.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    # PMTiles returns null fields as None (IsFieldNull may not be set).
    null_count = sum(1 for feat in lyr if feat.GetField("val") is None)
    assert null_count >= 1
    ds = None


@pytest.mark.integration
def test_none_stored_as_null(tmp_path: Path) -> None:
    """None values are stored as null fields (returned as None when read back)."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"label": ["a", None, "c"]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "none.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    null_count = sum(1 for feat in lyr if feat.GetField("label") is None)
    assert null_count >= 1
    ds = None


@pytest.mark.integration
def test_pd_na_treated_as_null(tmp_path: Path) -> None:
    """pandas NA values are treated as null fields."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"label": pd.array(["a", pd.NA, "c"], dtype="string")},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "pdna.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    null_count = sum(1 for feat in lyr if feat.GetField("label") is None)
    assert null_count >= 1
    ds = None


# ---------------------------------------------------------------------------
# json_fields — explicit list/dict encoding
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_property_json_encoded_auto(tmp_path: Path) -> None:
    """List-valued columns are JSON-encoded to strings when json_fields=None (auto)."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"tags": [["a", "b"], ["c"], ["d", "e", "f"]]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "list_auto.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    raw_values = [
        feat.GetField("tags") for feat in lyr if feat.GetField("tags") is not None
    ]
    assert len(raw_values) > 0
    for v in raw_values:
        decoded = json.loads(v)
        assert isinstance(decoded, list)
    ds = None


@pytest.mark.integration
def test_dict_property_json_encoded_auto(tmp_path: Path) -> None:
    """Dict-valued columns are JSON-encoded to strings when json_fields=None (auto)."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"meta": [{"k": "v"}, {"x": 1}, {"nested": True}]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "dict_auto.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    raw_values = [
        feat.GetField("meta") for feat in lyr if feat.GetField("meta") is not None
    ]
    assert len(raw_values) > 0
    for v in raw_values:
        decoded = json.loads(v)
        assert isinstance(decoded, dict)
    ds = None


@pytest.mark.integration
def test_list_property_json_encoded_explicit(tmp_path: Path) -> None:
    """A list column listed in json_fields is JSON-encoded; other columns work normally."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"tags": [["a", "b"], ["c"], ["d"]], "name": ["x", "y", "z"]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "list_explicit.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4, json_fields=["tags"])
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    raw_values = [
        feat.GetField("tags") for feat in lyr if feat.GetField("tags") is not None
    ]
    assert len(raw_values) > 0
    for v in raw_values:
        assert isinstance(json.loads(v), list)
    ds = None


@pytest.mark.integration
def test_mixed_json_column_is_stringified(tmp_path: Path) -> None:
    """A mixed scalar/list column is stringified instead of being coerced to zero."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"idx": [0, 1, 2], "mixed": [1, [2, 3], 4]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "mixed.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4, json_fields=["mixed"])
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    seen: dict[int, str] = {}
    for feat in lyr:
        idx = feat.GetField("idx")
        if idx not in seen:
            seen[idx] = feat.GetField("mixed")
    assert seen == {0: "1", 1: "[2, 3]", 2: "4"}
    ds = None


@pytest.mark.unit
def test_list_column_not_in_json_fields_raises() -> None:
    """A list column NOT in json_fields raises UnsupportedPropertyTypeError."""
    gdf = gpd.GeoDataFrame(
        {"tags": [["a", "b"], ["c"], ["d"]]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    with pytest.raises(UnsupportedPropertyTypeError, match="json_fields"):
        write({"lyr": gdf}, io.BytesIO(), json_fields=[])


@pytest.mark.unit
def test_dict_column_not_in_json_fields_raises() -> None:
    """A dict column NOT in json_fields raises UnsupportedPropertyTypeError."""
    gdf = gpd.GeoDataFrame(
        {"props": [{"k": "v"}, {}, {"x": 1}]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    with pytest.raises(UnsupportedPropertyTypeError, match="json_fields"):
        write({"lyr": gdf}, io.BytesIO(), json_fields=[])


# ---------------------------------------------------------------------------
# Overflow policy
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_feature_overflow_raises_before_path_is_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A feature cap diagnostic rejects the archive and preserves the destination."""
    from geodataframe_to_pmtiles import _writer as writer_module

    monkeypatch.setattr(writer_module, "_MAX_FEATURES", 2)
    clustered = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(0.0, 0.0), Point(0.0, 0.0), Point(0.0, 0.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "existing.pmtiles"
    out.write_bytes(b"keep this archive")

    with pytest.raises(TileOverflowError) as caught:
        write({"clustered": clustered}, out, min_zoom=0, max_zoom=0)

    violations = caught.value.violations
    assert len(violations) == 1
    violation = violations[0]
    assert violation.limit == "MAX_FEATURES"
    assert violation.requested == writer_module._MAX_FEATURES
    assert violation.observed == writer_module._MAX_FEATURES
    assert violation.tile == (0, 0, 0)
    assert out.read_bytes() == b"keep this archive"


@pytest.mark.integration
def test_size_overflow_raises_before_stream_is_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A size-driven geometry recode rejects the archive and preserves a stream."""
    from geodataframe_to_pmtiles import _writer as writer_module

    monkeypatch.setattr(writer_module, "_MAX_SIZE", 100)
    coordinates = [
        (float(index % 360) - 180.0, float((index * 17) % 170) - 85.0)
        for index in range(4_000)
    ]
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString(coordinates)],
        crs="EPSG:4326",
    )
    stream = io.BytesIO(b"keep this archive")
    stream.seek(0)

    with pytest.raises(TileOverflowError) as caught:
        write({"dense": gdf}, stream, min_zoom=0, max_zoom=0)

    violations = caught.value.violations
    assert violations
    assert {violation.requested for violation in violations} == {
        writer_module._MAX_SIZE
    }
    assert all(violation.observed >= violation.requested for violation in violations)
    violation = violations[0]
    assert violation.limit == "MAX_SIZE"
    assert violation.requested == writer_module._MAX_SIZE
    assert violation.observed > violation.requested
    assert violation.tile == (0, 0, 0)
    assert stream.getvalue() == b"keep this archive"


@pytest.mark.integration
def test_unsafe_overflow_opt_out_warns_and_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit unsafe mode is the only mode that permits an overflow."""
    from geodataframe_to_pmtiles import _writer as writer_module

    monkeypatch.setattr(writer_module, "_MAX_FEATURES", 2)
    buffer = io.BytesIO()
    with pytest.warns(UserWarning, match="unsafe"):
        write(
            {"lyr": _points_gdf()},
            buffer,
            min_zoom=0,
            max_zoom=0,
            on_overflow="unsafe",
        )
    assert buffer.getvalue()


@pytest.mark.unit
def test_on_overflow_invalid_value_raises() -> None:
    """An invalid on_overflow value raises ValueError."""
    with pytest.raises(ValueError, match="on_overflow"):
        write(
            {"lyr": _points_gdf()},
            io.BytesIO(),
            on_overflow="drop",  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_missing_gdal_runtime_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write raises a runtime error if GDAL cannot be imported."""
    from geodataframe_to_pmtiles import _writer as writer_module

    def _missing_osgeo(name: str, package: str | None = None) -> object:
        if name.startswith("osgeo"):
            raise ImportError("No module named 'osgeo'")
        return importlib.import_module(name, package)

    monkeypatch.setattr(writer_module, "import_module", _missing_osgeo)

    with pytest.raises(RuntimeError, match="GDAL Python bindings are required"):
        write({"lyr": _points_gdf()}, io.BytesIO(), on_overflow="unsafe")


# ---------------------------------------------------------------------------
# POC caps (fixed spike-validated values)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_poc_caps_allow_normal_dataset(tmp_path: Path) -> None:
    """The fixed POC caps (MAX_FEATURES=300,000, MAX_SIZE=10 MB) allow normal datasets."""
    out = tmp_path / "poc.pmtiles"
    large_gdf = gpd.GeoDataFrame(
        {"id": list(range(100))},
        geometry=[Point(float(i % 10), float(i // 10)) for i in range(100)],
        crs="EPSG:4326",
    )
    _write_safe({"lyr": large_gdf}, out)
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.integration
def test_poc_caps_preserve_200k_z0_features(tmp_path: Path) -> None:
    """Spike-validated: MAX_FEATURES=300,000 preserved 200,001 z0 features (~630 KB).

    This is the exact scenario from the spike test.  All features are spread
    across the globe so a single z0 tile holds all of them; the resulting
    archive must be non-empty and within the expected compressed-byte range.
    """
    n = 200_001
    # Spread features evenly across -180..179 lon / -85..84 lat bands.
    gdf = gpd.GeoDataFrame(
        {"id": range(n)},
        geometry=[
            Point(float(i % 360) - 180.0, float((i // 360) % 170) - 85.0)
            for i in range(n)
        ],
        crs="EPSG:4326",
    )
    out = tmp_path / "large_z0.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out)
    assert len(layers["lyr"]["features"]) == n
    assert out.exists()
    size = out.stat().st_size
    # Spike result: 630,430 bytes.  Allow ±50 % for driver/compression variance.
    assert 300_000 < size < 1_500_000, (
        f"Archive size {size:,} bytes is outside the expected spike range "
        "(300 K – 1.5 MB). Caps may have changed."
    )


@pytest.mark.integration
def test_default_feature_overflow_rejects_300001_z0_features(
    tmp_path: Path,
) -> None:
    """The default 300,000 feature cap rejects the exact 300,001-feature stress case."""
    from geodataframe_to_pmtiles import _writer as writer_module

    n = 300_001
    gdf = gpd.GeoDataFrame(
        {"id": range(n)},
        geometry=[
            Point(float(i % 360) - 180.0, float((i // 360) % 170) - 85.0)
            for i in range(n)
        ],
        crs="EPSG:4326",
    )
    out = tmp_path / "overflow_300001.pmtiles"

    with pytest.raises(TileOverflowError) as caught:
        write({"lyr": gdf}, out, min_zoom=0, max_zoom=0)

    violation = caught.value.violations[0]
    assert violation.limit == "MAX_FEATURES"
    assert violation.requested == writer_module._MAX_FEATURES
    assert violation.observed == writer_module._MAX_FEATURES
    assert violation.tile == (0, 0, 0)
    assert not out.exists()


# ---------------------------------------------------------------------------
# Zoom metadata
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_zoom_metadata_stored(tmp_path: Path) -> None:
    """min_zoom and max_zoom are reflected in the archive metadata."""
    from osgeo import gdal

    out = tmp_path / "zoom.pmtiles"
    _write_safe({"lyr": _points_gdf()}, out, min_zoom=2, max_zoom=7)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    meta = ds.GetMetadata()
    assert meta.get("minzoom") == "2"
    assert meta.get("maxzoom") == "7"
    ds = None


@pytest.mark.integration
def test_default_zoom_range(tmp_path: Path) -> None:
    """Default zoom range is 0-8 (archive-wide defaults)."""
    from osgeo import gdal

    out = tmp_path / "default_zoom.pmtiles"
    _write_safe({"lyr": _points_gdf()}, out)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    meta = ds.GetMetadata()
    assert meta.get("minzoom") == "0"
    assert meta.get("maxzoom") == "8"
    ds = None


# ---------------------------------------------------------------------------
# Metadata options (name, description)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_name_and_description_stored(tmp_path: Path) -> None:
    """name and description arguments are stored in the archive metadata."""
    from osgeo import gdal

    out = tmp_path / "meta.pmtiles"
    _write_safe(
        {"lyr": _points_gdf()},
        out,
        name="my map",
        description="a test map",
    )
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    meta = ds.GetMetadata()
    assert meta.get("name") == "my map"
    assert meta.get("description") == "a test map"
    ds = None


@pytest.mark.unit
def test_attribution_invalid_type_raises() -> None:
    """write raises TypeError when attribution is not a str."""
    with pytest.raises(TypeError, match="attribution"):
        write(  # type: ignore[call-overload]
            {"lyr": _points_gdf()},
            io.BytesIO(),
            attribution=123,  # type: ignore[arg-type]
            on_overflow="unsafe",
        )


@pytest.mark.unit
def test_inject_attribution_unit() -> None:
    """_inject_attribution patches metadata without touching tile payloads (no GDAL needed)."""
    from pmtiles.reader import MemorySource, Reader, all_tiles
    from pmtiles.tile import zxy_to_tileid

    from geodataframe_to_pmtiles._writer import _inject_attribution

    metadata = {
        "name": "unit-test",
        "description": "unit-test-desc",
        "vector_layers": [
            {"id": "lyr", "description": "layer", "fields": {"name": "String"}}
        ],
        "unknown": {"nested": ["alpha", 1]},
    }
    original = _build_pmtiles_archive(
        [(zxy_to_tileid(0, 0, 0), b"fake-mvt-tile-bytes-do-not-change")],
        metadata,
    )
    original_reader = Reader(MemorySource(original))
    original_header = original_reader.header()
    original_meta = original_reader.metadata()

    # Confirm attribution is absent before injection.
    assert "attribution" not in original_meta

    # Inject attribution.
    patched = _inject_attribution(original, "© Unit Test Ünïcödé")
    patched_reader = Reader(MemorySource(patched))
    patched_header = patched_reader.header()

    # Attribution key must be present with the exact value.
    meta = patched_reader.metadata()
    assert meta == {**original_meta, "attribution": "© Unit Test Ünïcödé"}

    delta = patched_header["metadata_length"] - original_header["metadata_length"]
    for key, value in original_header.items():
        if key in {"metadata_length", "leaf_directory_offset", "tile_data_offset"}:
            continue
        assert patched_header[key] == value
    assert patched_header["leaf_directory_offset"] == (
        original_header["leaf_directory_offset"] + delta
    )
    assert (
        patched_header["tile_data_offset"]
        == original_header["tile_data_offset"] + delta
    )

    assert _pmtiles_section(
        original, original_header, "root_offset", "root_length"
    ) == _pmtiles_section(patched, patched_header, "root_offset", "root_length")
    assert _pmtiles_section(
        original, original_header, "metadata_offset", "metadata_length"
    ) != _pmtiles_section(patched, patched_header, "metadata_offset", "metadata_length")
    assert _pmtiles_section(
        original, original_header, "leaf_directory_offset", "leaf_directory_length"
    ) == _pmtiles_section(
        patched, patched_header, "leaf_directory_offset", "leaf_directory_length"
    )
    assert _pmtiles_section(
        original, original_header, "tile_data_offset", "tile_data_length"
    ) == _pmtiles_section(
        patched, patched_header, "tile_data_offset", "tile_data_length"
    )

    assert list(all_tiles(MemorySource(original))) == list(
        all_tiles(MemorySource(patched))
    )
    assert original != patched


# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------


def _read_pmtiles_metadata(data: bytes) -> dict:
    """Parse the TileJSON metadata from a PMTiles archive in memory."""
    from pmtiles.reader import MemorySource, Reader

    return Reader(MemorySource(data)).metadata()


def _build_pmtiles_archive(
    tiles: list[tuple[int, bytes]],
    metadata: dict[str, object],
) -> bytes:
    """Build a small PMTiles archive with the official writer."""
    from pmtiles.tile import Compression, TileType
    from pmtiles.writer import Writer

    buf = io.BytesIO()
    writer = Writer(buf)
    for tile_id, tile_bytes in tiles:
        writer.write_tile(tile_id, tile_bytes)
    writer.finalize(
        {
            "tile_type": TileType.MVT,
            "tile_compression": Compression.GZIP,
        },
        metadata,
    )
    return buf.getvalue()


def _rewrite_metadata_as_raw_json(data: bytes) -> bytes:
    """Return *data* with raw JSON metadata and NONE internal compression."""
    from pmtiles.reader import MemorySource, Reader
    from pmtiles.tile import Compression, serialize_header

    reader = Reader(MemorySource(data))
    header = reader.header()
    metadata = reader.metadata()
    raw_metadata = json.dumps(metadata, ensure_ascii=False).encode("utf-8")

    new_header = dict(header)
    new_header["internal_compression"] = Compression.NONE
    new_header["metadata_length"] = len(raw_metadata)
    delta = len(raw_metadata) - header["metadata_length"]
    if delta:
        new_header["leaf_directory_offset"] = header["leaf_directory_offset"] + delta
        new_header["tile_data_offset"] = header["tile_data_offset"] + delta

    out = io.BytesIO()
    out.write(serialize_header(new_header))
    out.write(_pmtiles_section(data, header, "root_offset", "root_length"))
    out.write(raw_metadata)
    if header["leaf_directory_length"] > 0:
        out.write(
            _pmtiles_section(
                data, header, "leaf_directory_offset", "leaf_directory_length"
            )
        )
    out.write(_pmtiles_section(data, header, "tile_data_offset", "tile_data_length"))
    return out.getvalue()


def _pmtiles_section(
    data: bytes,
    header: dict[str, object],
    offset_key: str,
    length_key: str,
) -> bytes:
    """Return a raw PMTiles section from *data* using header offsets."""
    start = int(header[offset_key])
    stop = start + int(header[length_key])
    return data[start:stop]


def _all_tile_hashes(data: bytes) -> dict[tuple[int, int, int], str]:
    """Return a mapping of (z, x, y) → SHA-256 hex for every tile in the archive."""
    from pmtiles.reader import MemorySource, all_tiles

    return {
        zxy: hashlib.sha256(tile_bytes).hexdigest()
        for zxy, tile_bytes in all_tiles(MemorySource(data))
    }


# ---------------------------------------------------------------------------
# Attribution tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_attribution_stored_in_metadata_path(tmp_path: Path) -> None:
    """write stores attribution in metadata when output is a Path."""
    out = tmp_path / "attr.pmtiles"
    _write_safe({"pts": _points_gdf()}, out, attribution="Reuters, ECMWF")

    meta = _read_pmtiles_metadata(out.read_bytes())
    assert meta.get("attribution") == "Reuters, ECMWF"


@pytest.mark.integration
def test_attribution_stored_in_metadata_bytesio() -> None:
    """write stores attribution in metadata when output is a BytesIO."""
    buf = io.BytesIO()
    _write_safe({"pts": _points_gdf()}, buf, attribution="Reuters, ECMWF")
    buf.seek(0)

    meta = _read_pmtiles_metadata(buf.read())
    assert meta.get("attribution") == "Reuters, ECMWF"


@pytest.mark.integration
def test_attribution_unicode_preserved() -> None:
    """Unicode characters in attribution are preserved exactly."""
    unicode_attribution = "© Données — Réseau Géographique Japonais 日本語"
    buf = io.BytesIO()
    _write_safe({"pts": _points_gdf()}, buf, attribution=unicode_attribution)
    buf.seek(0)

    meta = _read_pmtiles_metadata(buf.read())
    assert meta.get("attribution") == unicode_attribution


@pytest.mark.integration
def test_attribution_html_preserved() -> None:
    """HTML markup in attribution is preserved exactly."""
    html_attribution = '<a href="https://example.com">© Example &amp; Co.</a>'
    buf = io.BytesIO()
    _write_safe({"pts": _points_gdf()}, buf, attribution=html_attribution)
    buf.seek(0)

    meta = _read_pmtiles_metadata(buf.read())
    assert meta.get("attribution") == html_attribution


@pytest.mark.integration
def test_attribution_omitted_key_absent() -> None:
    """When attribution is not supplied the key is absent from the metadata."""
    buf = io.BytesIO()
    _write_safe({"pts": _points_gdf()}, buf)
    buf.seek(0)

    meta = _read_pmtiles_metadata(buf.read())
    assert "attribution" not in meta


@pytest.mark.integration
def test_attribution_empty_string_key_absent() -> None:
    """When attribution is an empty string the key is absent from the metadata."""
    buf = io.BytesIO()
    _write_safe({"pts": _points_gdf()}, buf, attribution="")
    buf.seek(0)

    meta = _read_pmtiles_metadata(buf.read())
    assert "attribution" not in meta


@pytest.mark.integration
def test_attribution_tile_hashes_unchanged() -> None:
    """Every MVT tile payload is byte-identical before and after attribution injection."""
    # Write without attribution first to get baseline tile hashes.
    buf_base = io.BytesIO()
    _write_safe({"pts": _points_gdf(), "lines": _lines_gdf()}, buf_base)
    buf_base.seek(0)
    base_bytes = buf_base.read()

    # Write with attribution.
    buf_attr = io.BytesIO()
    _write_safe(
        {"pts": _points_gdf(), "lines": _lines_gdf()},
        buf_attr,
        attribution="© Test attribution",
    )
    buf_attr.seek(0)
    attr_bytes = buf_attr.read()

    # Tile hashes must be identical; only the metadata section changes.
    base_hashes = _all_tile_hashes(base_bytes)
    attr_hashes = _all_tile_hashes(attr_bytes)

    assert base_hashes == attr_hashes, (
        "Some tile payloads changed after attribution injection; "
        f"differing tiles: {set(base_hashes) ^ set(attr_hashes)}"
    )
    # Sanity: the archives must actually differ (the metadata section changed).
    assert base_bytes != attr_bytes


@pytest.mark.integration
def test_attribution_multilayer_metadata_retained() -> None:
    """Attribution injection preserves name, description, and vector_layers for a multilayer archive."""
    buf = io.BytesIO()
    _write_safe(
        {"points": _points_gdf(), "lines": _lines_gdf()},
        buf,
        min_zoom=0,
        max_zoom=2,
        attribution="© multilayer test",
    )
    buf.seek(0)
    meta = _read_pmtiles_metadata(buf.read())

    assert meta.get("attribution") == "© multilayer test"
    layer_ids = {lyr["id"] for lyr in meta.get("vector_layers", [])}
    assert "points" in layer_ids
    assert "lines" in layer_ids


@pytest.mark.integration
def test_attribution_era5_fixture(tmp_path: Path) -> None:
    """Attribution injection works on a real-world ERA5 fixture archive."""
    fixture = (
        Path(__file__).with_name("fixtures")
        / "conformance"
        / "climate"
        / "era5-1982-07-22-t2m-max-delta.geojson"
    )
    gdf = gpd.read_file(fixture).to_crs("EPSG:4326")

    out = tmp_path / "era5.pmtiles"
    write(
        {"era5": gdf},
        out,
        min_zoom=0,
        max_zoom=0,
        attribution="Reuters, ECMWF",
        on_overflow="unsafe",
    )

    meta = _read_pmtiles_metadata(out.read_bytes())
    assert meta.get("attribution") == "Reuters, ECMWF"
    # Tiles must still be present.
    from pmtiles.reader import MemorySource, all_tiles

    tiles = list(all_tiles(MemorySource(out.read_bytes())))
    assert len(tiles) > 0


@pytest.mark.integration
def test_attribution_other_metadata_fields_retained(tmp_path: Path) -> None:
    """name, description, min/max zoom, and vector_layers are intact after attribution injection."""
    out = tmp_path / "meta.pmtiles"
    _write_safe(
        {"pts": _points_gdf()},
        out,
        name="metadata test",
        description="metadata desc",
        min_zoom=0,
        max_zoom=4,
        attribution="Test provider",
    )
    # Verify via pmtiles.reader.Reader (the official API).
    meta = _read_pmtiles_metadata(out.read_bytes())
    assert meta.get("attribution") == "Test provider"
    assert meta.get("name") == "metadata test"
    assert meta.get("description") == "metadata desc"
    assert meta.get("minzoom") == "0"
    assert meta.get("maxzoom") == "4"
    # vector_layers must still describe the pts layer.
    layer_ids = [lyr["id"] for lyr in meta.get("vector_layers", [])]
    assert "pts" in layer_ids


@pytest.mark.unit
def test_attribution_preserves_leaf_directory_layout() -> None:
    """Attribution injection preserves a multi-leaf archive layout and raw tile bytes."""
    from pmtiles.reader import MemorySource, Reader, all_tiles
    from pmtiles.tile import zxy_to_tileid

    from geodataframe_to_pmtiles._writer import _inject_attribution

    original = _build_pmtiles_archive(
        [
            (zxy_to_tileid(10, i % 1024, i // 1024), f"data-{i}".encode())
            for i in range(12_000)
        ],
        {"name": "leaf", "description": "leaf-desc", "vector_layers": []},
    )
    original_reader = Reader(MemorySource(original))
    original_header = original_reader.header()
    original_meta = original_reader.metadata()
    assert original_header["leaf_directory_length"] > 0

    patched = _inject_attribution(original, "Leaf attribution")
    patched_reader = Reader(MemorySource(patched))
    patched_header = patched_reader.header()

    assert patched_reader.metadata() == {
        **original_meta,
        "attribution": "Leaf attribution",
    }

    delta = patched_header["metadata_length"] - original_header["metadata_length"]
    for key, value in original_header.items():
        if key in {"metadata_length", "leaf_directory_offset", "tile_data_offset"}:
            continue
        assert patched_header[key] == value
    assert patched_header["leaf_directory_offset"] == (
        original_header["leaf_directory_offset"] + delta
    )
    assert (
        patched_header["tile_data_offset"]
        == original_header["tile_data_offset"] + delta
    )
    assert _pmtiles_section(
        original, original_header, "root_offset", "root_length"
    ) == _pmtiles_section(patched, patched_header, "root_offset", "root_length")
    assert _pmtiles_section(
        original, original_header, "leaf_directory_offset", "leaf_directory_length"
    ) == _pmtiles_section(
        patched, patched_header, "leaf_directory_offset", "leaf_directory_length"
    )
    assert _pmtiles_section(
        original, original_header, "tile_data_offset", "tile_data_length"
    ) == _pmtiles_section(
        patched, patched_header, "tile_data_offset", "tile_data_length"
    )
    assert list(all_tiles(MemorySource(original))) == list(
        all_tiles(MemorySource(patched))
    )


@pytest.mark.unit
def test_attribution_preserves_duplicate_tile_entries() -> None:
    """Attribution injection keeps run-length and deduplicated tile entries valid."""
    from pmtiles.reader import MemorySource, Reader, all_tiles
    from pmtiles.tile import zxy_to_tileid

    from geodataframe_to_pmtiles._writer import _inject_attribution

    original = _build_pmtiles_archive(
        [(zxy_to_tileid(5, i, 0), b"payload") for i in range(20)],
        {"name": "dup", "description": "dup-desc", "vector_layers": []},
    )
    original_reader = Reader(MemorySource(original))
    original_header = original_reader.header()
    original_meta = original_reader.metadata()
    assert original_header["tile_contents_count"] == 1

    patched = _inject_attribution(original, "Duplicate attribution")
    patched_reader = Reader(MemorySource(patched))
    patched_header = patched_reader.header()

    assert patched_reader.metadata() == {
        **original_meta,
        "attribution": "Duplicate attribution",
    }
    assert patched_header["tile_entries_count"] == original_header["tile_entries_count"]
    assert (
        patched_header["tile_contents_count"] == original_header["tile_contents_count"]
    )

    delta = patched_header["metadata_length"] - original_header["metadata_length"]
    for key, value in original_header.items():
        if key in {"metadata_length", "leaf_directory_offset", "tile_data_offset"}:
            continue
        assert patched_header[key] == value
    assert patched_header["leaf_directory_offset"] == (
        original_header["leaf_directory_offset"] + delta
    )
    assert (
        patched_header["tile_data_offset"]
        == original_header["tile_data_offset"] + delta
    )
    assert _pmtiles_section(
        original, original_header, "tile_data_offset", "tile_data_length"
    ) == _pmtiles_section(
        patched, patched_header, "tile_data_offset", "tile_data_length"
    )
    assert list(all_tiles(MemorySource(original))) == list(
        all_tiles(MemorySource(patched))
    )


@pytest.mark.unit
def test_inject_attribution_handles_raw_metadata() -> None:
    """_inject_attribution also works when metadata is stored as raw JSON."""
    from pmtiles.reader import MemorySource, Reader, all_tiles
    from pmtiles.tile import Compression, zxy_to_tileid

    from geodataframe_to_pmtiles._writer import _inject_attribution

    original = _build_pmtiles_archive(
        [(zxy_to_tileid(0, 0, 0), b"fake-mvt-tile-bytes-do-not-change")],
        {
            "name": "raw",
            "description": "raw-desc",
            "vector_layers": [],
            "unknown": {"nested": ["alpha", 1]},
        },
    )
    raw_original = _rewrite_metadata_as_raw_json(original)
    original_reader = Reader(MemorySource(raw_original))
    original_header = original_reader.header()
    assert original_header["internal_compression"] == Compression.NONE
    original_meta = original_reader.metadata()

    patched = _inject_attribution(raw_original, "Raw attribution")
    patched_reader = Reader(MemorySource(patched))
    patched_header = patched_reader.header()

    assert patched_header["internal_compression"] == Compression.NONE
    assert patched_reader.metadata() == {
        **original_meta,
        "attribution": "Raw attribution",
    }
    assert list(all_tiles(MemorySource(raw_original))) == list(
        all_tiles(MemorySource(patched))
    )


@pytest.mark.unit
def test_inject_attribution_rejects_unsupported_metadata_compression() -> None:
    """_inject_attribution refuses archives it cannot rewrite safely."""
    from pmtiles.reader import MemorySource, Reader
    from pmtiles.tile import Compression, serialize_header, zxy_to_tileid

    from geodataframe_to_pmtiles._writer import _inject_attribution

    original = _build_pmtiles_archive(
        [(zxy_to_tileid(0, 0, 0), b"fake-mvt-tile-bytes-do-not-change")],
        {"name": "bad", "description": "", "vector_layers": []},
    )
    reader = Reader(MemorySource(original))
    header = dict(reader.header())
    header["internal_compression"] = Compression.BROTLI

    unsupported = io.BytesIO()
    unsupported.write(serialize_header(header))
    unsupported.write(
        _pmtiles_section(original, reader.header(), "root_offset", "root_length")
    )
    unsupported.write(
        _pmtiles_section(
            original, reader.header(), "metadata_offset", "metadata_length"
        )
    )
    unsupported.write(
        _pmtiles_section(
            original, reader.header(), "tile_data_offset", "tile_data_length"
        )
    )

    with pytest.raises(RuntimeError, match="metadata compression"):
        _inject_attribution(unsupported.getvalue(), "Unsupported")


@pytest.mark.unit
def test_inject_attribution_gzip_output_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated gzip rewrites stay byte-identical even if the clock changes."""
    from pmtiles.tile import zxy_to_tileid

    from geodataframe_to_pmtiles import _writer as writer_module
    from geodataframe_to_pmtiles._writer import _inject_attribution

    original = _build_pmtiles_archive(
        [(zxy_to_tileid(0, 0, 0), b"fake-mvt-tile-bytes-do-not-change")],
        {"name": "deterministic", "description": "", "vector_layers": []},
    )

    times = iter([1_700_000_000, 1_700_000_123])
    monkeypatch.setattr(writer_module.gzip.time, "time", lambda: next(times))

    first = _inject_attribution(original, "Deterministic attribution")
    second = _inject_attribution(original, "Deterministic attribution")

    assert first == second


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_path_output_creates_file(tmp_path: Path) -> None:
    """write creates a file at the given Path."""
    out = tmp_path / "out.pmtiles"
    _write_safe({"lyr": _points_gdf()}, out)
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.integration
def test_path_output_replace_failure_preserves_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the final replace fails, the original Path contents stay untouched."""
    out = tmp_path / "out.pmtiles"
    out.write_bytes(b"keep this archive")

    def _raise_replace(_self: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(type(out), "replace", _raise_replace)

    with pytest.raises(OSError, match="replace failed"):
        _write_safe({"lyr": _points_gdf()}, out)

    assert out.read_bytes() == b"keep this archive"


@pytest.mark.integration
def test_bytesio_output_returns_bytes() -> None:
    """write writes a non-empty byte stream to a BytesIO."""
    buf = io.BytesIO()
    _write_safe({"lyr": _points_gdf()}, buf)
    assert buf.tell() > 0


# ---------------------------------------------------------------------------
# Datetime properties
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_datetime_stored_as_iso_string(tmp_path: Path) -> None:
    """Datetime columns are stored as ISO 8601 strings."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"ts": pd.to_datetime(["2024-01-01", "2024-06-15", "2025-12-31"])},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "datetime.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    values = [feat.GetField("ts") for feat in lyr if feat.GetField("ts") is not None]
    assert len(values) > 0
    assert all("2024" in v or "2025" in v for v in values)
    ds = None


# ---------------------------------------------------------------------------
# NumPy scalar properties
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_numpy_scalar_properties(tmp_path: Path) -> None:
    """numpy scalar types (int32, float32) are normalised correctly."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {
            "ni": np.array([1, 2, 3], dtype=np.int32),
            "nf": np.array([1.1, 2.2, 3.3], dtype=np.float32),
        },
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "numpy.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    ni_vals = {feat.GetField("ni") for feat in lyr if feat.GetField("ni") is not None}
    assert {1, 2, 3}.issubset(ni_vals)
    ds = None


# ---------------------------------------------------------------------------
# Polygon layer
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_polygon_layer(tmp_path: Path) -> None:
    """Polygon geometries are written and the layer can be reopened."""
    from osgeo import gdal

    polys = gpd.GeoDataFrame(
        {"area": [1.0, 2.0]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
        ],
        crs="EPSG:4326",
    )
    out = tmp_path / "poly.pmtiles"
    _write_safe({"polys": polys}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    lyr = ds.GetLayerByIndex(0)
    assert lyr.GetName() == "polys"
    ds = None


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_simplification_option(tmp_path: Path) -> None:
    """simplification argument is accepted and the archive is written."""
    from osgeo import gdal

    out = tmp_path / "simplif.pmtiles"
    _write_safe({"lyr": _points_gdf()}, out, simplification=2.0)
    assert out.exists()
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    ds = None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_layers_dict_raises() -> None:
    """An empty layers mapping raises EmptyLayerError."""
    with pytest.raises(EmptyLayerError):
        write({}, io.BytesIO())


@pytest.mark.unit
def test_empty_geodataframe_raises() -> None:
    """A layer with zero features raises EmptyLayerError."""
    empty = gpd.GeoDataFrame({"a": []}, geometry=[], crs="EPSG:4326")
    with pytest.raises(EmptyLayerError):
        write({"empty": empty}, io.BytesIO())


@pytest.mark.unit
def test_missing_crs_raises() -> None:
    """A GeoDataFrame with no CRS raises MissingCRSError."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)])
    with pytest.raises(MissingCRSError, match="explicit source CRS"):
        write({"lyr": gdf}, io.BytesIO())


@pytest.mark.integration
def test_wrong_crs_no_longer_raises_auto_reprojects() -> None:
    """A GeoDataFrame in a non-EPSG:4326 CRS is now auto-reprojected (no error)."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:3857")
    # Should not raise — EPSG:3857 is reprojected to EPSG:4326 automatically.
    buf = io.BytesIO()
    write({"lyr": gdf}, buf)
    assert buf.tell() > 0


@pytest.mark.unit
def test_unresolvable_crs_raises_unsupported_crs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CRS lookup failure raises UnsupportedCRSError with the original cause chained."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:3857")

    def _boom(self: object) -> int:
        raise RuntimeError("bad crs")

    monkeypatch.setattr(type(gdf.crs), "to_epsg", _boom)

    with pytest.raises(UnsupportedCRSError, match="cannot be resolved") as excinfo:
        write({"lyr": gdf}, io.BytesIO())

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "bad crs" in str(excinfo.value.__cause__)


@pytest.mark.unit
def test_transform_failure_raises_crs_transform_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reprojection failure raises CRSTransformError with the original cause chained."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:3857")

    def _boom(self: object, *args: object, **kwargs: object) -> gpd.GeoDataFrame:
        raise ValueError("transform failed")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_crs", _boom)

    with pytest.raises(CRSTransformError, match="coordinate transformation") as excinfo:
        write({"lyr": gdf}, io.BytesIO())

    assert isinstance(excinfo.value.__cause__, ValueError)
    assert "transform failed" in str(excinfo.value.__cause__)


@pytest.mark.unit
def test_non_mutation_of_input_gdf() -> None:
    """write does not mutate the caller's GeoDataFrame CRS or geometries.

    This test does not require GDAL: reprojection is performed on a copy inside
    write via gdf.to_crs(), which must not modify the input.  If GDAL is
    unavailable the call raises RuntimeError after the reprojection step, but the
    check happens before writing.
    """
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0)],
        crs="EPSG:3857",
    )
    original_crs = gdf.crs
    original_geom = list(gdf.geometry)
    with contextlib.suppress(RuntimeError):
        # GDAL not available in this environment; mutation check still valid
        write({"lyr": gdf}, io.BytesIO())
    assert gdf.crs == original_crs, "CRS of input GeoDataFrame must not be changed"
    assert list(gdf.geometry) == original_geom, (
        "Geometries of input GDF must not be changed"
    )


# ---------------------------------------------------------------------------
# CRS reprojection — semantic decoder tests
# ---------------------------------------------------------------------------


def _point_gdf_4326() -> gpd.GeoDataFrame:
    """A known point in EPSG:4326 (lon/lat)."""
    return gpd.GeoDataFrame(
        {"label": ["origin"]},
        geometry=[Point(10.0, 52.0)],  # lon=10, lat=52
        crs="EPSG:4326",
    )


def _point_gdf_3857() -> gpd.GeoDataFrame:
    """The same point as above reprojected to EPSG:3857."""
    return _point_gdf_4326().to_crs("EPSG:3857")


def _point_gdf_projected() -> gpd.GeoDataFrame:
    """The same point in a local projected CRS (ETRS89 / UTM zone 32N)."""
    return _point_gdf_4326().to_crs("EPSG:25832")


def _point_gdf_non_epsg() -> gpd.GeoDataFrame:
    """The same point expressed via a non-EPSG PROJ string."""
    import pyproj

    crs_obj = pyproj.CRS.from_proj4("+proj=longlat +datum=WGS84 +no_defs")
    return gpd.GeoDataFrame(
        {"label": ["origin"]},
        geometry=[Point(10.0, 52.0)],
        crs=crs_obj,
    )


@pytest.mark.integration
def test_epsg4326_source_decodes_correctly(tmp_path: Path) -> None:
    """EPSG:4326 source (baseline) decodes to a Point feature with expected property."""
    from .pmtiles_semantics import read_pmtiles_archive, summarize_features

    out = tmp_path / "crs4326.pmtiles"
    _write_safe({"pts": _point_gdf_4326()}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    assert "pts" in layers
    summary = summarize_features(layers["pts"]["features"])
    assert summary["feature_count"] >= 1
    assert "Point" in summary["geometry_types"]
    assert "label" in summary["property_keys"]


@pytest.mark.integration
def test_epsg3857_source_decodes_equivalently(tmp_path: Path) -> None:
    """EPSG:3857 source is auto-reprojected; decoded result is semantically equivalent to EPSG:4326."""
    from .pmtiles_semantics import read_pmtiles_archive, summarize_features

    out = tmp_path / "crs3857.pmtiles"
    _write_safe({"pts": _point_gdf_3857()}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    assert "pts" in layers
    summary = summarize_features(layers["pts"]["features"])
    assert summary["feature_count"] >= 1
    assert "Point" in summary["geometry_types"]
    assert "label" in summary["property_keys"]


@pytest.mark.integration
def test_projected_crs_decodes_equivalently(tmp_path: Path) -> None:
    """A local projected CRS (EPSG:25832) is auto-reprojected; result decodes correctly."""
    from .pmtiles_semantics import read_pmtiles_archive, summarize_features

    out = tmp_path / "crs25832.pmtiles"
    _write_safe({"pts": _point_gdf_projected()}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    assert "pts" in layers
    summary = summarize_features(layers["pts"]["features"])
    assert summary["feature_count"] >= 1
    assert "Point" in summary["geometry_types"]


@pytest.mark.integration
def test_non_epsg_proj_string_decodes_correctly(tmp_path: Path) -> None:
    """A non-EPSG PROJ string CRS is accepted and decoded correctly."""
    from .pmtiles_semantics import read_pmtiles_archive, summarize_features

    out = tmp_path / "crs_proj.pmtiles"
    _write_safe({"pts": _point_gdf_non_epsg()}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    assert "pts" in layers
    summary = summarize_features(layers["pts"]["features"])
    assert summary["feature_count"] >= 1
    assert "Point" in summary["geometry_types"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source_factory", "label"),
    [
        (_point_gdf_3857, "epsg3857"),
        (_point_gdf_projected, "epsg25832"),
        (_point_gdf_non_epsg, "proj"),
    ],
)
def test_auto_reprojected_sources_match_pretransformed_4326(
    tmp_path: Path,
    source_factory,
    label: str,
) -> None:
    """Auto-reprojected sources decode exactly like a pre-transformed EPSG:4326 input."""
    from .pmtiles_semantics import read_pmtiles_archive

    expected = tmp_path / f"expected-{label}.pmtiles"
    actual = tmp_path / f"actual-{label}.pmtiles"
    _write_safe({"pts": _point_gdf_4326()}, expected, min_zoom=0, max_zoom=0)
    _write_safe({"pts": source_factory()}, actual, min_zoom=0, max_zoom=0)

    expected_features = read_pmtiles_archive(expected, z=0, x=0, y=0)[2]["pts"][
        "features"
    ]
    actual_features = read_pmtiles_archive(actual, z=0, x=0, y=0)[2]["pts"]["features"]

    assert actual_features == expected_features


@pytest.mark.integration
def test_mixed_crs_layers_decode_correctly(tmp_path: Path) -> None:
    """Mixed-CRS layers (EPSG:4326 + EPSG:3857) each decode to the correct geometry type."""
    from .pmtiles_semantics import read_pmtiles_archive, summarize_features

    out = tmp_path / "mixed_crs.pmtiles"
    _write_safe(
        {
            "pts_4326": _point_gdf_4326(),
            "pts_3857": _point_gdf_3857(),
        },
        out,
        min_zoom=0,
        max_zoom=0,
    )
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    for layer_name in ("pts_4326", "pts_3857"):
        assert layer_name in layers, f"Layer {layer_name!r} missing from decoded tile"
        summary = summarize_features(layers[layer_name]["features"])
        assert summary["feature_count"] >= 1
        assert "Point" in summary["geometry_types"]

    assert layers["pts_3857"]["features"] == layers["pts_4326"]["features"]


@pytest.mark.integration
def test_reprojection_preserves_property_values(tmp_path: Path) -> None:
    """Property values survive reprojection from EPSG:3857 to EPSG:4326 unchanged."""
    from .pmtiles_semantics import read_pmtiles_archive

    gdf = gpd.GeoDataFrame(
        {"name": ["alpha", "beta"], "score": [42, 99]},
        geometry=[Point(0.0, 0.0), Point(1_000_000.0, 1_000_000.0)],
        crs="EPSG:3857",
    )
    out = tmp_path / "repro_props.pmtiles"
    _write_safe({"lyr": gdf}, out, min_zoom=0, max_zoom=0)
    _, _, layers = read_pmtiles_archive(out, z=0, x=0, y=0)
    assert "lyr" in layers
    names = {f["properties"].get("name") for f in layers["lyr"]["features"]}
    assert {"alpha", "beta"}.issubset(names)


@pytest.mark.unit
def test_non_mutation_input_geometry_unchanged() -> None:
    """Reprojection from EPSG:3857 does not change the original geometry objects."""
    from shapely.geometry import Point as ShapelyPoint

    orig_geom = ShapelyPoint(1_000_000.0, 6_000_000.0)
    gdf = gpd.GeoDataFrame(
        {"x": [1]},
        geometry=[orig_geom],
        crs="EPSG:3857",
    )
    with contextlib.suppress(RuntimeError):
        # GDAL not available; mutation check is still valid
        write({"lyr": gdf}, io.BytesIO())
    # Original geometry must be unchanged.
    assert gdf.geometry.iloc[0].equals(orig_geom)
    assert gdf.crs.to_epsg() == 3857


@pytest.mark.unit
def test_non_geodataframe_raises() -> None:
    """A non-GeoDataFrame value in layers raises TypeError."""
    with pytest.raises(TypeError):
        write({"lyr": "not a gdf"}, io.BytesIO())  # type: ignore[arg-type]


@pytest.mark.unit
def test_unsupported_property_type_raises() -> None:
    """A column with an unsupported type raises UnsupportedPropertyTypeError."""

    class _Weird:
        pass

    gdf = gpd.GeoDataFrame(
        {"obj": [_Weird()]},
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )
    with pytest.raises(UnsupportedPropertyTypeError):
        write({"lyr": gdf}, io.BytesIO())


@pytest.mark.unit
def test_invalid_zoom_range_raises() -> None:
    """min_zoom > max_zoom raises ValueError."""
    with pytest.raises(ValueError, match="min_zoom"):
        write({"lyr": _points_gdf()}, io.BytesIO(), min_zoom=5, max_zoom=3)


@pytest.mark.unit
def test_zoom_out_of_range_raises() -> None:
    """Zoom levels outside 0-22 raise ValueError."""
    with pytest.raises(ValueError, match="max_zoom"):
        write({"lyr": _points_gdf()}, io.BytesIO(), max_zoom=30)


# ---------------------------------------------------------------------------
# Optimised-path semantic equivalence
# ---------------------------------------------------------------------------


def _rich_points_gdf(n: int = 20) -> gpd.GeoDataFrame:
    """Return a GeoDataFrame covering all normalised property kinds.

    Columns:
      idx     - int64 sequential index
      score   - float64 (some NaN)
      active  - bool (some None)
      label   - plain str
      geom    - Point
    """
    rng = np.random.default_rng(0)
    lons = rng.uniform(-180.0, 180.0, n)
    lats = rng.uniform(-90.0, 90.0, n)
    scores = rng.uniform(0.0, 1.0, n).tolist()
    scores[3] = float("nan")  # one NaN → null in output
    actives: list[object] = [bool(i % 2) for i in range(n)]
    actives[5] = None  # one None → null in output
    return gpd.GeoDataFrame(
        {
            "idx": list(range(n)),
            "score": scores,
            "active": actives,
            "label": [f"pt_{i}" for i in range(n)],
        },
        geometry=[Point(x, y) for x, y in zip(lons, lats, strict=True)],
        crs="EPSG:4326",
    )


@pytest.mark.integration
def test_optimised_path_feature_count_and_property_types(
    tmp_path: Path,
) -> None:
    """Optimised write path preserves feature count and property types.

    Decodes the archive via GDAL and verifies:
    - feature count is non-zero
    - expected property keys are present
    - bool-kind column decoded as 0 / 1 integer (MVT has no native bool)
    - float NaN row stores null (GetField returns None; see GDAL PMTiles note)
    - None bool row stores null
    """
    from osgeo import gdal

    gdf = _rich_points_gdf(n=50)
    out = tmp_path / "rich.pmtiles"
    _write_ignore({"pts": gdf}, out, min_zoom=0, max_zoom=4)

    # Decode via GDAL round-trip; PMTiles returns null fields as None,
    # not via OGR's IsFieldNull (see test_nullable_float_nan_is_null).
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    lyr = ds.GetLayerByIndex(0)
    assert lyr is not None

    bool_values: set[object] = set()
    has_null_score = False
    has_null_active = False
    feat_count = 0

    for f in lyr:
        feat_count += 1
        if f.GetField("score") is None:
            has_null_score = True
        if f.GetField("active") is None:
            has_null_active = True
        else:
            bool_values.add(f.GetField("active"))

    assert feat_count > 0, "no features decoded"
    assert has_null_score, "NaN score should be null (None) in decoded output"
    assert has_null_active, "None active should be null (None) in decoded output"
    # bools encoded as 0/1 integers (MVT has no native bool type)
    assert bool_values.issubset({0, 1}), f"bool values not 0/1: {bool_values}"

    ds = None


@pytest.mark.integration
def test_optimised_path_and_bytesio_decoded_equivalent(tmp_path: Path) -> None:
    """Path and BytesIO output modes produce semantically equivalent archives.

    Each call produces a different vsimem UUID, which GDAL may include as the
    archive name in metadata, so byte-level equality is not guaranteed.  This
    test instead decodes both archives and compares feature counts and tile
    type metadata.
    """
    from osgeo import gdal

    gdf = _rich_points_gdf(n=30)

    path_out = tmp_path / "path.pmtiles"
    _write_ignore({"pts": gdf}, path_out, min_zoom=0, max_zoom=4)

    buf = io.BytesIO()
    _write_ignore({"pts": gdf}, buf, min_zoom=0, max_zoom=4)
    buf_out = tmp_path / "buf.pmtiles"
    buf_out.write_bytes(buf.getvalue())

    def _count_features(p: Path) -> int:
        ds = gdal.OpenEx(str(p), gdal.OF_VECTOR)
        assert ds is not None, f"gdal.OpenEx could not open {p}"
        lyr = ds.GetLayerByIndex(0)
        assert lyr is not None, f"GetLayerByIndex(0) returned None for {p}"
        count = sum(1 for _ in lyr)
        ds = None  # release dataset handle to avoid file lock / leak
        return count

    assert _count_features(path_out) == _count_features(buf_out), (
        "Path and BytesIO decode to different feature counts"
    )


@pytest.mark.integration
def test_optimised_path_string_isoformat_fallback(tmp_path: Path) -> None:
    """String-kind values with an isoformat method still round-trip as strings."""
    from osgeo import gdal

    class _IsoValue:
        def __init__(self, label: str) -> None:
            self.label = label

        def isoformat(self) -> str:
            return f"iso-{self.label}"

    gdf = gpd.GeoDataFrame(
        {"stamp": [_IsoValue("a"), _IsoValue("b"), _IsoValue("c")]},
        geometry=[Point(float(i), 0.0) for i in range(3)],
        crs="EPSG:4326",
    )
    out = tmp_path / "isoformat.pmtiles"
    _write_safe({"pts": gdf}, out, min_zoom=0, max_zoom=0)

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    lyr = ds.GetLayerByIndex(0)
    assert lyr is not None
    values = [feat.GetField("stamp") for feat in lyr]
    assert values == ["iso-a", "iso-b", "iso-c"]
    ds = None


@pytest.mark.integration
def test_optimised_path_skips_invalid_wkb_geometry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A geometry conversion failure is skipped without stopping the layer write."""
    from osgeo import gdal, ogr

    gdf = gpd.GeoDataFrame(
        {"idx": [1, 2, 3]},
        geometry=[
            Point(0.0, 0.0),
            Point(1.0, 1.0),
            Point(2.0, 2.0),
        ],
        crs="EPSG:4326",
    )

    original = ogr.CreateGeometryFromWkb
    calls = {"count": 0}

    def _fake_create_geometry_from_wkb(wkb: bytes) -> object | None:
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original(wkb)

    monkeypatch.setattr(ogr, "CreateGeometryFromWkb", _fake_create_geometry_from_wkb)

    out = tmp_path / "skip-geometry.pmtiles"
    _write_safe({"pts": gdf}, out, min_zoom=0, max_zoom=0)

    assert calls["count"] == 3
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    lyr = ds.GetLayerByIndex(0)
    assert lyr is not None
    assert sum(1 for _ in lyr) == 2
    ds = None


@pytest.mark.integration
def test_optimised_path_feature_order_preserved(tmp_path: Path) -> None:
    """Optimised path writes features in input (DataFrame row) order."""
    from osgeo import ogr

    n = 10
    gdf = gpd.GeoDataFrame(
        {"seq": list(range(n))},
        geometry=[Point(float(i), 0.0) for i in range(n)],
        crs="EPSG:4326",
    )
    out = tmp_path / "ordered.pmtiles"
    _write_ignore({"pts": gdf}, out)

    ds = ogr.Open(str(out))
    lyr = ds.GetLayer("pts")
    decoded_seq = []
    lyr.ResetReading()
    while True:
        f = lyr.GetNextFeature()
        if f is None:
            break
        decoded_seq.append(f.GetField("seq"))

    # All unique first occurrences must be in ascending input order.
    seen: set[int] = set()
    first_occurrences: list[int] = []
    for v in decoded_seq:
        if v not in seen:
            seen.add(v)
            first_occurrences.append(v)

    assert first_occurrences == sorted(first_occurrences), (
        f"Feature order not preserved: first occurrences = {first_occurrences}"
    )
    ds = None


# ---------------------------------------------------------------------------
# gpm.write public API: invocation forms and invalid argument combinations
# ---------------------------------------------------------------------------


def test_write_single_gdf_without_layer_raises() -> None:
    """write(gdf, output) without layer= raises TypeError."""
    gdf = _points_gdf()
    with pytest.raises(TypeError, match="layer"):
        write(gdf, "ignored.pmtiles")  # type: ignore[call-overload]


def test_write_mapping_with_layer_raises() -> None:
    """write(mapping, output, layer=...) raises TypeError."""
    gdf = _points_gdf()
    buf = io.BytesIO()
    with pytest.raises(TypeError, match="layer"):
        write({"pts": gdf}, buf, layer="pts")  # type: ignore[call-overload]


def test_write_mapping_with_layer_none_raises() -> None:
    """write(mapping, output, layer=None) raises TypeError.

    Explicitly passing ``layer=None`` to the mapping form must be rejected
    because *None* is an invalid layer argument in that context — it is not
    the same as omitting the keyword entirely.
    """
    gdf = _points_gdf()
    buf = io.BytesIO()
    with pytest.raises(TypeError, match="layer"):
        write({"pts": gdf}, buf, layer=None)  # type: ignore[call-overload]


def test_write_single_gdf_with_layer_none_raises() -> None:
    """write(gdf, output, layer=None) raises TypeError.

    Explicitly passing ``layer=None`` to the single-frame form is as invalid
    as omitting the keyword — both must raise TypeError.
    """
    gdf = _points_gdf()
    buf = io.BytesIO()
    with pytest.raises(TypeError, match="layer"):
        write(gdf, buf, layer=None)  # type: ignore[call-overload]


@pytest.mark.integration
def test_write_single_gdf_form(tmp_path: Path) -> None:
    from osgeo import gdal

    gdf = _points_gdf()
    out = tmp_path / "single.pmtiles"
    write(gdf, out, layer="mypoints", min_zoom=0, max_zoom=4, on_overflow="unsafe")

    assert out.exists()
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    layer_names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    assert "mypoints" in layer_names
    ds = None


@pytest.mark.integration
def test_write_mapping_form(tmp_path: Path) -> None:
    """write(mapping, output) produces a valid multi-layer archive."""
    from osgeo import gdal

    out = tmp_path / "multi.pmtiles"
    write(
        {"pts": _points_gdf(), "lns": _lines_gdf()},
        out,
        min_zoom=0,
        max_zoom=4,
        on_overflow="unsafe",
    )

    assert out.exists()
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    layer_names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    assert "pts" in layer_names
    assert "lns" in layer_names
    ds = None


def test_write_is_only_public_symbol() -> None:
    """gpm.write exists and write_pmtiles is not exposed."""
    import geodataframe_to_pmtiles as gpm

    assert callable(gpm.write)
    assert not hasattr(gpm, "write_pmtiles")
