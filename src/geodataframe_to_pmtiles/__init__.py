"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL."""

from __future__ import annotations

from geodataframe_to_pmtiles._writer import write_pmtiles
from geodataframe_to_pmtiles.exceptions import (
    EmptyLayerError,
    MissingCRSError,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
    WritePMTilesError,
)

__all__ = [
    "EmptyLayerError",
    "MissingCRSError",
    "TileOverflowError",
    "UnsupportedCRSError",
    "UnsupportedPropertyTypeError",
    "WritePMTilesError",
    "write_pmtiles",
]
