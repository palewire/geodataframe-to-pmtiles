"""Semantic conformance tests for real-world and Tippecanoe fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest

from geodataframe_to_pmtiles import write_pmtiles

from .pmtiles_semantics import (
    feature_property_sequence,
    read_pmtiles_archive,
    summarize_band_counts,
    summarize_features,
    summarize_header,
    summarize_metadata,
)

FIXTURES = Path(__file__).with_name("fixtures") / "conformance"
CLIMATE_FIXTURE = FIXTURES / "climate"
TIPPECANOE_FIXTURE = FIXTURES / "tippecanoe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_tippecanoe_features(path: Path) -> list[dict[str, Any]]:
    """Load an upstream Tippecanoe fixture into plain GeoJSON features."""
    text = path.read_text().strip()
    try:
        loaded: Any = json.loads(text)
    except json.JSONDecodeError:
        if '"FeatureCollection"' in text:
            loaded = json.loads(re.sub(r",(\s*\])", r"\1", text))
        else:
            loaded = [
                json.loads(line.rstrip(","))
                for line in text.splitlines()
                if line.strip()
            ]

    if isinstance(loaded, dict):
        if loaded.get("type") == "FeatureCollection":
            return list(loaded["features"])
        if loaded.get("type") == "Feature":
            return [loaded]
    if isinstance(loaded, list):
        return loaded

    msg = f"Unsupported Tippecanoe fixture format in {path}."
    raise ValueError(msg)


def _write_archive(
    layers: dict[str, gpd.GeoDataFrame],
    output: Path,
    *,
    name: str,
    description: str,
) -> None:
    write_pmtiles(
        layers,
        output,
        min_zoom=0,
        max_zoom=0,
        name=name,
        description=description,
        on_overflow="ignore",
    )


def _summarize_archive(
    path: Path,
    layer_name: str,
    *,
    include_hole_counts: bool = False,
) -> dict[str, Any]:
    header, metadata, layers = read_pmtiles_archive(path)
    features = layers[layer_name]["features"]
    return {
        "header": summarize_header(header),
        "metadata": summarize_metadata(metadata),
        "semantic": summarize_features(
            features, include_hole_counts=include_hole_counts
        ),
    }


@pytest.mark.integration
def test_climate_fixture_preserves_semantics(tmp_path: Path) -> None:
    """The real-world ERA5 fixture keeps its provenance, schema, and holes."""
    geojson = CLIMATE_FIXTURE / "era5-1982-07-22-t2m-max-delta.geojson"
    provenance = _load_json(
        CLIMATE_FIXTURE / "era5-1982-07-22-t2m-max-delta.provenance.json"
    )
    expected = _load_json(CLIMATE_FIXTURE / "summary.json")

    assert _sha256(geojson) == provenance["geojson_sha256"]

    gdf = gpd.read_file(geojson)
    assert gdf.crs is not None
    assert gdf.crs.to_epsg() == 4326
    assert len(gdf) == provenance["output_feature_count"]
    assert Counter(gdf.geometry.geom_type) == Counter({"Polygon": 329})

    hole_counts = [len(geometry.interiors) for geometry in gdf.geometry]
    assert (
        sum(1 for count in hole_counts if count > 0)
        == provenance["polygons_with_holes"]
    )
    assert sum(hole_counts) == provenance["total_holes"]

    out = tmp_path / "era5-1982-07-22-t2m-max-delta.pmtiles"
    _write_archive(
        {"era5_t2m_max_delta": gdf},
        out,
        name="ERA5 t2m max delta",
        description="ERA5 daily anomalies clipped to Europe",
    )

    summary = _summarize_archive(out, "era5_t2m_max_delta")
    features = read_pmtiles_archive(out)[2]["era5_t2m_max_delta"]["features"]
    summary["semantic"] = {
        **summary["semantic"],
        "band_counts": summarize_band_counts(features),
    }

    assert summary == expected


@pytest.mark.integration
def test_polygon_winding_fixture_preserves_holes(tmp_path: Path) -> None:
    """The polygon winding fixture keeps the hole semantics intact."""
    fixture = TIPPECANOE_FIXTURE / "polygon-winding" / "in.json"
    expected = _load_json(TIPPECANOE_FIXTURE / "polygon-winding" / "summary.json")

    gdf = gpd.GeoDataFrame.from_features(
        _load_tippecanoe_features(fixture), crs="EPSG:4326"
    )
    out = tmp_path / "polygon-winding.pmtiles"
    _write_archive(
        {"polygon_winding": gdf},
        out,
        name="Polygon winding fixture",
        description="Tippecanoe polygon hole fixture",
    )

    summary = _summarize_archive(out, "polygon_winding", include_hole_counts=True)
    assert summary == expected


@pytest.mark.integration
def test_attribute_type_fixture_normalizes_values(tmp_path: Path) -> None:
    """The attribute-type fixture keeps mixed list-valued columns as strings."""
    fixture = TIPPECANOE_FIXTURE / "attribute-type" / "in.json"
    source_features = _load_tippecanoe_features(fixture)
    gdf = gpd.GeoDataFrame.from_features(source_features, crs="EPSG:4326")
    out = tmp_path / "attribute-type.pmtiles"
    _write_archive(
        {"attribute_type": gdf},
        out,
        name="Attribute type fixture",
        description="Tippecanoe property-type fixture",
    )

    header, metadata, layers = read_pmtiles_archive(out)
    summary = {
        "header": summarize_header(header),
        "metadata": summarize_metadata(metadata),
        "semantic": summarize_features(layers["attribute_type"]["features"]),
    }

    assert summary["metadata"]["vector_layers"] == [
        {
            "id": "attribute_type",
            "fields": ["booltype", "expect", "floattype", "inttype", "stringtype"],
        }
    ]

    features = layers["attribute_type"]["features"]
    assert len(features) == len(source_features)

    for field_name in ("booltype", "stringtype", "inttype", "floattype"):
        decoded_values = [
            feature["properties"].get(field_name)
            for feature in features
            if feature["properties"].get(field_name) is not None
        ]
        assert decoded_values
        assert all(isinstance(value, str) for value in decoded_values)
        assert "[2, 3]" in decoded_values


@pytest.mark.integration
def test_stable_fixture_preserves_feature_order(tmp_path: Path) -> None:
    """The stable fixture keeps the decoded feature order intact."""
    fixture = TIPPECANOE_FIXTURE / "stable" / "in.json"
    expected = _load_json(TIPPECANOE_FIXTURE / "stable" / "summary.json")

    gdf = gpd.GeoDataFrame.from_features(
        _load_tippecanoe_features(fixture), crs="EPSG:4326"
    )
    out = tmp_path / "stable.pmtiles"
    _write_archive(
        {"stable": gdf},
        out,
        name="Stable fixture",
        description="Tippecanoe feature-order fixture",
    )

    summary = _summarize_archive(out, "stable")
    summary["semantic"] = {
        **summary["semantic"],
        "feature_order": feature_property_sequence(
            read_pmtiles_archive(out)[2]["stable"]["features"],
            "order",
        ),
    }

    assert summary == expected
