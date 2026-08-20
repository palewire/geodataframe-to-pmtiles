"""Helpers for PMTiles semantic conformance tests."""

from __future__ import annotations

import gzip
from collections import Counter
from pathlib import Path
from typing import Any

from mapbox_vector_tile import decode
from pmtiles.reader import MmapSource, Reader


def read_pmtiles_archive(
    path: Path,
    *,
    z: int = 0,
    x: int = 0,
    y: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return the PMTiles header, metadata, and decoded tile layers."""
    with path.open("r+b") as file:
        reader = Reader(MmapSource(file))
        header = reader.header()
        metadata = reader.metadata()
        tile = reader.get(z, x, y)

    if tile is None:
        msg = f"Tile {z}/{x}/{y} was not found in {path}."
        raise AssertionError(msg)

    if header["tile_compression"].name.lower() == "gzip":
        tile = gzip.decompress(tile)

    return header, metadata, decode(tile)


def read_pmtiles_bytes(
    data: bytes,
    *,
    z: int,
    x: int,
    y: int,
) -> dict[str, Any] | None:
    """Decode a single tile from in-memory PMTiles bytes.

    Returns the decoded layer dict, or ``None`` if the tile is absent.
    """
    from pmtiles.reader import MemorySource

    reader = Reader(MemorySource(data))
    header = reader.header()
    tile = reader.get(z, x, y)
    if tile is None:
        return None
    if header["tile_compression"].name.lower() == "gzip":
        tile = gzip.decompress(tile)
    return decode(tile)


def count_features_in_tile(
    data: bytes,
    layer_name: str,
    *,
    z: int,
    x: int,
    y: int,
) -> int:
    """Return the number of decoded features for *layer_name* in tile z/x/y."""
    layers = read_pmtiles_bytes(data, z=z, x=x, y=y)
    if layers is None:
        return 0
    return len(layers.get(layer_name, {}).get("features", []))


def count_features_across_zoom(
    data: bytes,
    layer_name: str,
    *,
    zoom: int,
) -> int:
    """Return the total decoded feature count for *layer_name* across all tiles at *zoom*."""
    from pmtiles.reader import MemorySource

    reader = Reader(MemorySource(data))
    header = reader.header()
    total = 0
    n_tiles = 2**zoom
    for x in range(n_tiles):
        for y in range(n_tiles):
            tile = reader.get(zoom, x, y)
            if tile is None:
                continue
            if header["tile_compression"].name.lower() == "gzip":
                tile = gzip.decompress(tile)
            layers = decode(tile)
            total += len(layers.get(layer_name, {}).get("features", []))
    return total


def summarize_header(header: dict[str, Any]) -> dict[str, Any]:
    """Normalize the PMTiles header for golden comparisons."""
    return {
        "minzoom": header["min_zoom"],
        "maxzoom": header["max_zoom"],
        "tile_type": header["tile_type"].name.lower(),
        "tile_compression": header["tile_compression"].name.lower(),
    }


def summarize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize the PMTiles metadata block for golden comparisons."""
    return {
        "name": metadata.get("name", ""),
        "description": metadata.get("description", ""),
        "vector_layers": [
            {
                "id": layer["id"],
                "fields": sorted(layer.get("fields", {})),
            }
            for layer in metadata.get("vector_layers", [])
        ],
    }


def count_geometry_holes(geometry: dict[str, Any]) -> int:
    """Return the number of interior rings in a decoded MVT geometry."""
    geometry_type = geometry["type"]
    if geometry_type == "Polygon":
        return max(0, len(geometry["coordinates"]) - 1)
    if geometry_type == "MultiPolygon":
        return sum(max(0, len(polygon) - 1) for polygon in geometry["coordinates"])
    return 0


def summarize_features(
    features: list[dict[str, Any]],
    *,
    include_hole_counts: bool = False,
) -> dict[str, Any]:
    """Summarize decoded features without depending on raw tile bytes."""
    geometry_types: Counter[str] = Counter()
    property_keys: set[str] = set()
    hole_counts: list[int] = []

    for feature in features:
        geometry = feature["geometry"]
        geometry_types[geometry["type"]] += 1
        property_keys.update(feature.get("properties", {}))
        hole_counts.append(count_geometry_holes(geometry))

    return {
        "feature_count": len(features),
        "geometry_types": dict(sorted(geometry_types.items())),
        "property_keys": sorted(property_keys),
        "features_with_holes": sum(1 for hole_count in hole_counts if hole_count > 0),
        "total_holes": sum(hole_counts),
        **({"hole_counts": hole_counts} if include_hole_counts else {}),
    }


def summarize_band_counts(
    features: list[dict[str, Any]],
    *,
    floor_key: str = "floor",
    ceil_key: str = "ceil",
) -> list[dict[str, Any]]:
    """Summarize band coverage as floor/ceil counts."""
    counts = Counter(
        (feature["properties"][floor_key], feature["properties"][ceil_key])
        for feature in features
    )
    return [
        {"floor": floor, "ceil": ceil, "feature_count": feature_count}
        for (floor, ceil), feature_count in sorted(counts.items())
    ]


def feature_property_sequence(
    features: list[dict[str, Any]],
    key: str,
) -> list[Any]:
    """Return a feature property sequence in decoded order."""
    return [feature["properties"][key] for feature in features]
