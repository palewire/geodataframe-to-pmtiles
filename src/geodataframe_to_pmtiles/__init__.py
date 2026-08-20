"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL."""

from __future__ import annotations

from geodataframe_to_pmtiles._writer import write
from geodataframe_to_pmtiles.exceptions import (
    CRSTransformError,
    EmptyLayerError,
    MissingCRSError,
    TileLimitViolation,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
    WritePMTilesError,
)

__all__ = [
    "CRSTransformError",
    "EmptyLayerError",
    "MissingCRSError",
    "TileLimitViolation",
    "TileOverflowError",
    "UnsupportedCRSError",
    "UnsupportedPropertyTypeError",
    "WritePMTilesError",
    "write",
]
