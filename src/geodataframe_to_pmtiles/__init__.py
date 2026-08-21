"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from geodataframe_to_pmtiles._diagnostics import CheckReport, CheckResult, check
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

if TYPE_CHECKING:
    from geodataframe_to_pmtiles._writer import write as write

__all__ = [
    "CRSTransformError",
    "CheckReport",
    "CheckResult",
    "EmptyLayerError",
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
    if name != "write":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    write = import_module("geodataframe_to_pmtiles._writer").write
    globals()["write"] = write
    return write
