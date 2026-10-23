"""Empty maps preserve metadata without inventing tile features."""

from __future__ import annotations

import importlib.util
import io

import geopandas as gpd
import pandas as pd
import pytest
from pmtiles.reader import MemorySource, Reader
from shapely.geometry import Point

from geodataframe_to_pmtiles import (
    EmptyLayerError,
    LayerZoomSpec,
    MissingCRSError,
    write,
)


def empty_frame() -> gpd.GeoDataFrame:
    """Return an empty frame with declared numeric, boolean and string columns.

    Returns:
        A zero-row GeoDataFrame with an explicit WGS84 CRS.

    Examples:
        ``write({"empty": empty_frame()}, io.BytesIO())``.
    """
    return gpd.GeoDataFrame(
        {
            "count": pd.Series(dtype="Int64"),
            "score": pd.Series(dtype="float64"),
            "active": pd.Series(dtype="boolean"),
            "name": pd.Series(dtype="string"),
            "date": pd.Series(dtype="datetime64[ns]"),
        },
        geometry=[],
        crs="EPSG:4326",
    )


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857"])
def test_empty_archive_metadata_tiles_and_determinism(crs):
    """Retain typed fields and zooms without changing input or generating tiles.

    Args:
        crs: Explicit input CRS, including an empty reprojection case.

    Returns:
        None.

    Examples:
        Run ``pytest tests/test_empty.py``.
    """
    frame = empty_frame().to_crs(crs)
    original = frame.copy()
    outputs = []
    for _ in range(2):
        output = io.BytesIO()
        write(
            {"storms": frame, "selection": frame},
            output,
            min_zoom=1,
            max_zoom=7,
            layer_zooms={"selection": LayerZoomSpec(minzoom=2, maxzoom=5)},
            name="No storms",
            description="Completed empty day",
            attribution="Reuters",
        )
        outputs.append(output.getvalue())
    assert outputs[0] == outputs[1]
    pd.testing.assert_frame_equal(frame, original)
    reader = Reader(MemorySource(outputs[0]))
    header = reader.header()
    assert header["addressed_tiles_count"] == header["tile_entries_count"] == 0
    assert header["tile_contents_count"] == header["tile_data_length"] == 0
    assert (header["min_zoom"], header["max_zoom"]) == (1, 7)
    assert header["min_lon_e7"] == -1800000000
    assert header["center_lon_e7"] == header["center_lat_e7"] == 0
    for z, x, y in ((0, 0, 0), (1, 0, 0), (7, 64, 64)):
        assert reader.get(z, x, y) is None
    metadata = reader.metadata()
    assert metadata["attribution"] == "Reuters"
    assert metadata["name"] == "No storms"
    assert metadata["description"] == "Completed empty day"
    layers = {layer["id"]: layer for layer in metadata["vector_layers"]}
    assert set(layers) == {"storms", "selection"}
    assert layers["storms"]["fields"] == {
        "count": "Number",
        "score": "Number",
        "active": "Boolean",
        "name": "String",
        "date": "String",
    }
    assert (layers["selection"]["minzoom"], layers["selection"]["maxzoom"]) == (2, 5)


def test_empty_single_frame_replaces_path(tmp_path):
    """Replace existing bytes with a readable empty archive through the normal API.

    Args:
        tmp_path: Isolated temporary output directory.

    Returns:
        None.

    Examples:
        Run ``pytest tests/test_empty.py``.
    """
    path = tmp_path / "map.pmtiles"
    path.write_bytes(b"old populated map")
    write(empty_frame(), str(path), layer="storms")
    reader = Reader(MemorySource(path.read_bytes()))
    assert reader.metadata()["vector_layers"][0]["id"] == "storms"
    assert reader.get(0, 0, 0) is None
    assert list(tmp_path.iterdir()) == [path]


def test_empty_missing_crs_still_fails():
    """Require a CRS even when there are no features to transform.

    Returns:
        None.

    Examples:
        Run ``pytest tests/test_empty.py``.
    """
    with pytest.raises(MissingCRSError):
        write(gpd.GeoDataFrame(geometry=[]), io.BytesIO(), layer="empty")


@pytest.mark.skipif(
    importlib.util.find_spec("osgeo") is None, reason="GDAL unavailable"
)
@pytest.mark.parametrize("mixed", [False, True])
def test_empty_layers_reopen_through_gdal(tmp_path, mixed):
    """Native GDAL sees declared empty layers alongside any populated layer.

    Args:
        tmp_path: Isolated temporary output directory.
        mixed: Whether another layer contains a real feature.

    Returns:
        None.

    Examples:
        Run with a GDAL Python runtime: ``pytest tests/test_empty.py``.
    """
    from osgeo import gdal

    frame = empty_frame()
    layers = {"empty": frame}
    if mixed:
        layers["points"] = gpd.GeoDataFrame(
            {"id": [1]}, geometry=[Point(0, 0)], crs=4326
        )
    path = tmp_path / "map.pmtiles"
    write(layers, path, max_zoom=0)
    dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    assert dataset is not None
    assert dataset.GetLayerCount() == len(layers)
    empty = dataset.GetLayerByName("empty")
    assert empty is not None
    assert empty.GetFeatureCount() == 0
    fields = empty.GetLayerDefn()
    assert {
        fields.GetFieldDefn(i).GetName() for i in range(fields.GetFieldCount())
    } >= {"count", "score", "active", "name", "date"}
    reader = Reader(MemorySource(path.read_bytes()))
    if mixed:
        import gzip

        from mapbox_vector_tile import decode

        tile = reader.get(0, 0, 0)
        assert tile is not None
        if reader.header()["tile_compression"].name == "GZIP":
            tile = gzip.decompress(tile)
        decoded = decode(tile)
        assert len(decoded["points"]["features"]) == 1
        assert "empty" not in decoded or decoded["empty"]["features"] == []
    else:
        assert reader.get(0, 0, 0) is None
    dataset = None


def test_nonempty_input_with_no_usable_geometry_is_not_an_empty_map():
    """Keep malformed nonempty inputs distinguishable from intentional emptiness.

    Returns:
        None.

    Examples:
        Run ``pytest tests/test_empty.py``.
    """
    frame = gpd.GeoDataFrame({"id": [1]}, geometry=[None], crs=4326)
    with pytest.raises(EmptyLayerError, match="no valid features"):
        write({"bad": frame, "empty": empty_frame()}, io.BytesIO())


@pytest.mark.parametrize("name", ["", "  ", "bad\x00name"])
def test_empty_mapping_rejects_invalid_layer_names(name):
    """Require usable layer names even when GDAL is not needed.

    Args:
        name: Empty, whitespace or null-containing name.

    Returns:
        None.

    Examples:
        Run ``pytest tests/test_empty.py``.
    """
    output = io.BytesIO(b"existing bytes")
    with pytest.raises(TypeError, match="Layer names"):
        write({name: empty_frame()}, output)
    assert output.getvalue() == b"existing bytes"
