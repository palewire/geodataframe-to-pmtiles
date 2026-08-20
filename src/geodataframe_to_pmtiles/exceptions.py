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


class TileOverflowError(WritePMTilesError):
    """Raised when ``on_overflow='error'`` and tile-level data loss cannot be ruled out.

    The GDAL PMTiles driver silently drops features when a tile exceeds its
    ``MAX_SIZE`` or ``MAX_FEATURES`` per-tile limit.  This library sets both
    limits generously (derived from input feature counts) to minimise the risk,
    but the driver provides no post-write signal indicating that a drop
    occurred.  When ``on_overflow='error'``, this exception is raised before
    any data is written so callers can explicitly acknowledge the limitation by
    switching to ``on_overflow='warn'`` or ``on_overflow='ignore'``.
    """
