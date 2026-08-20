"""Custom exception types for geodataframe_to_pmtiles."""

from __future__ import annotations


class WritePMTilesError(Exception):
    """Base class for all geodataframe_to_pmtiles errors."""


class EmptyLayerError(WritePMTilesError):
    """Raised when the layers mapping is empty or a layer has no features."""


class MissingCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame has no CRS set."""


class UnsupportedCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame's CRS is not EPSG:4326."""


class UnsupportedPropertyTypeError(WritePMTilesError):
    """Raised when a column has a value that cannot be encoded as an MVT property."""
