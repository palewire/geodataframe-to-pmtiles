# API reference

```{eval-rst}
.. py:function:: geodataframe_to_pmtiles.write(layers: Mapping[str, GeoDataFrame], output: str | Path | BinaryIO, *, min_zoom: int = 0, max_zoom: int = 8, name: str = "", description: str = "", attribution: str = "", json_fields: Collection[str] | None = None, on_overflow: "error" | "unsafe" = "error", simplification: float | None = None) -> None
   :module: geodataframe_to_pmtiles

   **Mapping form** — write multiple named layers from a ``{name: gdf, ...}`` mapping.

.. py:function:: geodataframe_to_pmtiles.write(layers: GeoDataFrame, output: str | Path | BinaryIO, *, layer: str, min_zoom: int = 0, max_zoom: int = 8, name: str = "", description: str = "", attribution: str = "", json_fields: Collection[str] | None = None, on_overflow: "error" | "unsafe" = "error", simplification: float | None = None) -> None
   :module: geodataframe_to_pmtiles

   **Single-frame form** — write one named layer from a single GeoDataFrame.

.. autofunction:: geodataframe_to_pmtiles.write
   :no-index:

.. automodule:: geodataframe_to_pmtiles.exceptions
   :members:
   :show-inheritance:
```
