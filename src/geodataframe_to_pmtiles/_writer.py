"""Write PMTiles vector archives from GeoPandas GeoDataFrames using GDAL.

This module provides the main ``write_pmtiles`` function, which is the sole
public entry point for the library.  The GDAL PMTiles vector driver is used
directly; no subprocesses are spawned and no temporary files are written to
disk.  Intermediate data lives in GDAL's in-memory virtual filesystem
(``/vsimem/``) for the duration of the call.

.. rubric:: Property normalisation rules

+---------------------------+--------------------+------------------------------+
| Python / pandas type      | MVT field type     | Notes                        |
+===========================+====================+==============================+
| ``str``                   | String             |                              |
+---------------------------+--------------------+------------------------------+
| ``bool`` / ``np.bool_``   | Integer (0 or 1)   | MVT has no native bool type  |
+---------------------------+--------------------+------------------------------+
| ``int`` / ``np.integer``  | Integer64          |                              |
+---------------------------+--------------------+------------------------------+
| ``float`` / ``np.float_`` | Real               | ``NaN`` → null               |
+---------------------------+--------------------+------------------------------+
| ``datetime``              | String             | ISO 8601, UTC if tz-aware    |
+---------------------------+--------------------+------------------------------+
| ``list`` / ``dict``       | String             | JSON-encoded (explicit)      |
+---------------------------+--------------------+------------------------------+
| ``None`` / ``pd.NA``      | null field         |                              |
+---------------------------+--------------------+------------------------------+
| anything else             | —                  | raises UnsupportedPropertyTypeError |
+---------------------------+--------------------+------------------------------+

.. rubric:: Known limitations

* The GDAL PMTiles driver silently drops features whose tile exceeds
  ``MAX_SIZE`` bytes or ``MAX_FEATURES`` per tile.  ``write_pmtiles``
  sets both limits to very large values (500 MB / 2 000 000 features) to
  reduce the chance of silent loss, but callers writing large datasets should
  check the output tile count or use per-layer simplification independently.
  There is no guaranteed error on overflow; this is a GDAL driver limitation.
* Feature counts reported when *reading back* a PMTiles archive will be
  higher than the input because the MVT format duplicates features across
  tile boundaries.  This is expected and not data loss.
* Simplification is disabled by default.  Pass ``simplification`` to enable
  it; the value is a tolerance in tile coordinates (4096 units per tile).
* Attribution metadata is stored in the ``tilejson`` JSON blob inside the
  archive, not as a first-class GDAL metadata key.  ``write_pmtiles`` embeds
  it via the ``CONF`` creation option so standards-compliant readers can
  surface it.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import uuid
from io import IOBase
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import numpy as np

from geodataframe_to_pmtiles.exceptions import (
    EmptyLayerError,
    MissingCRSError,
    UnsupportedCRSError,
    UnsupportedPropertyTypeError,
)

if TYPE_CHECKING:
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

# Safety limits: very large values to minimise silent tile-level feature drops.
# See module docstring for the caveats.
_SAFE_MAX_SIZE: int = 500_000_000  # bytes per tile
_SAFE_MAX_FEATURES: int = 2_000_000  # features per tile

# ---------------------------------------------------------------------------
# OGR field-type helpers
# ---------------------------------------------------------------------------

_OGR_FIELD_TYPES: dict[str, int] = {
    "string": int(ogr.OFTString),
    "int": int(ogr.OFTInteger64),
    "float": int(ogr.OFTReal),
    "bool": int(ogr.OFTInteger),  # MVT has no native bool; store as 0/1
}


def _infer_ogr_field_type(series: gpd.pd.Series) -> int:  # type: ignore[name-defined]
    """Return an OGR field type constant for *series*.

    Only the first non-null value is examined; if the series is entirely null
    the field is typed as String.  Raises ``UnsupportedPropertyTypeError`` for
    types that cannot be normalised.
    """
    import pandas as pd

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
            return _OGR_FIELD_TYPES["string"]  # JSON-encoded
        if isinstance(val, (_dt.date, _dt.datetime)):
            return _OGR_FIELD_TYPES["string"]  # ISO 8601 string
        # pandas Timestamp and other datetime-like types.
        if callable(getattr(type(val), "isoformat", None)):
            return _OGR_FIELD_TYPES["string"]  # ISO 8601 string
        raise UnsupportedPropertyTypeError(
            f"Column '{series.name}' contains a value of type {type(val).__name__!r} "
            "which cannot be encoded as an MVT property.  Supported types are: "
            "str, bool, int, float, list, dict, datetime, None / NA."
        )

    # Entirely null column → String
    return _OGR_FIELD_TYPES["string"]


def _normalise_value(
    val: object,
    column_name: str,
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
        return False, json.dumps(val, ensure_ascii=False)
    if isinstance(val, (_dt.date, _dt.datetime)):
        return False, val.isoformat()
    # pandas Timestamp and other datetime-like objects with isoformat() method.
    iso_method = getattr(type(val), "isoformat", None)
    if callable(iso_method):
        return False, iso_method(val)
    raise UnsupportedPropertyTypeError(
        f"Column '{column_name}' contains a value of type {type(val).__name__!r} "
        "which cannot be encoded as an MVT property.  Supported types are: "
        "str, bool, int, float, list, dict, datetime, None / NA."
    )


# ---------------------------------------------------------------------------
# Core writer
# ---------------------------------------------------------------------------


def write_pmtiles(
    layers: dict[str, gpd.GeoDataFrame],
    output: Path | BinaryIO,
    *,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    name: str = "",
    description: str = "",
    attribution: str = "",
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
        file is created or overwritten) or any binary-writable stream
        (e.g. :class:`io.BytesIO`).
    min_zoom:
        Archive-wide minimum zoom level (0-22, default 0).
    max_zoom:
        Archive-wide maximum zoom level (0-22, default 8).
    name:
        Optional tileset name stored in the archive metadata.
    description:
        Optional human-readable description stored in the archive metadata.
    attribution:
        Optional attribution string stored in the archive metadata as a
        TileJSON ``attribution`` key.  Note: GDAL does not expose this as a
        first-class metadata field; it is embedded via the ``CONF`` JSON
        option and may not be surfaced by all readers.
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
        If a column contains a value that cannot be encoded as an MVT property.
    ValueError
        If zoom levels are out of range or *min_zoom* > *max_zoom*.

    Notes
    -----
    The GDAL PMTiles driver silently drops features when a tile exceeds its
    per-tile ``MAX_SIZE`` or ``MAX_FEATURES`` limit.  ``write_pmtiles`` sets
    very generous limits (500 MB / 2 000 000 features per tile) to reduce
    the chance of silent loss, but there is no guaranteed error on overflow.
    See the module docstring for a complete list of known limitations.

    List- and dict-valued properties are explicitly JSON-encoded to strings.
    Boolean values are stored as integers (1 for True, 0 for False) because
    the MVT specification does not include a dedicated boolean type.
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
                "Set the CRS to EPSG:4326 before calling write_pmtiles."
            )
            raise MissingCRSError(msg)
        if gdf.crs.to_epsg() != 4326:
            msg = (
                f"Layer '{layer_name}' has CRS {gdf.crs!r} (EPSG:{gdf.crs.to_epsg()}).  "
                "Only EPSG:4326 (geographic WGS 84) is supported; "
                "reproject with gdf.to_crs('EPSG:4326') before calling write_pmtiles."
            )
            raise UnsupportedCRSError(msg)

    if not (0 <= min_zoom <= 22):
        msg = f"min_zoom must be between 0 and 22, got {min_zoom}."
        raise ValueError(msg)
    if not (0 <= max_zoom <= 22):
        msg = f"max_zoom must be between 0 and 22, got {max_zoom}."
        raise ValueError(msg)
    if min_zoom > max_zoom:
        msg = f"min_zoom ({min_zoom}) must be ≤ max_zoom ({max_zoom})."
        raise ValueError(msg)

    # Validate all column types up-front before any GDAL objects are created.
    # This prevents a partially-initialised dataset from being written.
    for gdf in layers.values():
        for col in (c for c in gdf.columns if c != gdf.geometry.name):
            _infer_ogr_field_type(gdf[col])  # raises UnsupportedPropertyTypeError early

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

    # Build CONF JSON for metadata that GDAL does not expose as named options.
    conf: dict[str, str] = {}
    if attribution:
        conf["attribution"] = attribution

    ds_options: list[str] = [
        f"MINZOOM={min_zoom}",
        f"MAXZOOM={max_zoom}",
        f"MAX_SIZE={_SAFE_MAX_SIZE}",
        f"MAX_FEATURES={_SAFE_MAX_FEATURES}",
    ]
    if name:
        ds_options.append(f"NAME={name}")
    if description:
        ds_options.append(f"DESCRIPTION={description}")
    if conf:
        ds_options.append(f"CONF={json.dumps(conf)}")

    ds = drv.CreateDataSource(vsimem_path, options=ds_options)
    if ds is None:
        msg = f"GDAL could not create a PMTiles datasource at {vsimem_path!r}."
        raise RuntimeError(msg)

    try:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)

        for layer_name, gdf in layers.items():
            _write_layer(ds, layer_name, gdf, srs, simplification)

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
        msg = f"'output' must be a pathlib.Path or binary-writable stream, got {type(output).__name__!r}."
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
        field_types[col] = _infer_ogr_field_type(gdf[col])
        fld = ogr.FieldDefn(col, field_types[col])
        lyr.CreateField(fld)

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
            is_null, normalised = _normalise_value(val, col)
            if is_null or normalised is None:
                feat.SetFieldNull(col)
            else:
                feat.SetField(col, normalised)

        lyr.CreateFeature(feat)
        feat = None  # Release

    _ = pd  # suppress unused-import; needed for pd.isna in helpers


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
