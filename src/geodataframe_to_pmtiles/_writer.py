"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL.

This module provides the main ``write_pmtiles`` function, which is the sole
public entry point for the library.  The GDAL PMTiles vector driver is used
directly; no subprocesses are spawned and no temporary files are written to
disk.  Intermediate data lives in GDAL's in-memory virtual filesystem
(``/vsimem/``) for the duration of the call.

The package imports without GDAL present.  ``write_pmtiles`` raises a clear
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

.. rubric:: Per-tile caps (tested POC values)

The GDAL PMTiles driver silently drops features when a tile exceeds its
per-tile ``MAX_FEATURES`` or ``MAX_SIZE`` limit.  ``write_pmtiles`` uses the
following fixed caps, which were validated by spike testing:

* ``MAX_FEATURES = 300_000`` per tile
* ``MAX_SIZE = 10_000_000`` bytes (10 MB) per tile

**Spike result:** 200,001 point features at zoom 0 were preserved in full
and produced a 630,430-byte compressed archive with these caps.

These are **POC values, not universal limits**.  A single dense tile
(e.g. all features in a 40 km x 40 km area at z8) could still exceed 300 K
features.  Setting ``MAX_FEATURES=0`` does *not* disable the limit — GDAL
clamps it to its internal minimum rather than treating 0 as "unlimited".

The GDAL driver provides no post-write signal when a drop occurs.

Use ``on_overflow`` to control the library's response:

* ``"warn"`` (default) - emit a :class:`UserWarning` before writing.
* ``"ignore"`` - write silently.
* ``"error"`` - raise :class:`~geodataframe_to_pmtiles.TileOverflowError`
  *before* writing, with an explanation of the GDAL limitation.  This forces
  callers to opt in to ``"warn"`` or ``"ignore"`` after reading the warning.

.. rubric:: Attribution — not supported in this POC

The GDAL PMTiles vector driver's ``CONF`` creation option was investigated as
a means of embedding an ``attribution`` field in the TileJSON metadata block.
Testing with GDAL 3.12.2 showed that the ``attribution`` key passed via
``CONF`` is **not written to the raw archive bytes** and **not returned by
``ds.GetMetadata()`` on read-back**.  Official PMTiles attribution would
require rebuilding the archive with byte-level patching, which is outside the
scope of this POC.  The parameter is intentionally omitted from the public
API.

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
import json
import math
import uuid
import warnings
from importlib import import_module
from io import IOBase
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

import numpy as np

from geodataframe_to_pmtiles.exceptions import (
    EmptyLayerError,
    MissingCRSError,
    TileOverflowError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    import geopandas as gpd

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default archive-wide minimum zoom level.
DEFAULT_MIN_ZOOM: int = 0

#: Default archive-wide maximum zoom level.
DEFAULT_MAX_ZOOM: int = 8

# Spike-validated per-tile caps.  200,001 z0 point features were preserved
# in full at these values (630,430 compressed bytes).  They are NOT unlimited:
# GDAL clamps MAX_FEATURES=0 to its internal minimum rather than disabling the
# limit.  Dense tiles may still exceed these caps and produce silent drops.
_POC_MAX_FEATURES: int = 300_000
_POC_MAX_SIZE: int = 10_000_000  # bytes per tile (~10 MB)

PropertyKind = Literal["string", "int", "float", "bool"]


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
            "system package manager), then call write_pmtiles again."
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
OverflowPolicy = Literal["error", "warn", "ignore"]

_OVERFLOW_WARNING = (
    "The GDAL PMTiles driver silently drops features when a tile exceeds its "
    f"per-tile MAX_FEATURES ({_POC_MAX_FEATURES:,}) or MAX_SIZE "
    f"({_POC_MAX_SIZE // 1_000_000} MB) limit.  These are spike-validated POC "
    "caps (200,001 z0 features preserved in 630,430 bytes) but are not "
    "unlimited: a single dense tile could still exceed them.  Setting "
    "MAX_FEATURES=0 does not disable the limit; GDAL clamps it to its internal "
    "minimum.  GDAL provides no post-write overflow signal.  Verify the output "
    "for high-density datasets.  Pass on_overflow='ignore' to suppress this "
    "warning, or on_overflow='error' to refuse to write instead."
)


def write_pmtiles(
    layers: dict[str, gpd.GeoDataFrame],
    output: Path | BinaryIO,
    *,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    name: str = "",
    description: str = "",
    json_fields: Collection[str] | None = None,
    on_overflow: OverflowPolicy = "warn",
    simplification: float | None = None,
) -> None:
    """Write a PMTiles vector archive from one or more GeoDataFrames.

    Parameters
    ----------
    layers:
        Mapping of layer name → GeoDataFrame.  Every GeoDataFrame must have
        CRS EPSG:4326 (geographic, longitude/latitude).  The mapping must not
        be empty, and no GeoDataFrame may be empty.
    output:
        Destination for the archive.  Either a :class:`pathlib.Path` (the
        file is created or overwritten) or any binary-writable stream such as
        :class:`io.BytesIO`.
    min_zoom:
        Archive-wide minimum zoom level (0-22, default 0).
    max_zoom:
        Archive-wide maximum zoom level (0-22, default 8).
    name:
        Optional tileset name stored in the archive metadata.
    description:
        Optional human-readable description stored in the archive metadata.
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
        Overflow policy for GDAL's per-tile ``MAX_FEATURES`` / ``MAX_SIZE``
        limits (see module docstring for details).  One of:

        * ``"warn"`` (default) — emit a :class:`UserWarning` before writing.
        * ``"ignore"`` — write silently.
        * ``"error"`` — raise :class:`~geodataframe_to_pmtiles.TileOverflowError`
          before writing; the caller must acknowledge the limitation by
          switching to ``"warn"`` or ``"ignore"``.
    simplification:
        Optional geometry simplification factor in tile-coordinate units
        (4096 per tile).  ``None`` (default) disables simplification, which
        is recommended for a proof-of-concept to avoid unexpected data loss.

    Raises
    ------
    EmptyLayerError
        If *layers* is empty or any GeoDataFrame has zero features.
    MissingCRSError
        If any GeoDataFrame has no CRS set.
    UnsupportedCRSError
        If any GeoDataFrame's CRS is not EPSG:4326.
    UnsupportedPropertyTypeError
        If a column contains a value that cannot be encoded as an MVT property,
        or a list/dict column is not covered by *json_fields*.
    TileOverflowError
        If ``on_overflow='error'``.  See module docstring for GDAL limitations.
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
    """
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
                "Set the CRS to EPSG:4326 or reproject with "
                "gdf.to_crs('EPSG:4326') before calling write_pmtiles."
            )
            raise MissingCRSError(msg)
        if gdf.crs.to_epsg() != 4326:
            msg = (
                f"Layer '{layer_name}' has CRS {gdf.crs!r} "
                f"(EPSG:{gdf.crs.to_epsg()}).  "
                "Only EPSG:4326 (geographic WGS 84) is accepted.  "
                "Reproject with gdf.to_crs('EPSG:4326') before calling "
                "write_pmtiles."
            )
            raise UnsupportedCRSError(msg)

    if not (0 <= min_zoom <= 22):
        msg = f"min_zoom must be between 0 and 22, got {min_zoom}."
        raise ValueError(msg)
    if not (0 <= max_zoom <= 22):
        msg = f"max_zoom must be between 0 and 22, got {max_zoom}."
        raise ValueError(msg)
    if min_zoom > max_zoom:
        msg = f"min_zoom ({min_zoom}) must be <= max_zoom ({max_zoom})."
        raise ValueError(msg)

    if on_overflow not in ("error", "warn", "ignore"):
        msg = f"on_overflow must be 'error', 'warn', or 'ignore', got {on_overflow!r}."
        raise ValueError(msg)

    # Normalise json_fields to frozenset or None for O(1) membership tests.
    json_field_names: frozenset[str] | None = (
        None if json_fields is None else frozenset(json_fields)
    )

    # Validate all column types up-front before any GDAL objects are created.
    # This prevents a partially-initialised dataset from being written.
    field_kinds_by_layer: dict[str, dict[str, PropertyKind]] = {}
    for layer_name, gdf in layers.items():
        field_kinds: dict[str, PropertyKind] = {}
        for col in (c for c in gdf.columns if c != gdf.geometry.name):
            field_kinds[col] = _infer_property_kind(gdf[col], json_field_names)
        field_kinds_by_layer[layer_name] = field_kinds

    # ------------------------------------------------------------------
    # Overflow policy
    # ------------------------------------------------------------------
    if on_overflow == "error":
        msg = (
            f"on_overflow='error': refusing to write because tile-level "
            f"data loss cannot be ruled out.  The GDAL PMTiles driver "
            f"silently drops features when a tile exceeds its fixed per-tile "
            f"caps (MAX_FEATURES={_POC_MAX_FEATURES:,}, "
            f"MAX_SIZE={_POC_MAX_SIZE // 1_000_000} MB), and provides no "
            f"post-write signal when a drop occurs.  Setting MAX_FEATURES=0 "
            f"does not disable the limit.  "
            f"Pass on_overflow='warn' or on_overflow='ignore' to proceed "
            f"after acknowledging this limitation."
        )
        raise TileOverflowError(msg)

    if on_overflow == "warn":
        warnings.warn(_OVERFLOW_WARNING, UserWarning, stacklevel=2)

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
        f"MAX_SIZE={_POC_MAX_SIZE}",
        f"MAX_FEATURES={_POC_MAX_FEATURES}",
    ]
    if name:
        ds_options.append(f"NAME={name}")
    if description:
        ds_options.append(f"DESCRIPTION={description}")

    ds = drv.CreateDataSource(vsimem_path, options=ds_options)
    if ds is None:
        msg = f"GDAL could not create a PMTiles datasource at {vsimem_path!r}."
        raise RuntimeError(msg)

    try:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)

        for layer_name, gdf in layers.items():
            _write_layer(
                ds,
                layer_name,
                gdf,
                srs,
                simplification,
                json_field_names,
                ogr,
                field_kinds_by_layer[layer_name],
            )

        # Flush to vsimem before reading back.
        ds.FlushCache()
    finally:
        ds = None  # Close/release the datasource

    # ------------------------------------------------------------------
    # Read the bytes from /vsimem/ and write to the caller's output.
    # ------------------------------------------------------------------
    try:
        data = _read_vsimem(vsimem_path, gdal)
    finally:
        gdal.Unlink(vsimem_path)

    if isinstance(output, Path):
        output.write_bytes(data)
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


def _write_layer(
    ds: Any,
    layer_name: str,
    gdf: Any,
    srs: Any,
    simplification: float | None,
    json_field_names: frozenset[str] | None,
    ogr: Any,
    field_kinds: dict[str, PropertyKind],
) -> None:
    """Create an OGR layer inside *ds* and populate it from *gdf*."""
    import pandas as pd

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
    ogr_field_types = _ogr_field_types(ogr)
    field_types: dict[str, int] = {
        col: ogr_field_types[field_kinds[col]] for col in property_cols
    }
    for col in property_cols:
        field_defn = ogr.FieldDefn(col, field_types[col])
        if field_kinds[col] == "bool":
            field_defn.SetSubType(ogr.OFSTBoolean)
        lyr.CreateField(field_defn)

    layer_defn = lyr.GetLayerDefn()
    geom_col = str(gdf.geometry.name)

    # Write features in input order (deterministic).
    for row in gdf.itertuples(index=False):
        geom = getattr(row, geom_col)
        if geom is None or (hasattr(geom, "is_empty") and geom.is_empty):
            continue  # Skip null / empty geometries

        feat = ogr.Feature(layer_defn)
        ogr_geom = ogr.CreateGeometryFromWkt(geom.wkt)
        if ogr_geom is None:
            continue
        feat.SetGeometry(ogr_geom)

        for col in property_cols:
            val = getattr(row, col)
            is_null, normalised = _normalise_value(val, col, json_field_names)
            if is_null or normalised is None:
                feat.SetFieldNull(col)
            else:
                feat.SetField(col, normalised)

        lyr.CreateFeature(feat)
        feat = None  # Release

    _ = pd  # suppress unused-import; pd.isna is used in helpers


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
