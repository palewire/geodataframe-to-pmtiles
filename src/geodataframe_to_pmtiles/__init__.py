"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from geodataframe_to_pmtiles._diagnostics import CheckReport, CheckResult, check
from geodataframe_to_pmtiles.exceptions import (
    CRSTransformError,
    EmptyLayerError,
    InvalidLayerZoomError,
    MissingCRSError,
    TileLimitViolation,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
    WritePMTilesError,
)

if TYPE_CHECKING:
    from geodataframe_to_pmtiles._writer import LayerZoomSpec
    from geodataframe_to_pmtiles._writer import write as write

__all__ = [
    "CRSTransformError",
    "CheckReport",
    "CheckResult",
    "EmptyLayerError",
    "InvalidLayerZoomError",
    "LayerZoomSpec",
    "MissingCRSError",
    "TileLimitViolation",
    "TileOverflowError",
    "UnsupportedCRSError",
    "UnsupportedPropertyTypeError",
    "WritePMTilesError",
    "check",
    "write",
]


def __getattr__(name: str) -> object:
    if name == "write":
        write = import_module("geodataframe_to_pmtiles._writer").write
        globals()["write"] = write
        return write
    if name == "LayerZoomSpec":
        _cls = import_module("geodataframe_to_pmtiles._writer").LayerZoomSpec
        globals()["LayerZoomSpec"] = _cls
        return _cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
