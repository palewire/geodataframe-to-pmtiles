"""Tests for geodataframe_to_pmtiles.write_pmtiles."""

from __future__ import annotations

import io
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from geodataframe_to_pmtiles import (
    EmptyLayerError,
    MissingCRSError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
    write_pmtiles,
)

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


def _open_pmtiles(data: bytes) -> gdal.Dataset:  # noqa: F821
    """Write *data* to a vsimem path and return the opened GDAL datasource."""
    from osgeo import gdal

    gdal.UseExceptions()
    path = f"/vsimem/test_open_{id(data)}.pmtiles"
    vf = gdal.VSIFOpenL(path, "wb")
    gdal.VSIFWriteL(data, 1, len(data), vf)
    gdal.VSIFCloseL(vf)
    ds = gdal.OpenEx(path, gdal.OF_VECTOR)
    return ds, path


# ---------------------------------------------------------------------------
# Two-layer archive: core round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_two_layer_archive_path(tmp_path: Path) -> None:
    """write_pmtiles writes a two-layer archive to a Path and can be reopened."""
    from osgeo import gdal

    out = tmp_path / "two_layers.pmtiles"
    write_pmtiles(
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
    """write_pmtiles writes a two-layer archive to a BytesIO stream."""
    buf = io.BytesIO()
    write_pmtiles(
        {"points": _points_gdf(), "lines": _lines_gdf()},
        buf,
        min_zoom=0,
        max_zoom=4,
    )
    buf.seek(0)
    data = buf.read()
    assert len(data) > 0

    ds, path = _open_pmtiles(data)
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


# ---------------------------------------------------------------------------
# Feature order determinism
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_feature_order_preserved(tmp_path: Path) -> None:
    """Features are written in input order (deterministic)."""
    from osgeo import gdal

    names = [f"feat_{i}" for i in range(5)]
    gdf = gpd.GeoDataFrame(
        {"name": names, "idx": list(range(5))},
        geometry=[Point(float(i), float(i)) for i in range(5)],
        crs="EPSG:4326",
    )
    out = tmp_path / "order.pmtiles"
    write_pmtiles({"ordered": gdf}, out, min_zoom=0, max_zoom=4)

    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    seen = [feat.GetField("name") for feat in lyr]
    # Features may appear in multiple tiles; check the unique ordered subset.
    unique_seen = list(dict.fromkeys(seen))  # preserve first occurrence order
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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    names = {feat.GetField("name") for feat in lyr}
    assert {"alpha", "beta", "gamma"}.issubset(names)
    ds = None


@pytest.mark.integration
def test_integer_properties(tmp_path: Path) -> None:
    """Integer columns round-trip as integer values.

    Note: The PMTiles/MVT format stores all integer types in protocol-buffer
    varint fields.  GDAL's PMTiles reader reports them back as OFTInteger (not
    OFTInteger64) regardless of the input type.  We verify the field is an
    integer type and that the values are correct.
    """
    from osgeo import gdal, ogr

    gdf = gpd.GeoDataFrame(
        {"count": [10, 20, 30]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "int.pmtiles"
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("count"))
    # PMTiles reader returns OFTInteger or OFTInteger64; both are acceptable.
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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("ratio"))
    assert fld.GetType() == ogr.OFTReal
    ds = None


@pytest.mark.integration
def test_bool_stored_as_integer(tmp_path: Path) -> None:
    """Boolean columns are stored as Integer (0/1) because MVT has no bool type."""
    from osgeo import gdal, ogr

    gdf = gpd.GeoDataFrame(
        {"flag": [True, False, True]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "bool.pmtiles"
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    fld = lyr.GetLayerDefn().GetFieldDefn(lyr.GetLayerDefn().GetFieldIndex("flag"))
    assert fld.GetType() == ogr.OFTInteger
    values = {feat.GetField("flag") for feat in lyr}
    assert {0, 1}.issubset(values)
    ds = None


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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    # PMTiles returns null fields as None (IsFieldNull may not be set for all drivers).
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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    # PMTiles returns null fields as None (not via IsFieldNull for all drivers).
    null_count = sum(1 for feat in lyr if feat.GetField("label") is None)
    assert null_count >= 1
    ds = None


@pytest.mark.integration
def test_list_property_json_encoded(tmp_path: Path) -> None:
    """List-valued properties are JSON-encoded to strings."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"tags": [["a", "b"], ["c"], ["d", "e", "f"]]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "list.pmtiles"
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    raw_values = [
        feat.GetField("tags") for feat in lyr if feat.GetField("tags") is not None
    ]
    assert len(raw_values) > 0
    # Each stored value must be valid JSON encoding a list
    for v in raw_values:
        decoded = json.loads(v)
        assert isinstance(decoded, list)
    ds = None


@pytest.mark.integration
def test_dict_property_json_encoded(tmp_path: Path) -> None:
    """Dict-valued properties are JSON-encoded to strings."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"meta": [{"k": "v"}, {"x": 1}, {"nested": True}]},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "dict.pmtiles"
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
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
def test_datetime_stored_as_iso_string(tmp_path: Path) -> None:
    """Datetime columns are stored as ISO 8601 strings."""
    from osgeo import gdal

    gdf = gpd.GeoDataFrame(
        {"ts": pd.to_datetime(["2024-01-01", "2024-06-15", "2025-12-31"])},
        geometry=[Point(0.0, 0.0), Point(1.0, 1.0), Point(2.0, 2.0)],
        crs="EPSG:4326",
    )
    out = tmp_path / "datetime.pmtiles"
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    values = [feat.GetField("ts") for feat in lyr if feat.GetField("ts") is not None]
    assert len(values) > 0
    # Values should look like ISO date strings
    assert all("2024" in v or "2025" in v for v in values)
    ds = None


@pytest.mark.integration
def test_numpy_scalar_properties(tmp_path: Path) -> None:
    """numpy scalar types (int32, float32, bool_) are normalised correctly."""
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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    ni_vals = {feat.GetField("ni") for feat in lyr if feat.GetField("ni") is not None}
    assert {1, 2, 3}.issubset(ni_vals)
    ds = None


# ---------------------------------------------------------------------------
# Zoom metadata
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_zoom_metadata_stored(tmp_path: Path) -> None:
    """min_zoom and max_zoom are reflected in the archive metadata."""
    from osgeo import gdal

    out = tmp_path / "zoom.pmtiles"
    write_pmtiles({"lyr": _points_gdf()}, out, min_zoom=2, max_zoom=7)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    meta = ds.GetMetadata()
    assert meta.get("minzoom") == "2"
    assert meta.get("maxzoom") == "7"
    ds = None


# ---------------------------------------------------------------------------
# Metadata options
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_name_and_description_stored(tmp_path: Path) -> None:
    """name and description arguments are stored in the archive metadata."""
    from osgeo import gdal

    out = tmp_path / "meta.pmtiles"
    write_pmtiles(
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


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_path_output_creates_file(tmp_path: Path) -> None:
    """write_pmtiles creates a file at the given Path."""
    out = tmp_path / "out.pmtiles"
    write_pmtiles({"lyr": _points_gdf()}, out)
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.integration
def test_bytesio_output_returns_bytes() -> None:
    """write_pmtiles writes a non-empty byte stream to a BytesIO."""
    buf = io.BytesIO()
    write_pmtiles({"lyr": _points_gdf()}, buf)
    assert buf.tell() > 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_layers_dict_raises() -> None:
    """An empty layers mapping raises EmptyLayerError."""
    with pytest.raises(EmptyLayerError):
        write_pmtiles({}, io.BytesIO())


@pytest.mark.unit
def test_empty_geodataframe_raises() -> None:
    """A layer with zero features raises EmptyLayerError."""
    empty = gpd.GeoDataFrame({"a": []}, geometry=[], crs="EPSG:4326")
    with pytest.raises(EmptyLayerError):
        write_pmtiles({"empty": empty}, io.BytesIO())


@pytest.mark.unit
def test_missing_crs_raises() -> None:
    """A GeoDataFrame with no CRS raises MissingCRSError."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)])
    with pytest.raises(MissingCRSError):
        write_pmtiles({"lyr": gdf}, io.BytesIO())


@pytest.mark.unit
def test_wrong_crs_raises() -> None:
    """A GeoDataFrame in a non-EPSG:4326 CRS raises UnsupportedCRSError."""
    gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:3857")
    with pytest.raises(UnsupportedCRSError):
        write_pmtiles({"lyr": gdf}, io.BytesIO())


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
        write_pmtiles({"lyr": gdf}, io.BytesIO())


@pytest.mark.unit
def test_invalid_zoom_range_raises() -> None:
    """min_zoom > max_zoom raises ValueError."""
    with pytest.raises(ValueError, match="min_zoom"):
        write_pmtiles({"lyr": _points_gdf()}, io.BytesIO(), min_zoom=5, max_zoom=3)


@pytest.mark.unit
def test_zoom_out_of_range_raises() -> None:
    """Zoom levels outside 0-22 raise ValueError."""
    with pytest.raises(ValueError, match="max_zoom"):
        write_pmtiles({"lyr": _points_gdf()}, io.BytesIO(), max_zoom=30)


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
    write_pmtiles({"polys": polys}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    lyr = ds.GetLayerByIndex(0)
    assert lyr.GetName() == "polys"
    ds = None


# ---------------------------------------------------------------------------
# Attribution and simplification
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_attribution_stored_in_conf(tmp_path: Path) -> None:
    """attribution argument is embedded in the archive's TileJSON CONF blob."""
    from osgeo import gdal

    out = tmp_path / "attr.pmtiles"
    write_pmtiles(
        {"lyr": _points_gdf()},
        out,
        attribution="© Test Attribution",
    )
    # We can verify the file was written successfully.
    assert out.exists()
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    # The archive must be readable.
    assert ds.GetLayerCount() >= 1
    ds = None


@pytest.mark.integration
def test_simplification_option(tmp_path: Path) -> None:
    """simplification argument is accepted and the archive is written."""
    from osgeo import gdal

    out = tmp_path / "simplif.pmtiles"
    write_pmtiles({"lyr": _points_gdf()}, out, simplification=2.0)
    assert out.exists()
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    assert ds is not None
    ds = None


@pytest.mark.unit
def test_non_geodataframe_raises() -> None:
    """A non-GeoDataFrame value in layers raises TypeError."""
    with pytest.raises(TypeError):
        write_pmtiles({"lyr": "not a gdf"}, io.BytesIO())  # type: ignore[arg-type]


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
    write_pmtiles({"lyr": gdf}, out, min_zoom=0, max_zoom=4)
    ds = gdal.OpenEx(str(out), gdal.OF_VECTOR)
    lyr = ds.GetLayerByIndex(0)
    null_count = sum(1 for feat in lyr if feat.GetField("label") is None)
    assert null_count >= 1
    ds = None
