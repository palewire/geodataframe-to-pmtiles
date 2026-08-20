"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL.

This module provides the main ``write_pmtiles`` function, which is the sole
public entry point for the library.  The GDAL PMTiles vector driver is used
directly; no subprocesses are spawned and no temporary files are written to
disk.  Intermediate data lives in GDAL's in-memory virtual filesystem
(``/vsimem/``) for the duration of the call.

.. rubric:: Property normalisation rules

+---------------------------+--------------------+------------------------------------------+
| Python / pandas type      | MVT field type     | Notes                                    |
+===========================+====================+==========================================+
| ``str``                   | String             |                                          |
+---------------------------+--------------------+------------------------------------------+
| ``bool`` / ``np.bool_``   | Integer (0 or 1)   | MVT has no native bool type              |
+---------------------------+--------------------+------------------------------------------+
| ``int`` / ``np.integer``  | Integer64          |                                          |
+---------------------------+--------------------+------------------------------------------+
| ``float`` / ``np.float_`` | Real               | ``NaN`` → null                           |
+---------------------------+--------------------+------------------------------------------+
| ``datetime``              | String             | ISO 8601, UTC if tz-aware                |
+---------------------------+--------------------+------------------------------------------+
| ``list`` / ``dict``       | String             | JSON-encoded; column must appear in      |
|                           |                    | ``json_fields`` (or ``json_fields=None`` |
|                           |                    | for auto).  Unlisted list/dict columns   |
|                           |                    | raise ``UnsupportedPropertyTypeError``.  |
+---------------------------+--------------------+------------------------------------------+
| ``None`` / ``pd.NA``      | null field         |                                          |
+---------------------------+--------------------+------------------------------------------+
| anything else             | —                  | raises ``UnsupportedPropertyTypeError``  |
+---------------------------+--------------------+------------------------------------------+

.. rubric:: Overflow policy

The GDAL PMTiles driver silently drops features when a tile exceeds its
per-tile ``MAX_SIZE`` (bytes) or ``MAX_FEATURES`` limit.  ``write_pmtiles``
sets per-tile limits derived from the input: ``MAX_FEATURES`` is set to
``max(2_000_000, total_features_across_all_layers)`` so that a single tile
could theoretically hold every input feature.  ``MAX_SIZE`` is set to 500 MB.

Despite these generous limits, dense spatial clustering at coarse zoom levels
can still produce tiles that exceed the limit.  The GDAL driver provides no
post-write signal when a drop occurs.

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
``ds.GetMetadata()`` on read-back**.  Attribution therefore cannot be round-
tripped reliably through this backend without direct byte-level patching of
the PMTiles file, which is outside the scope of this POC.  The parameter has
been intentionally omitted from the public API; support can be added in a
future iteration once a reliable mechanism is found.

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
from io import IOBase
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

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

try:
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    ogr.UseExceptions()
except ImportError as _gdal_err:
    msg = (
        "GDAL Python bindings are required but could not be imported. "
        "Install the 'gdal' package matching your system GDAL version, e.g. "
        "'pip install gdal==3.12.2'.  The system GDAL development headers must "
        "be present (e.g. 'libgdal-dev' on Debian/Ubuntu or the 'gdal' "
        "Homebrew formula on macOS)."
    )
    raise ImportError(msg) from _gdal_err

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default archive-wide minimum zoom level.
DEFAULT_MIN_ZOOM: int = 0

#: Default archive-wide maximum zoom level.
DEFAULT_MAX_ZOOM: int = 8

# Floor for per-tile feature limit even when input is small.
_MIN_MAX_FEATURES: int = 2_000_000

# Per-tile size ceiling (bytes).  500 MB is well above any realistic tile.
_MAX_SIZE_BYTES: int = 500_000_000

# ---------------------------------------------------------------------------
# OGR field-type helpers
# ---------------------------------------------------------------------------

_OGR_FIELD_TYPES: dict[str, int] = {
    "string": int(ogr.OFTString),
    "int": int(ogr.OFTInteger64),
    "float": int(ogr.OFTReal),
    "bool": int(ogr.OFTInteger),  # MVT has no native bool; store as 0/1
}


def _infer_ogr_field_type(
    series: gpd.pd.Series,  # type: ignore[name-defined]
    json_field_names: frozenset[str] | None,
) -> int:
    """Return an OGR field type constant for *series*.

    Only the first non-null value is examined.  An entirely-null series is
    typed as String.  Raises ``UnsupportedPropertyTypeError`` for types that
    cannot be normalised, respecting the ``json_field_names`` policy.

    Parameters
    ----------
    series:
        A pandas Series from a GeoDataFrame column.
    json_field_names:
        If ``None``, list/dict values are auto-JSON-encoded.  If a frozenset,
        only column names in the set receive JSON treatment; other list/dict
        values raise ``UnsupportedPropertyTypeError``.
    """
    import pandas as pd

    col_name = str(series.name)

    for val in series:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        try:
            if pd.isna(val):
                continue
        except (TypeError, ValueError):
            pass

        if isinstance(val, bool) or (
            isinstance(val, np.generic) and np.issubdtype(type(val), np.bool_)
        ):
            return _OGR_FIELD_TYPES["bool"]
        if isinstance(val, (int, np.integer)):
            return _OGR_FIELD_TYPES["int"]
        if isinstance(val, (float, np.floating)):
            return _OGR_FIELD_TYPES["float"]
        if isinstance(val, str):
            return _OGR_FIELD_TYPES["string"]
        if isinstance(val, (list, dict)):
            if json_field_names is None or col_name in json_field_names:
                return _OGR_FIELD_TYPES["string"]  # will be JSON-encoded
            raise UnsupportedPropertyTypeError(
                f"Column '{col_name}' contains list/dict values that would be "
                "JSON-encoded, but this column is not in json_fields.  "
                "Pass json_fields=None to auto-encode all list/dict columns, "
                f"or include '{col_name}' in json_fields explicitly."
            )
        if isinstance(val, (_dt.date, _dt.datetime)):
            return _OGR_FIELD_TYPES["string"]  # ISO 8601 string
        # pandas Timestamp and other datetime-like types.
        if callable(getattr(type(val), "isoformat", None)):
            return _OGR_FIELD_TYPES["string"]  # ISO 8601 string
        raise UnsupportedPropertyTypeError(
            f"Column '{col_name}' contains a value of type "
            f"{type(val).__name__!r} which cannot be encoded as an MVT "
            "property.  Supported types: str, bool, int, float, "
            "list (with json_fields), dict (with json_fields), datetime, None / NA."
        )

    # Entirely null column → String
    return _OGR_FIELD_TYPES["string"]


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

    if isinstance(val, bool) or (
        isinstance(val, np.generic) and np.issubdtype(type(val), np.bool_)
    ):
        return False, int(val)
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
    "per-tile MAX_SIZE or MAX_FEATURES limit.  Per-tile limits are set "
    "generously (MAX_FEATURES = max(2_000_000, total_features); MAX_SIZE = "
    "500 MB) to minimise the risk, but dense spatial clustering at coarse zoom "
    "levels can still trigger silent drops.  GDAL provides no post-write "
    "signal when a drop occurs.  Verify the output independently for "
    "high-density datasets.  Pass on_overflow='ignore' to suppress this "
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
    **Attribution is not supported in this POC.**  The GDAL 3.12 PMTiles
    driver does not reliably write or expose attribution metadata.  See the
    module docstring for details.

    List- and dict-valued properties are explicitly JSON-encoded to strings.
    Boolean values are stored as integers (1 for True, 0 for False) because
    the MVT specification does not include a dedicated boolean type.

    Per-tile ``MAX_FEATURES`` is derived from the input:
    ``max(2_000_000, total_features_across_all_layers)``.  ``MAX_SIZE`` is
    fixed at 500 MB.
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
    for gdf in layers.values():
        for col in (c for c in gdf.columns if c != gdf.geometry.name):
            _infer_ogr_field_type(gdf[col], json_field_names)

    # ------------------------------------------------------------------
    # Overflow policy
    # ------------------------------------------------------------------
    total_features = sum(len(gdf) for gdf in layers.values())
    # Derive a generous per-tile MAX_FEATURES from the input so that a
    # single tile could theoretically contain all features.
    derived_max_features = max(_MIN_MAX_FEATURES, total_features)

    if on_overflow == "error":
        msg = (
            f"on_overflow='error': refusing to write because tile-level "
            f"data loss cannot be ruled out.  The GDAL PMTiles driver "
            f"silently drops features when a tile exceeds its per-tile "
            f"MAX_FEATURES ({derived_max_features:,}) or MAX_SIZE "
            f"({_MAX_SIZE_BYTES / 1_000_000:.0f} MB) limit, and provides "
            f"no post-write signal when a drop occurs.  "
            f"Pass on_overflow='warn' or on_overflow='ignore' to proceed "
            f"after acknowledging this limitation."
        )
        raise TileOverflowError(msg)

    if on_overflow == "warn":
        warnings.warn(_OVERFLOW_WARNING, UserWarning, stacklevel=2)

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
        f"MAX_SIZE={_MAX_SIZE_BYTES}",
        f"MAX_FEATURES={derived_max_features}",
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
            _write_layer(ds, layer_name, gdf, srs, simplification, json_field_names)

        # Flush to vsimem before reading back.
        ds.FlushCache()
    finally:
        ds = None  # Close/release the datasource

    # ------------------------------------------------------------------
    # Read the bytes from /vsimem/ and write to the caller's output.
    # ------------------------------------------------------------------
    try:
        data = _read_vsimem(vsimem_path)
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
    ds: gdal.Dataset,
    layer_name: str,
    gdf: gpd.GeoDataFrame,
    srs: osr.SpatialReference,
    simplification: float | None,
    json_field_names: frozenset[str] | None,
) -> None:
    """Create an OGR layer inside *ds* and populate it from *gdf*."""
    import pandas as pd

    # Determine OGR geometry type from the GeoDataFrame.
    geom_type = _shapely_geom_type_to_ogr(gdf)

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
    field_types: dict[str, int] = {}
    for col in property_cols:
        field_types[col] = _infer_ogr_field_type(gdf[col], json_field_names)
        lyr.CreateField(ogr.FieldDefn(col, field_types[col]))

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


def _shapely_geom_type_to_ogr(gdf: gpd.GeoDataFrame) -> int:
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


def _read_vsimem(path: str) -> bytes:
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
