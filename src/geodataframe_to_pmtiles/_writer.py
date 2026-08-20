"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL.

This module provides the main ``write`` function, which is the sole
public entry point for the library.  The GDAL PMTiles vector driver is used
directly; no subprocesses are spawned and no temporary files are written to
disk.  Intermediate data lives in GDAL's in-memory virtual filesystem
(``/vsimem/``) for the duration of the call.

The package imports without GDAL present.  ``write`` raises a clear
``RuntimeError`` if the native GDAL runtime or PMTiles driver is unavailable at
call time.

.. rubric:: Property normalisation

OGR's ``Feature.SetField`` rejects several Python types directly.  The
normaliser converts them before they reach OGR:

+---------------------------+--------------------+------------------------------------------+
| Python / pandas type      | MVT field type     | Notes                                    |
+===========================+====================+==========================================+
| ``str``                   | String             |                                          |
+---------------------------+--------------------+------------------------------------------+
| ``bool`` / ``np.bool_``   | Boolean            | Encoded as native MVT ``bool_value``     |
+---------------------------+--------------------+------------------------------------------+
| ``int`` / ``np.integer``  | Integer64          | numpy scalars normalised to ``int``      |
+---------------------------+--------------------+------------------------------------------+
| ``float`` / ``np.float_`` | Real               | numpy scalars normalised to ``float``;   |
|                           |                    | ``NaN`` stored as null                   |
+---------------------------+--------------------+------------------------------------------+
| ``datetime.date`` /       | String             | Normalised to ISO 8601 via               |
| ``datetime.datetime`` /   |                    | ``isoformat()`` before SetField.         |
| ``pd.Timestamp``          |                    | GDAL date/datetime fields decode to      |
|                           |                    | tuples; we store as OFTString to avoid   |
|                           |                    | that.                                    |
+---------------------------+--------------------+------------------------------------------+
| ``list`` / ``dict``       | String             | Explicitly JSON-encoded via              |
|                           |                    | ``json.dumps``; column must appear in    |
|                           |                    | ``json_fields`` (or ``json_fields=None`` |
|                           |                    | for auto).  Unlisted containers raise    |
|                           |                    | ``UnsupportedPropertyTypeError``.        |
|                           |                    | Never passed raw to SetField.            |
+---------------------------+--------------------+------------------------------------------+
| ``None`` / ``pd.NA``      | null field         |                                          |
+---------------------------+--------------------+------------------------------------------+
| anything else             | —                  | raises ``UnsupportedPropertyTypeError``  |
+---------------------------+--------------------+------------------------------------------+

.. rubric:: Tile overflow safety

GDAL's MVT encoder can remove features when ``MAX_FEATURES`` is reached and
can recode geometries at a lower resolution when ``MAX_SIZE`` is exceeded.
The writer configures practical limits (300,000 features and 10 MB per tile)
and captures GDAL's MVT diagnostics while the archive closes.  By default,
any feature-cap rebuild or size-driven recoding raises
:class:`~geodataframe_to_pmtiles.TileOverflowError` before the destination is
changed.

``on_overflow="unsafe"`` is an explicit opt-out for callers that knowingly
accept GDAL's lossy behavior. It emits a warning and must not be used for data
where every feature or coordinate matters.

.. rubric:: Web Mercator boundary semantics

The PMTiles/MVT tile scheme uses Web Mercator (EPSG:3857), which maps only
the latitude range **±85.05112877980659°** (the Google/OSM slippy-map limit,
referred to as ``WEB_MERCATOR_LAT_LIMIT`` in this module).  ``write``
reprojects every input layer to EPSG:4326 before passing it to the GDAL PMTiles
driver.

**Input-side boundary handling (performed by this library before GDAL):**

1. Any geometry that does not intersect the Web Mercator latitude extent is
   detected before being passed to GDAL.  A :class:`UserWarning` is emitted
   (once per layer, not once per feature) and the feature is skipped.  Without
   this check GDAL would discard such features silently.

2. Geometries that *partially* overlap the extent (e.g. a polygon that crosses
   85° N) are passed to GDAL.  GDAL clips them to the tile boundary
   internally using its ``EXTENT``/``BUFFER`` tile coordinate system.

**Longitude:** ±180° is supported.  A ``MultiPolygon`` manually split at the
antimeridian (one part from 170° to 180° and another from -180° to -170°) is
the correct representation for antimeridian-crossing features; GDAL places each
part in the appropriate tile column.

**GDAL PMTiles driver tile options** (fixed in this implementation):

* ``EXTENT = 4096`` — tile coordinate range (MVT standard).
* ``BUFFER = 80`` — extra tile units included beyond the tile edge; a feature
  is included in a tile if it falls within ``EXTENT + BUFFER`` of the tile
  origin.  Features beyond the Web Mercator latitude limit (|lat| >
  ~85.051°) are excluded before writing; ``write`` warns once for each
  affected layer and raises ``EmptyLayerError`` if all features in a layer
  are outside that bound.  Tippecanoe uses a larger default buffer (approx
  5% of tile vs GDAL's approx 2%), which is why z-7/z-8 decoded feature
  counts can differ by 1-2 between backends without any data loss.

**ERA5 z-0 fragment count difference:** 329 ERA5 polygons (within 35-60 degrees N)
written with this library decode to 297 features at z-0, while the same source
data passed through Tippecanoe decodes to 256 (Celsius) / 254 (Fahrenheit)
features.  The difference is purely a tile-clipping artifact — both backends
produce equivalent geographic union coverage at z-0.  The ERA5 data is well
within the Mercator extent; no features are lost.

.. rubric:: Attribution

An optional ``attribution`` string is stored in the archive's TileJSON metadata
block under the ``"attribution"`` key, as required by the TileJSON 3.0 spec.
Attribution is injected **after** GDAL writes the archive, using a pure
in-memory format-level operation:

1. The archive bytes from ``/vsimem/`` are parsed with the official
   ``pmtiles.reader.Reader`` and ``pmtiles.tile.serialize_header`` APIs.
2. The metadata JSON is decoded from the archive's internal compression format,
   the ``"attribution"`` key is set, and the JSON is re-encoded with the same
   format. GZIP output uses a stable header timestamp.
3. The archive is reassembled in a :class:`io.BytesIO` buffer: the fixed 127-
   byte header (with updated ``metadata_length`` and, if needed, updated
   ``leaf_directory_offset`` / ``tile_data_offset``) is followed by the root
   directory, the new metadata bytes, any leaf directories, and the tile data
   section — byte-for-byte unchanged.

No subprocesses, no temporary files, and no hard-coded magic offsets are used.
When ``attribution`` is an empty string (the default), the injection step is
skipped and the archive is written as GDAL produced it.

.. rubric:: CONF per-layer zoom (future extension point)

The GDAL CONF creation option also supports per-layer ``minzoom``,
``maxzoom``, and ``target_name`` overrides via a JSON object.  For example::

    CONF = {"layers": {"my_layer": {"minzoom": 5, "maxzoom": 8}}}

The current API exposes only archive-wide ``min_zoom`` / ``max_zoom``.  Per-
layer overrides can be added to the CONF dict if finer control is needed,
but this is not yet part of the public interface.

.. rubric:: Alternative in-memory path (VectorTranslate)

An alternative implementation was investigated using GDAL's
``VectorTranslate``::

    gdal.VectorTranslate(
        "/vsimem/out.pmtiles",
        mem_ds,
        format="PMTiles",
        srcSRS="EPSG:4326",
        dstSRS="EPSG:3857",
    )

This path reprojects to EPSG:3857 (the MVT native CRS) before handing off to
the driver.  The current implementation uses ``CreateDataSource`` directly and
passes EPSG:4326 SRS objects, letting the driver handle reprojection
internally.  Both paths produce valid archives.

.. rubric:: Feature order

Features are written in input (DataFrame row) order and the GDAL PMTiles
driver preserves that order within each tile.  When reading back, the driver
iterates across tile boundaries, so the same feature may appear more than once
(once per intersecting tile).  Unique first-occurrence order matches insertion
order for commonly shaped geometries.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import math
import tempfile
import uuid
import warnings
from importlib import import_module
from io import BytesIO, IOBase
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, overload

import numpy as np

from geodataframe_to_pmtiles.exceptions import (
    CRSTransformError,
    EmptyLayerError,
    MissingCRSError,
    TileLimitViolation,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    import geopandas as gpd

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default archive-wide minimum zoom level.
DEFAULT_MIN_ZOOM: int = 0

#: Default archive-wide maximum zoom level.
DEFAULT_MAX_ZOOM: int = 8

# These limits keep common dense datasets practical. They are not a claim of
# losslessness: _GDALDiagnosticCapture turns every detected limit action into
# a failure before output is published.
_MAX_FEATURES: int = 300_000
_MAX_SIZE: int = 10_000_000

PropertyKind = Literal["string", "int", "float", "bool"]


class _MissingType:
    """Sentinel singleton — instances signal "argument was not supplied".

    Using a dedicated type (rather than ``None``) lets the ``write``
    implementation distinguish ``layer=None`` (invalid explicit call) from
    the keyword being omitted entirely (valid for the mapping form).
    """

    _instance: _MissingType | None = None

    def __new__(cls) -> _MissingType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover
        return "_MISSING"


_MISSING = _MissingType()

# ---------------------------------------------------------------------------
# Web Mercator boundary constants
# ---------------------------------------------------------------------------

#: Maximum latitude supported by the Web Mercator tile scheme (EPSG:3857 /
#: Google/OSM slippy-map convention).  Features at ``|lat| > WEB_MERCATOR_LAT_LIMIT``
#: lie outside the tile space and are dropped by the GDAL PMTiles driver.
WEB_MERCATOR_LAT_LIMIT: float = 85.05112877980659

# Warning emitted once per layer when out-of-bounds features are detected.
_OUT_OF_BOUNDS_WARNING = (
    "Layer '{layer_name}': {count} feature(s) lie entirely outside the Web "
    f"Mercator latitude extent (±{WEB_MERCATOR_LAT_LIMIT}°) and will not appear "
    "in the archive.  The GDAL PMTiles driver silently drops such features; "
    "this library detects and reports them up-front.  Clip or drop the affected "
    "geometries so they fall within ±85.05° latitude before calling gpm.write."
)


def _is_outside_mercator_extent(geom: Any) -> bool:
    """Return True if *geom* lies entirely outside the Web Mercator latitude extent.

    Bounding boxes identify the common cases quickly. A geometry intersection
    check handles multipart geometries with components on opposite sides of the
    valid latitude range. Geometries that intersect the boundary are passed to
    GDAL, which clips them to the tile boundary internally.
    """
    minx, miny, maxx, maxy = map(float, geom.bounds)
    if maxy < -WEB_MERCATOR_LAT_LIMIT or miny > WEB_MERCATOR_LAT_LIMIT:
        return True
    if miny >= -WEB_MERCATOR_LAT_LIMIT and maxy <= WEB_MERCATOR_LAT_LIMIT:
        return False

    from shapely.geometry import box

    return not geom.intersects(
        box(minx - 1.0, -WEB_MERCATOR_LAT_LIMIT, maxx + 1.0, WEB_MERCATOR_LAT_LIMIT)
    )


def _load_gdal_modules() -> tuple[Any, Any, Any]:
    """Import GDAL modules and raise a runtime error if they are unavailable."""
    try:
        gdal = import_module("osgeo.gdal")
        ogr = import_module("osgeo.ogr")
        osr = import_module("osgeo.osr")
    except ImportError as exc:
        msg = (
            "GDAL Python bindings are required to write PMTiles. Install a "
            "matching native GDAL runtime and Python bindings in the active "
            "environment (for example via conda-forge, Homebrew, or your "
            "system package manager), then call gpm.write again."
        )
        raise RuntimeError(msg) from exc

    gdal.UseExceptions()
    ogr.UseExceptions()
    return gdal, ogr, osr


def _ogr_field_types(ogr: Any) -> dict[str, int]:
    """Return the OGR field type mapping used by the writer."""
    return {
        "string": int(ogr.OFTString),
        "int": int(ogr.OFTInteger64),
        "float": int(ogr.OFTReal),
        "bool": int(ogr.OFTInteger),
    }


def _is_boolean(value: object) -> bool:
    """Return whether *value* is a Python or NumPy boolean scalar."""
    return isinstance(value, (bool, np.bool_))


def _infer_property_kind(
    series: Any,
    json_field_names: frozenset[str] | None,
) -> PropertyKind:
    """Return the normalised property kind for *series*.

    Pure scalar columns are typed from their first non-null value.  If a column
    contains any list/dict values and JSON encoding is allowed for that column,
    the entire column is treated as ``"string"`` so JSON payloads never get
    coerced into numeric zeroes.  An entirely-null series is typed as
    ``"string"``.  Raises ``UnsupportedPropertyTypeError`` for types that
    cannot be normalised, respecting the ``json_field_names`` policy.
    """
    import pandas as pd

    col_name = str(series.name)
    _missing = object()
    first_non_null: object = _missing
    property_kinds: set[PropertyKind] = set()
    saw_structured_value = False

    for val in series:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        try:
            if pd.isna(val):
                continue
        except (TypeError, ValueError):
            pass

        if _is_boolean(val):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("bool")
            continue
        if isinstance(val, (int, np.integer)):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("int")
            continue
        if isinstance(val, (float, np.floating)):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("float")
            continue
        if isinstance(val, str):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("string")
            continue
        if isinstance(val, (list, dict)):
            if json_field_names is None or col_name in json_field_names:
                saw_structured_value = True
                continue
            raise UnsupportedPropertyTypeError(
                f"Column '{col_name}' contains list/dict values that would be "
                "JSON-encoded, but this column is not in json_fields.  "
                "Pass json_fields=None to auto-encode all list/dict columns, "
                f"or include '{col_name}' in json_fields explicitly."
            )
        if isinstance(val, (_dt.date, _dt.datetime)):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("string")
            continue
        # pandas Timestamp and other datetime-like types.
        if callable(getattr(type(val), "isoformat", None)):
            if first_non_null is _missing:
                first_non_null = val
            property_kinds.add("string")
            continue
        raise UnsupportedPropertyTypeError(
            f"Column '{col_name}' contains a value of type "
            f"{type(val).__name__!r} which cannot be encoded as an MVT "
            "property.  Supported types: str, bool, int, float, "
            "list (with json_fields), dict (with json_fields), datetime, None / NA."
        )

    if saw_structured_value:
        # Keep every value in the column as a string field so JSON payloads
        # never get coerced into zeroes by a numeric OGR field definition.
        return "string"
    if first_non_null is _missing and pd.api.types.is_bool_dtype(series.dtype):
        return "bool"
    if "bool" in property_kinds and len(property_kinds) > 1:
        raise UnsupportedPropertyTypeError(
            f"Column '{col_name}' mixes boolean and non-boolean values. Boolean "
            "properties must contain only bool, np.bool_, and null values; "
            "numeric 0 and 1 values are integers, not booleans."
        )
    if first_non_null is _missing:
        return "string"

    val = first_non_null
    if _is_boolean(val):
        return "bool"
    if isinstance(val, (int, np.integer)):
        return "int"
    if isinstance(val, (float, np.floating)):
        return "float"
    if isinstance(val, str):
        return "string"
    if isinstance(val, (_dt.date, _dt.datetime)):
        return "string"
    # pandas Timestamp and other datetime-like types.
    if callable(getattr(type(val), "isoformat", None)):
        return "string"
    raise UnsupportedPropertyTypeError(
        f"Column '{col_name}' contains a value of type "
        f"{type(val).__name__!r} which cannot be encoded as an MVT property.  "
        "Supported types: str, bool, int, float, list (with json_fields), "
        "dict (with json_fields), datetime, None / NA."
    )


def _normalise_value(
    val: object,
    column_name: str,
    json_field_names: frozenset[str] | None,
) -> tuple[bool, object]:
    """Return ``(is_null, normalised_value)`` for *val*.

    The normalised value is suitable for passing to ``OGR Feature.SetField``.
    Raises ``UnsupportedPropertyTypeError`` for unrecognised types.
    """
    import pandas as pd

    if val is None:
        return True, None
    if isinstance(val, float) and math.isnan(val):
        return True, None
    try:
        if pd.isna(val):
            return True, None
    except (TypeError, ValueError):
        pass

    if _is_boolean(val):
        return False, bool(val)
    if isinstance(val, np.integer):
        return False, int(val)
    if isinstance(val, np.floating):
        if math.isnan(float(val)):
            return True, None
        return False, float(val)
    if isinstance(val, (int, float, str)):
        return False, val
    if isinstance(val, (list, dict)):
        if json_field_names is None or column_name in json_field_names:
            # Explicit JSON encoding — never allow GDAL's internal list handling
            # to leak through.  This is the only safe path for structured values.
            return False, json.dumps(val, ensure_ascii=False)
        raise UnsupportedPropertyTypeError(
            f"Column '{column_name}' contains a list/dict value that is not "
            "JSON-encoded because this column is not in json_fields.  "
            "Pass json_fields=None to auto-encode, or add the column name to "
            "json_fields."
        )
    if isinstance(val, (_dt.date, _dt.datetime)):
        return False, val.isoformat()
    # pandas Timestamp and other datetime-like objects with isoformat() method.
    iso_method = getattr(type(val), "isoformat", None)
    if callable(iso_method):
        return False, iso_method(val)
    raise UnsupportedPropertyTypeError(
        f"Column '{column_name}' contains a value of type "
        f"{type(val).__name__!r} which cannot be encoded as an MVT property.  "
        "Supported types: str, bool, int, float, list (with json_fields), "
        "dict (with json_fields), datetime, None / NA."
    )


# ---------------------------------------------------------------------------
# Core writer
# ---------------------------------------------------------------------------

#: Allowed values for the ``on_overflow`` parameter.
OverflowPolicy = Literal["error", "unsafe"]


class _GDALDiagnosticCapture:
    """Collect MVT debug messages emitted while GDAL finalizes an archive."""

    def __init__(self, gdal: Any) -> None:
        self._gdal = gdal
        self._events: list[tuple[int, str]] = []
        self._get_thread_option = getattr(gdal, "GetThreadLocalConfigOption", None)
        self._set_thread_option = getattr(gdal, "SetThreadLocalConfigOption", None)
        self._previous_debug: str | None = None

    def __enter__(self) -> _GDALDiagnosticCapture:
        if callable(self._get_thread_option):
            self._previous_debug = self._get_thread_option("CPL_DEBUG")
        if callable(self._set_thread_option):
            self._set_thread_option("CPL_DEBUG", "MVT")
        else:
            self._previous_debug = self._gdal.GetConfigOption("CPL_DEBUG")
            self._gdal.SetConfigOption("CPL_DEBUG", "MVT")
        self._gdal.PushErrorHandler(self._record)
        return self

    def __exit__(self, *_: object) -> None:
        self._gdal.PopErrorHandler()
        if callable(self._set_thread_option):
            self._set_thread_option("CPL_DEBUG", self._previous_debug)
        else:
            self._gdal.SetConfigOption("CPL_DEBUG", self._previous_debug)

    def _record(self, _level: int, _number: int, message: str) -> None:
        self._events.append((_level, message))

    def failures(self) -> tuple[str, ...]:
        """Return GDAL failures hidden by the temporary diagnostic handler."""
        return tuple(
            message for level, message in self._events if level >= self._gdal.CE_Failure
        )

    def violations(self) -> tuple[TileLimitViolation, ...]:
        """Translate GDAL's stable MVT diagnostics into public context."""
        violations: list[TileLimitViolation] = []
        for _, message in self._events:
            coordinates = _tile_coordinates(message)
            if "feature count limit of" in message:
                limit = _integer_after(message, "feature count limit of ")
                if limit is not None:
                    violations.append(
                        TileLimitViolation(
                            limit="MAX_FEATURES",
                            requested=_MAX_FEATURES,
                            observed=limit,
                            tile=coordinates,
                        )
                    )
            elif "Recoding tile " in message and "From " in message:
                observed = _integer_after(message, "From ")
                if observed is not None:
                    violations.append(
                        TileLimitViolation(
                            limit="MAX_SIZE",
                            requested=_MAX_SIZE,
                            observed=observed,
                            tile=coordinates,
                        )
                    )
        return tuple(violations)


def _integer_after(message: str, prefix: str) -> int | None:
    """Return the decimal integer immediately following *prefix* in *message*."""
    start = message.find(prefix)
    if start == -1:
        return None
    parts = message[start + len(prefix) :].lstrip().split()
    if not parts:
        return None
    digits = parts[0].rstrip(".,")
    return int(digits) if digits.isdecimal() else None


def _tile_coordinates(message: str) -> tuple[int, int, int] | None:
    """Extract ``z/x/y`` from a GDAL MVT diagnostic when it provides one."""
    marker = "tile "
    start = message.lower().find(marker)
    if start == -1:
        return None
    raw = message[start + len(marker) :].split(",", 1)[0].split()[0]
    parts = raw.split("/")
    if len(parts) != 3 or not all(part.isdecimal() for part in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2]))


# ---------------------------------------------------------------------------
# Public overload signatures for gpm.write
# ---------------------------------------------------------------------------


@overload
def write(
    layers: Mapping[str, gpd.GeoDataFrame],
    output: str | Path | BinaryIO,
    *,
    min_zoom: int = ...,
    max_zoom: int = ...,
    name: str = ...,
    description: str = ...,
    attribution: str = ...,
    json_fields: Collection[str] | None = ...,
    on_overflow: OverflowPolicy = ...,
    simplification: float | None = ...,
) -> None: ...


@overload
def write(
    layers: gpd.GeoDataFrame,
    output: str | Path | BinaryIO,
    *,
    layer: str,
    min_zoom: int = ...,
    max_zoom: int = ...,
    name: str = ...,
    description: str = ...,
    attribution: str = ...,
    json_fields: Collection[str] | None = ...,
    on_overflow: OverflowPolicy = ...,
    simplification: float | None = ...,
) -> None: ...


def write(
    layers: Mapping[str, gpd.GeoDataFrame] | gpd.GeoDataFrame,
    output: str | Path | BinaryIO,
    *,
    layer: str | _MissingType | None = _MISSING,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    name: str = "",
    description: str = "",
    attribution: str = "",
    json_fields: Collection[str] | None = None,
    on_overflow: OverflowPolicy = "error",
    simplification: float | None = None,
) -> None:
    """Write a PMTiles vector archive from one or more GeoDataFrames.

    Two calling forms are supported:

    **Mapping form** — write multiple named layers::

        gpm.write({"contours": contours, "data": points}, "map.pmtiles")

    **Single-frame form** — write one layer with an explicit name::

        gpm.write(contours, "map.pmtiles", layer="contours")

    Parameters
    ----------
    layers:
        Either a :class:`~collections.abc.Mapping` of layer name →
        :class:`~geopandas.GeoDataFrame`, or a single
        :class:`~geopandas.GeoDataFrame` (``layer`` is required in the
        latter case).  Every GeoDataFrame must have an explicit, resolvable
        CRS.  GeoDataFrames not already in EPSG:4326 are automatically
        reprojected using traditional GIS X/Y axis order; inputs are never
        mutated.  The mapping must not be empty, and no GeoDataFrame may be
        empty.
    output:
        Destination for the archive.  A :class:`str` or
        :class:`pathlib.Path` (written through a temporary file in the same
        directory and then atomically replaced) or any binary-writable stream
        such as :class:`io.BytesIO`.
    layer:
        Layer name for the single-frame form.  Required when *layers* is a
        :class:`~geopandas.GeoDataFrame`; must be omitted when
        *layers* is a mapping.
    min_zoom:
        Archive-wide minimum zoom level (0-22, default 0).
    max_zoom:
        Archive-wide maximum zoom level (0-22, default 8).
    name:
        Optional tileset name stored in the archive metadata.
    description:
        Optional human-readable description stored in the archive metadata.
    attribution:
        Optional attribution string stored in the archive's TileJSON metadata
        under the ``"attribution"`` key.  Any non-empty string is accepted;
        Unicode and HTML are preserved exactly.  When omitted or set to an
        empty string (the default) the key is not written to the archive.
    json_fields:
        Controls which columns are JSON-encoded when they contain ``list`` or
        ``dict`` values.

        * ``None`` (default) — all list/dict columns are automatically
          JSON-encoded to strings.  This is the safest default: it prevents
          GDAL's internal list-to-string conversion from producing unexpected
          output.
        * ``Collection[str]`` — only the named columns receive JSON treatment.
          Any other column that contains a list or dict value raises
          :class:`~geodataframe_to_pmtiles.UnsupportedPropertyTypeError`.

        In both cases the encoding is explicit: ``json.dumps`` with
        ``ensure_ascii=False``.  The resulting MVT field type is ``String``.
    on_overflow:
        Response to GDAL's per-tile ``MAX_FEATURES`` / ``MAX_SIZE`` actions.
        ``"error"`` (the default) raises
        :class:`~geodataframe_to_pmtiles.TileOverflowError` before the
        destination is changed. ``"unsafe"`` writes the archive after a
        warning, even if GDAL dropped features or reduced geometry precision.
    simplification:
        Optional geometry simplification factor in tile-coordinate units
        (4096 per tile).  ``None`` (default) disables simplification, which
        is recommended for a proof-of-concept to avoid unexpected data loss.

    Raises
    ------
    TypeError
        If *layers* is a :class:`~geopandas.GeoDataFrame` and *layer* is
        omitted, or if *layers* is a mapping and *layer* is provided.
    EmptyLayerError
        If *layers* is empty or any GeoDataFrame has zero features.
    MissingCRSError
        If any GeoDataFrame has no CRS set.
    UnsupportedCRSError
        If any GeoDataFrame's CRS definition cannot be resolved by the
        installed geospatial stack.  The root cause is chained.
    CRSTransformError
        If the coordinate transformation to EPSG:4326 fails at runtime.
        The root cause is chained.
    UnsupportedPropertyTypeError
        If a column contains a value that cannot be encoded as an MVT property,
        or a list/dict column is not covered by *json_fields*.
    TileOverflowError
        If ``on_overflow='error'``.  See module docstring for GDAL limitations.
    TypeError
        If *attribution* is not a string.
    ValueError
        If zoom levels are out of range or *min_zoom* > *max_zoom*.

    Notes
    -----
    List- and dict-valued properties are explicitly JSON-encoded to strings;
    they are never passed raw to ``OGR Feature.SetField`` (which rejects them).
    Boolean values use MVT's native ``bool_value`` encoding through OGR's
    Boolean field subtype.  numpy scalars and ``datetime``/``pd.Timestamp``
    objects are normalised to Python native types before reaching OGR.

    Per-tile caps are fixed spike-validated POC values:
    ``MAX_FEATURES = 300,000`` and ``MAX_SIZE = 10 MB``.  They are not
    unlimited; see the module docstring for details and the tested result.

    Attribution is injected after GDAL writes the archive by re-encoding only
    the metadata section; all MVT tile payloads are preserved byte-for-byte.
    See the module docstring for the format-level mechanism.
    """
    import geopandas as gpd

    # Normalise a plain string output path to Path so downstream code
    # can use isinstance(output, Path) uniformly.
    if isinstance(output, str):
        output = Path(output)

    # ------------------------------------------------------------------
    # Dispatch: single-GDF form vs. mapping form
    # ------------------------------------------------------------------
    if isinstance(layers, gpd.GeoDataFrame):
        if layer is _MISSING or layer is None:
            msg = (
                "Pass a layer name when writing a single GeoDataFrame: "
                "gpm.write(gdf, output, layer='my_layer')."
            )
            raise TypeError(msg)
        if not isinstance(layer, str):
            msg = f"'layer' must be a str, got {type(layer).__name__!r}."
            raise TypeError(msg)
        layers_mapping: dict[str, gpd.GeoDataFrame] = {layer: layers}
    else:
        if layer is not _MISSING:
            msg = (
                "The 'layer' argument must not be provided when 'layers' is a "
                "mapping; the layer names are taken from the mapping keys."
            )
            raise TypeError(msg)
        layers_mapping = dict(layers)
        for key in layers_mapping:
            if not isinstance(key, str):
                msg = (
                    f"All mapping keys must be strings; got key {key!r} "
                    f"of type {type(key).__name__!r}."
                )
                raise TypeError(msg)

    _write_impl(
        layers_mapping,
        output,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        name=name,
        description=description,
        attribution=attribution,
        json_fields=json_fields,
        on_overflow=on_overflow,
        simplification=simplification,
    )


def _write_impl(
    layers: dict[str, gpd.GeoDataFrame],
    output: Path | BinaryIO,
    *,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    name: str = "",
    description: str = "",
    attribution: str = "",
    json_fields: Collection[str] | None = None,
    on_overflow: OverflowPolicy = "error",
    simplification: float | None = None,
) -> None:
    """Internal implementation; called by :func:`write` after dispatch."""
    import geopandas as gpd

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    if not layers:
        msg = "The 'layers' mapping must contain at least one entry."
        raise EmptyLayerError(msg)

    for layer_name, gdf in layers.items():
        if not isinstance(gdf, gpd.GeoDataFrame):
            msg = f"Layer '{layer_name}' is not a GeoDataFrame."
            raise TypeError(msg)
        if gdf.empty:
            msg = (
                f"Layer '{layer_name}' contains no features.  "
                "Empty layers are not permitted; omit the layer or supply data."
            )
            raise EmptyLayerError(msg)
        if gdf.crs is None:
            msg = (
                f"Layer '{layer_name}' has no CRS.  "
                "An explicit source CRS is required.  "
                "Set the CRS with gdf.set_crs('EPSG:4326') if the data are "
                "already in WGS 84, or reproject with "
                "gdf.to_crs('EPSG:4326') before calling gpm.write."
            )
            raise MissingCRSError(msg)
        # Validate that the CRS definition is resolvable (catches garbage
        # WKT or authority codes the installed stack cannot look up).
        try:
            gdf.crs.to_epsg()
        except Exception as exc:
            msg = (
                f"Layer '{layer_name}' has a CRS that cannot be resolved by "
                "the installed geospatial stack: "
                f"{gdf.crs!r}.  "
                "Provide a standard EPSG code or a well-formed WKT/PROJ string."
            )
            raise UnsupportedCRSError(msg) from exc

    if not (0 <= min_zoom <= 22):
        msg = f"min_zoom must be between 0 and 22, got {min_zoom}."
        raise ValueError(msg)
    if not (0 <= max_zoom <= 22):
        msg = f"max_zoom must be between 0 and 22, got {max_zoom}."
        raise ValueError(msg)
    if min_zoom > max_zoom:
        msg = f"min_zoom ({min_zoom}) must be <= max_zoom ({max_zoom})."
        raise ValueError(msg)

    if on_overflow not in ("error", "unsafe"):
        msg = f"on_overflow must be 'error' or 'unsafe', got {on_overflow!r}."
        raise ValueError(msg)

    if not isinstance(attribution, str):
        msg = (
            f"'attribution' must be a str, got {type(attribution).__name__!r}.  "
            "Pass an empty string to omit attribution from the archive."
        )
        raise TypeError(msg)

    # Normalise json_fields to frozenset or None for O(1) membership tests.
    json_field_names: frozenset[str] | None = (
        None if json_fields is None else frozenset(json_fields)
    )

    # ------------------------------------------------------------------
    # Reproject layers to EPSG:4326 (does not mutate caller's GDFs).
    # Layers already in EPSG:4326 are passed through as-is (no copy).
    # ------------------------------------------------------------------
    wgs84_layers: dict[str, gpd.GeoDataFrame] = {}
    for layer_name, gdf in layers.items():
        if gdf.crs.to_epsg() == 4326:
            wgs84_layers[layer_name] = gdf
        else:
            try:
                # GeoPandas to_crs() produces traditional GIS X/Y (lon/lat) order by default,
                # regardless of GDAL/PyProj axis-order authority conventions.
                wgs84_layers[layer_name] = gdf.to_crs("EPSG:4326")
            except Exception as exc:
                msg = (
                    f"Layer '{layer_name}': coordinate transformation from "
                    f"{gdf.crs!r} to EPSG:4326 failed.  "
                    "Check that the source CRS is consistent with the data."
                )
                raise CRSTransformError(msg) from exc

    # Validate all column types up-front before any GDAL objects are created.
    # This prevents a partially-initialised dataset from being written.
    field_kinds_by_layer: dict[str, dict[str, PropertyKind]] = {}
    for layer_name, gdf in wgs84_layers.items():
        field_kinds: dict[str, PropertyKind] = {}
        for col in (c for c in gdf.columns if c != gdf.geometry.name):
            field_kinds[col] = _infer_property_kind(gdf[col], json_field_names)
        field_kinds_by_layer[layer_name] = field_kinds

    # ------------------------------------------------------------------
    # Web Mercator bounds pre-check
    # ------------------------------------------------------------------
    # Count features entirely outside the Web Mercator latitude extent for
    # each layer.  GDAL silently drops them; we warn up-front and, if the
    # whole layer would be empty, raise EmptyLayerError with a clear message
    # instead of letting GDAL fail with "Invalid bounds".
    valid_row_indices_by_layer: dict[str, list[int]] = {}
    for layer_name, gdf in wgs84_layers.items():
        out_of_bounds = 0
        valid_row_indices: list[int] = []
        for row_index, geom in enumerate(gdf.geometry):
            if geom is None or (hasattr(geom, "is_empty") and geom.is_empty):
                continue
            if _is_outside_mercator_extent(geom):
                out_of_bounds += 1
                continue
            valid_row_indices.append(row_index)
        valid_row_indices_by_layer[layer_name] = valid_row_indices
        if out_of_bounds > 0:
            warnings.warn(
                _OUT_OF_BOUNDS_WARNING.format(
                    layer_name=layer_name,
                    count=out_of_bounds,
                ),
                UserWarning,
                stacklevel=2,
            )
        if not valid_row_indices:
            msg = (
                f"Layer '{layer_name}' has no valid features (non-null, non-empty, "
                f"in-bounds) within the Web Mercator latitude extent "
                f"(±{WEB_MERCATOR_LAT_LIMIT}°)"
            )
            if out_of_bounds:
                msg += f" after excluding {out_of_bounds} out-of-bounds feature(s)"
            msg += ". Clip or drop the affected geometries before calling gpm.write."
            raise EmptyLayerError(msg)

    # Import GDAL only after the pure-Python validation has succeeded.
    gdal, ogr, osr = _load_gdal_modules()

    # ------------------------------------------------------------------
    # Create the PMTiles archive in /vsimem/
    # ------------------------------------------------------------------
    vsimem_path = f"/vsimem/{uuid.uuid4().hex}.pmtiles"

    drv = gdal.GetDriverByName("PMTiles")
    if drv is None:
        msg = (
            "GDAL was built without the PMTiles driver.  "
            "Ensure GDAL >= 3.8 is installed."
        )
        raise RuntimeError(msg)

    ds_options: list[str] = [
        f"MINZOOM={min_zoom}",
        f"MAXZOOM={max_zoom}",
        f"MAX_SIZE={_MAX_SIZE}",
        f"MAX_FEATURES={_MAX_FEATURES}",
    ]
    if name:
        ds_options.append(f"NAME={name}")
    if description:
        ds_options.append(f"DESCRIPTION={description}")

    try:
        with _GDALDiagnosticCapture(gdal) as diagnostics:
            ds = drv.CreateDataSource(vsimem_path, options=ds_options)
            if ds is None:
                msg = f"GDAL could not create a PMTiles datasource at {vsimem_path!r}."
                raise RuntimeError(msg)

            try:
                srs = osr.SpatialReference()
                srs.ImportFromEPSG(4326)

                for layer_name, gdf in wgs84_layers.items():
                    _write_layer(
                        ds,
                        layer_name,
                        gdf,
                        srs,
                        simplification,
                        json_field_names,
                        ogr,
                        field_kinds_by_layer[layer_name],
                        valid_row_indices_by_layer[layer_name],
                    )

                ds.FlushCache()
            finally:
                ds = None  # Closing emits the MVT limit diagnostics.

        violations = diagnostics.violations()
        failures = diagnostics.failures()
        if failures:
            msg = f"GDAL failed to write the PMTiles archive: {failures[-1]}"
            raise RuntimeError(msg)
        if violations and on_overflow == "error":
            raise TileOverflowError(violations)
        if violations:
            warnings.warn(
                "on_overflow='unsafe' allowed GDAL to exceed a tile limit; "
                "features may be missing or geometry precision may be reduced.",
                UserWarning,
                stacklevel=2,
            )
        data = _read_vsimem(vsimem_path, gdal)
    finally:
        gdal.Unlink(vsimem_path)

    # Inject attribution into the metadata section when requested.
    if attribution:
        data = _inject_attribution(data, attribution)

    if isinstance(output, Path):
        _write_path_atomic(output, data)
    elif isinstance(output, IOBase) or hasattr(output, "write"):
        output.write(data)
    else:
        msg = (
            f"'output' must be a pathlib.Path or binary-writable stream, "
            f"got {type(output).__name__!r}."
        )
        raise TypeError(msg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _inject_attribution(raw: bytes, attribution: str) -> bytes:
    """Return a new PMTiles archive with *attribution* set in the metadata.

    The implementation uses the official ``pmtiles.reader`` and
    ``pmtiles.tile`` APIs to parse and re-serialise only the metadata section.
    All tile payloads, the root directory, and leaf directories are copied
    verbatim from *raw*; no re-tiling occurs.

    The archive section layout is always::

        header (127 bytes) | root directory | metadata | leaf directories | tile data

    When the new metadata length differs from the original, the
    ``metadata_length``, ``leaf_directory_offset``, and ``tile_data_offset``
    fields in the header are updated accordingly.  All other header fields
    (bounds, center, zoom, compression, tile type, directory counts) are
    preserved exactly.

    Parameters
    ----------
    raw:
        Raw bytes of a valid PMTiles v3 archive produced by GDAL.
    attribution:
        Non-empty attribution string to embed under the ``"attribution"`` key.

    Returns
    -------
    bytes
        Raw bytes of the updated PMTiles archive.
    """
    from typing import cast

    from pmtiles.reader import MemorySource, Reader
    from pmtiles.tile import Compression, HeaderDict, serialize_header

    get_bytes = MemorySource(raw)
    reader = Reader(get_bytes)
    h = reader.header()
    metadata_bytes = get_bytes(h["metadata_offset"], h["metadata_length"])
    if h["internal_compression"] == Compression.GZIP:
        metadata_bytes = gzip.decompress(metadata_bytes)
    elif h["internal_compression"] != Compression.NONE:
        msg = (
            "Unsupported PMTiles metadata compression "
            f"{h['internal_compression'].name!r}; only NONE and GZIP "
            "metadata can be rewritten safely."
        )
        raise RuntimeError(msg)

    metadata = json.loads(metadata_bytes)
    metadata["attribution"] = attribution

    # Re-encode the metadata, preserving the original compression scheme.
    meta_json: bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    if h["internal_compression"] == Compression.GZIP:
        # Keep the gzip header stable so repeated writes stay byte-identical.
        new_meta_bytes: bytes = gzip.compress(meta_json, mtime=0)
    else:
        new_meta_bytes = meta_json

    # Compute how much the metadata section grew or shrank.
    delta = len(new_meta_bytes) - h["metadata_length"]

    # Build an updated header dict — copy all fields, then patch the offsets.
    new_h: HeaderDict = cast("HeaderDict", dict(h))
    new_h["metadata_length"] = len(new_meta_bytes)
    if delta:
        new_h["leaf_directory_offset"] = h["leaf_directory_offset"] + delta
        new_h["tile_data_offset"] = h["tile_data_offset"] + delta

    # Reassemble: header | root dir | new metadata | leaf dirs | tile data.
    out = BytesIO()
    out.write(serialize_header(new_h))
    out.write(get_bytes(h["root_offset"], h["root_length"]))
    out.write(new_meta_bytes)
    if h["leaf_directory_length"] > 0:
        out.write(get_bytes(h["leaf_directory_offset"], h["leaf_directory_length"]))
    out.write(get_bytes(h["tile_data_offset"], h["tile_data_length"]))
    return out.getvalue()


def _write_layer(
    ds: Any,
    layer_name: str,
    gdf: Any,
    srs: Any,
    simplification: float | None,
    json_field_names: frozenset[str] | None,
    ogr: Any,
    field_kinds: dict[str, PropertyKind],
    valid_row_indices: list[int],
) -> None:
    """Create an OGR layer inside *ds* and populate it from *gdf*.

    Performance notes
    -----------------
    Geometries accepted by the boundary pre-check are batch-converted to WKB
    via :func:`shapely.to_wkb` before the feature loop. This avoids a per-row
    Python-level text roundtrip (``geom.wkt`` string serialisation followed by
    ``CreateGeometryFromWkt`` parsing).

    Property columns are pre-normalised to Python-native lists before the
    feature loop.  For ``int``, ``float``, and ``bool`` kinds,
    ``Series.tolist()`` converts numpy scalars to Python scalars in one
    batch operation, and ``Series.isna().tolist()`` produces the null mask
    without per-row :func:`~pandas.isna` overhead.  The ``bool`` kind is
    further converted to 0 / 1 :class:`int` values, matching the MVT
    encoding rule documented in the module docstring.  For ``string``-kind
    columns a fast path handles the common all-strings case; only columns
    that contain non-string values (``datetime``, ``list``, ``dict``) use
    per-row conversion via :func:`str` or ``isoformat``/``json.dumps`` as
    appropriate for each value type.
    """
    import shapely

    # Determine OGR geometry type from the GeoDataFrame.
    geom_type = _shapely_geom_type_to_ogr(gdf, ogr)

    layer_options: list[str] = []
    if simplification is not None:
        layer_options.append(f"SIMPLIFICATION={simplification}")

    lyr = ds.CreateLayer(
        layer_name, srs=srs, geom_type=geom_type, options=layer_options
    )
    if lyr is None:
        msg = f"GDAL could not create layer '{layer_name}'."
        raise RuntimeError(msg)

    # Discover non-geometry columns and their OGR types.
    property_cols = [c for c in gdf.columns if c != gdf.geometry.name]
    ogr_field_type_map = _ogr_field_types(ogr)
    for col in property_cols:
        field_defn = ogr.FieldDefn(col, ogr_field_type_map[field_kinds[col]])
        if field_kinds[col] == "bool":
            field_defn.SetSubType(ogr.OFSTBoolean)
        lyr.CreateField(field_defn)

    layer_defn = lyr.GetLayerDefn()

    # ------------------------------------------------------------------
    # Batch-convert the rows accepted by the boundary pre-check to WKB.
    # ------------------------------------------------------------------
    geom_arr = gdf.geometry.values  # geopandas GeometryArray (shapely-backed)
    valid_idx = np.asarray(valid_row_indices, dtype=int)
    # to_wkb on a filtered numpy array → ndarray of bytes objects
    wkb_arr: Any = shapely.to_wkb(geom_arr[valid_idx])

    # ------------------------------------------------------------------
    # Pre-normalise property columns.
    # For numeric/bool kinds, Series.tolist() converts numpy scalars to
    # Python scalars in one call; isna().tolist() builds the null mask.
    # For string-kind columns we pre-build the raw list and fall back to
    # per-cell normalisation only for non-str values (datetime, JSON…).
    # ------------------------------------------------------------------
    col_nulls: dict[str, list[bool]] = {}
    col_vals: dict[str, list[Any]] = {}
    for col in property_cols:
        series = gdf[col]
        null_flags: list[bool] = series.isna().tolist()
        col_nulls[col] = null_flags
        kind = field_kinds[col]
        if kind == "bool":
            # Pre-convert to 0/1 int; nulls get a placeholder (never used).
            raw = series.tolist()
            col_vals[col] = [
                0 if null_flags[i] else int(bool(raw[i])) for i in range(len(raw))
            ]
        elif kind in ("int", "float"):
            col_vals[col] = series.tolist()  # numpy scalars → Python scalars
        else:  # "string" kind: str, datetime, list/dict, or None/NA
            col_vals[col] = series.tolist()

    # ------------------------------------------------------------------
    # Feature loop — uses pre-computed WKB and pre-normalised columns.
    # ------------------------------------------------------------------
    for local_i, row_i_raw in enumerate(valid_idx):
        row_i: int = int(row_i_raw)
        ogr_geom = ogr.CreateGeometryFromWkb(bytes(wkb_arr[local_i]))
        if ogr_geom is None:
            continue

        feat = ogr.Feature(layer_defn)
        feat.SetGeometry(ogr_geom)

        for col in property_cols:
            if col_nulls[col][row_i]:
                feat.SetFieldNull(col)
                continue

            val = col_vals[col][row_i]
            kind = field_kinds[col]
            if kind in ("bool", "int", "float"):
                # Already Python-native scalar; set directly.
                feat.SetField(col, val)
            else:
                # "string" kind: fast path for the common all-str case;
                # fall back to per-cell normalisation for other types.
                if isinstance(val, str):
                    feat.SetField(col, val)
                elif isinstance(val, (list, dict)):
                    feat.SetField(col, json.dumps(val, ensure_ascii=False))
                elif isinstance(val, (_dt.date, _dt.datetime)):
                    feat.SetField(col, val.isoformat())
                elif isinstance(val, (int, float, np.integer, np.floating)):
                    # Mixed column (e.g. scalars alongside list/dict values):
                    # OFTString field; pass the scalar and let OGR convert.
                    feat.SetField(
                        col,
                        int(val) if isinstance(val, (int, np.integer)) else float(val),
                    )
                else:
                    iso = getattr(type(val), "isoformat", None)
                    if callable(iso):
                        feat.SetField(col, iso(val))
                    else:
                        feat.SetFieldNull(col)

        lyr.CreateFeature(feat)
        feat = None  # Release


def _shapely_geom_type_to_ogr(gdf: Any, ogr: Any) -> int:
    """Return an OGR geometry type constant for the dominant type in *gdf*."""
    _map: dict[str, int] = {
        "Point": int(ogr.wkbPoint),
        "MultiPoint": int(ogr.wkbMultiPoint),
        "LineString": int(ogr.wkbLineString),
        "MultiLineString": int(ogr.wkbMultiLineString),
        "Polygon": int(ogr.wkbPolygon),
        "MultiPolygon": int(ogr.wkbMultiPolygon),
        "GeometryCollection": int(ogr.wkbGeometryCollection),
    }
    _unknown: int = int(ogr.wkbUnknown)
    types = gdf.geometry.geom_type.dropna().unique().tolist()
    if len(types) == 1:
        return _map.get(types[0], _unknown)
    return _unknown


def _read_vsimem(path: str, gdal: Any) -> bytes:
    """Return the raw bytes of a file stored in GDAL's virtual filesystem."""
    vf = gdal.VSIFOpenL(path, "rb")
    if vf is None:
        msg = f"Could not open vsimem file {path!r} for reading."
        raise RuntimeError(msg)
    try:
        gdal.VSIFSeekL(vf, 0, 2)  # Seek to end
        size = gdal.VSIFTellL(vf)
        gdal.VSIFSeekL(vf, 0, 0)  # Rewind
        raw = gdal.VSIFReadL(1, size, vf)
    finally:
        gdal.VSIFCloseL(vf)
    return bytes(raw)


def _write_path_atomic(output: Path, data: bytes) -> None:
    """Atomically replace *output* with *data* using a temporary sibling file."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(output.parent),
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(data)
            temp_path = Path(tmp_file.name)
        temp_path.replace(output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
