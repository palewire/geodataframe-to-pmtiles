"""Custom exception types for geodataframe_to_pmtiles."""

from __future__ import annotations


class WritePMTilesError(Exception):
    """Base class for all geodataframe_to_pmtiles errors."""


class EmptyLayerError(WritePMTilesError):
    """Raised when the layers mapping is empty, or all layers have zero features.

    Also raised when ``empty_layer_policy='error'`` (the default) and any
    individual layer has zero features.  Use ``empty_layer_policy='skip'``
    to omit empty layers instead; that mode reports which layers were omitted
    via :attr:`~geodataframe_to_pmtiles.WriteResult.skipped_layers` and a
    :class:`UserWarning`.
    """


class MissingCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame has no CRS set."""


class UnsupportedCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame's CRS is not EPSG:4326."""


class UnsupportedPropertyTypeError(WritePMTilesError):
    """Raised when a column has a value that cannot be encoded as an MVT property."""


class TileOverflowError(WritePMTilesError):
    """Raised when ``on_overflow='error'`` and tile-level data loss cannot be ruled out.

    The GDAL PMTiles driver silently drops features when a tile exceeds its
    fixed per-tile caps: ``MAX_FEATURES = 300,000`` and ``MAX_SIZE = 10 MB``
    (spike-validated: 200,001 z0 features preserved in 630,430 bytes).
    Setting ``MAX_FEATURES=0`` does *not* disable the limit — GDAL clamps it
    to its internal minimum.  The driver provides no post-write signal when a
    drop occurs.

    When ``on_overflow='error'``, this exception is raised before any data is
    written so callers can explicitly acknowledge the limitation by switching to
    ``on_overflow='warn'`` or ``on_overflow='ignore'``.
    """
