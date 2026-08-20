"""Custom exception types for geodataframe_to_pmtiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class WritePMTilesError(Exception):
    """Base class for all geodataframe_to_pmtiles errors."""


class EmptyLayerError(WritePMTilesError):
    """Raised when the layers mapping is empty or a layer has no features."""


class MissingCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame has no CRS set."""


class UnsupportedCRSError(WritePMTilesError):
    """Raised when a GeoDataFrame's CRS cannot be resolved or transformed.

    This replaces the former "only EPSG:4326 accepted" restriction.  It is
    now raised only when the CRS definition is unresolvable by the installed
    geospatial stack, or when the coordinate transformation to EPSG:4326
    fails at runtime.  The original cause is always chained via ``__cause__``.
    """


class CRSTransformError(WritePMTilesError):
    """Raised when a coordinate transformation to EPSG:4326 fails.

    The original exception from pyproj / GDAL is always chained as
    ``__cause__`` so callers can inspect the root error.
    """


class UnsupportedPropertyTypeError(WritePMTilesError):
    """Raised when a column has a value that cannot be encoded as an MVT property."""


@dataclass(frozen=True)
class TileLimitViolation:
    """A GDAL MVT tile-limit diagnostic captured while writing an archive.

    ``requested`` is the configured limit passed to GDAL.
    ``observed`` is the value parsed from GDAL's diagnostic text.
    """

    limit: Literal["MAX_FEATURES", "MAX_SIZE"]
    requested: int
    observed: int
    tile: tuple[int, int, int] | None


class TileOverflowError(WritePMTilesError):
    """Raised when GDAL reports a tile limit that can lose data or precision.

    The archive is discarded before the caller's destination is changed.  The
    ``violations`` attribute gives the limit, configured value, observed
    value, and tile coordinate reported by GDAL.
    """

    def __init__(self, violations: tuple[TileLimitViolation, ...]) -> None:
        self.violations = violations
        details = "; ".join(
            (
                f"{violation.limit}={violation.requested:,}, "
                f"observed={violation.observed:,}"
                + (
                    f" at {violation.tile[0]}/{violation.tile[1]}/{violation.tile[2]}"
                    if violation.tile is not None
                    else ""
                )
            )
            for violation in violations
        )
        super().__init__(
            "GDAL reported a PMTiles tile limit that can drop features or reduce "
            f"geometry precision ({details}). The destination was not changed. "
            "Reduce density, split the data, or explicitly use "
            "on_overflow='unsafe' only after accepting this risk."
        )
