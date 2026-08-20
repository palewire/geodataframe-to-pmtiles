# API reference

```{eval-rst}
.. py:function:: geodataframe_to_pmtiles.write(layers: ~collections.abc.Mapping[str, ~geopandas.GeoDataFrame], output: str | Path | BinaryIO, *, min_zoom: int = 0, max_zoom: int = 8, name: str = "", description: str = "", attribution: str = "", json_fields: ~collections.abc.Collection[str] | None = None, on_overflow: "error" | "unsafe" = "error", simplification: float | None = None) -> None
   :module: geodataframe_to_pmtiles

   **Mapping form** — write multiple named layers from a ``{name: gdf, ...}`` mapping.

.. py:function:: geodataframe_to_pmtiles.write(layers: ~geopandas.GeoDataFrame, output: str | Path | BinaryIO, *, layer: str, min_zoom: int = 0, max_zoom: int = 8, name: str = "", description: str = "", attribution: str = "", json_fields: ~collections.abc.Collection[str] | None = None, on_overflow: "error" | "unsafe" = "error", simplification: float | None = None) -> None
   :module: geodataframe_to_pmtiles
   :no-index:

   **Single-frame form** — write one named layer from a single GeoDataFrame.

Both forms share the same keyword parameters, behaviour, and exceptions
documented below.

:param layers:
   Either a :class:`~collections.abc.Mapping` of layer name →
   :class:`~geopandas.GeoDataFrame`, or a single
   :class:`~geopandas.GeoDataFrame` (``layer`` is then required).
   Every GeoDataFrame must have an explicit, resolvable CRS.  GeoDataFrames
   not already in EPSG:4326 are automatically reprojected using traditional
   GIS X/Y axis order; inputs are never mutated.  The mapping must not be
   empty, and no GeoDataFrame may be empty.
:param output:
   Destination for the archive.  A :class:`str` or
   :class:`pathlib.Path` (written through a temporary file in the same
   directory and then atomically replaced) or any binary-writable stream
   such as :class:`io.BytesIO`.
:param layer:
   Layer name for the single-frame form.  Required when *layers* is a
   :class:`~geopandas.GeoDataFrame`; must be omitted when *layers* is a
   mapping.
:param min_zoom: Archive-wide minimum zoom level (0–22, default 0).
:param max_zoom: Archive-wide maximum zoom level (0–22, default 8).
:param name: Optional tileset name stored in the archive metadata.
:param description:
   Optional human-readable description stored in the archive metadata.
:param attribution:
   Optional attribution string stored in the archive's TileJSON metadata
   under the ``"attribution"`` key.  Any non-empty string is accepted;
   Unicode and HTML are preserved exactly.  When omitted or set to an empty
   string (the default) the key is not written to the archive.
:param json_fields:
   Controls which columns are JSON-encoded when they contain ``list`` or
   ``dict`` values.

   * ``None`` (default) — all list/dict columns are automatically
     JSON-encoded to strings.
   * :class:`~collections.abc.Collection`\[str] — only the named columns
     receive JSON treatment; other columns containing a list or dict raise
     :exc:`~geodataframe_to_pmtiles.exceptions.UnsupportedPropertyTypeError`.

   In both cases the encoding uses ``json.dumps`` with
   ``ensure_ascii=False``; the resulting MVT field type is ``String``.
:param on_overflow:
   Response to GDAL's per-tile ``MAX_FEATURES`` / ``MAX_SIZE`` actions.
   ``"error"`` (the default) raises
   :exc:`~geodataframe_to_pmtiles.exceptions.TileOverflowError` *before* the
   destination is changed.  ``"unsafe"`` writes the archive after a
   :class:`UserWarning`, even if GDAL dropped features or reduced geometry
   precision.
:param simplification:
   Optional geometry simplification factor in tile-coordinate units
   (4096 per tile).  ``None`` (default) disables simplification.

:raises TypeError:
   If *layers* is a :class:`~geopandas.GeoDataFrame` and *layer* is
   omitted, or if *layers* is a mapping and *layer* is provided.
:raises ~geodataframe_to_pmtiles.exceptions.EmptyLayerError:
   If *layers* is empty or any GeoDataFrame has zero features (including
   after excluding features outside the Web Mercator latitude extent).
:raises ~geodataframe_to_pmtiles.exceptions.MissingCRSError:
   If any GeoDataFrame has no CRS set.
:raises ~geodataframe_to_pmtiles.exceptions.UnsupportedCRSError:
   If any GeoDataFrame's CRS definition cannot be resolved by the installed
   geospatial stack.
:raises ~geodataframe_to_pmtiles.exceptions.CRSTransformError:
   If the coordinate transformation to EPSG:4326 fails at runtime.
:raises ~geodataframe_to_pmtiles.exceptions.UnsupportedPropertyTypeError:
   If a column contains a value that cannot be encoded as an MVT property,
   or a list/dict column is not covered by *json_fields*.
:raises ~geodataframe_to_pmtiles.exceptions.TileOverflowError:
   If ``on_overflow='error'`` and GDAL exceeds a per-tile cap.
:raises ValueError:
   If zoom levels are out of range or *min_zoom* > *max_zoom*.

.. rubric:: Notes

Features outside the Web Mercator latitude extent (±85.051°) are detected
before passing data to GDAL.  A :class:`UserWarning` is emitted once per
affected layer, and the feature is skipped.  If *all* features in a layer
are out of bounds, :exc:`~geodataframe_to_pmtiles.exceptions.EmptyLayerError` is raised.

Per-tile caps are fixed at ``MAX_FEATURES = 300,000`` and
``MAX_SIZE = 10 MB``.  Attribution is injected after GDAL writes the archive
by re-encoding only the metadata section; all MVT tile payloads are preserved
byte-for-byte.

.. automodule:: geodataframe_to_pmtiles.exceptions
   :members:
   :show-inheritance:
```
