"""Serialize a zero-feature PMTiles archive without GDAL's bounds inference."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any

from pmtiles.tile import (
    Compression,
    HeaderDict,
    TileType,
    serialize_directory,
    serialize_header,
)


@dataclass(frozen=True, slots=True)
class EmptyArchive:
    """An intentionally empty map with declared layers and zoom limits.

    Args:
        metadata: TileJSON metadata, including the declared vector layers.
        min_zoom: Minimum map zoom.
        max_zoom: Maximum map zoom.

    Examples:
        ``EmptyArchive({"vector_layers": []}, 0, 8).to_bytes()`` serializes a map.
    """

    metadata: dict[str, Any]
    min_zoom: int
    max_zoom: int

    def to_bytes(self) -> bytes:
        """Serialize an empty root directory and metadata with no tile payloads.

        Returns:
            A complete PMTiles v3 archive with zero addressed tiles. World bounds
            and a zero-degree center are used because no features define bounds.

        Examples:
            ``data = EmptyArchive(metadata, 0, 8).to_bytes()``.
        """
        root = gzip.compress(gzip.decompress(serialize_directory([])), mtime=0)
        metadata = gzip.compress(
            json.dumps(self.metadata, ensure_ascii=False).encode("utf-8"), mtime=0
        )
        metadata_offset = 127 + len(root)
        end_offset = metadata_offset + len(metadata)
        header: HeaderDict = {
            "version": 3,
            "root_offset": 127,
            "root_length": len(root),
            "metadata_offset": metadata_offset,
            "metadata_length": len(metadata),
            "leaf_directory_offset": end_offset,
            "leaf_directory_length": 0,
            "tile_data_offset": end_offset,
            "tile_data_length": 0,
            "addressed_tiles_count": 0,
            "tile_entries_count": 0,
            "tile_contents_count": 0,
            "clustered": True,
            "internal_compression": Compression.GZIP,
            "tile_compression": Compression.NONE,
            "tile_type": TileType.MVT,
            "min_zoom": self.min_zoom,
            "max_zoom": self.max_zoom,
            "min_lon_e7": -1800000000,
            "min_lat_e7": -850511288,
            "max_lon_e7": 1800000000,
            "max_lat_e7": 850511288,
            "center_zoom": self.min_zoom,
            "center_lon_e7": 0,
            "center_lat_e7": 0,
        }
        return bytes(serialize_header(header)) + root + metadata
