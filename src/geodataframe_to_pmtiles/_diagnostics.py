"""Runtime checks for the optional GDAL PMTiles installation."""

from __future__ import annotations

import gzip
import re
from dataclasses import asdict, dataclass
from importlib import import_module
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIN_GDAL_VERSION = (3, 8, 0)
_DIAGNOSTIC_FAILURES = (
    AssertionError,
    AttributeError,
    EOFError,
    ImportError,
    IndexError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
_CHECK_NAMES = (
    "python_bindings",
    "gdal_versions",
    "supported_gdal_version",
    "pmtiles_driver",
    "pmtiles_capabilities",
    "pmtiles_smoke",
)

CheckName = Literal[
    "python_bindings",
    "gdal_versions",
    "supported_gdal_version",
    "pmtiles_driver",
    "pmtiles_capabilities",
    "pmtiles_smoke",
]


@dataclass(frozen=True)
class CheckResult:
    """One stable result returned by :func:`check`."""

    name: CheckName
    ok: bool
    observed: dict[str, str]
    message: str
    guidance: str


@dataclass(frozen=True)
class CheckReport:
    """Structured runtime diagnostic report returned by :func:`check`."""

    ok: bool
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation with stable keys."""
        return {"ok": self.ok, "checks": [asdict(result) for result in self.checks]}


def _result(
    name: CheckName,
    ok: bool,
    observed: dict[str, str],
    message: str,
    guidance: str,
) -> CheckResult:
    return CheckResult(name, ok, observed, message, guidance)


def _not_run(name: CheckName, reason: str) -> CheckResult:
    return _result(
        name,
        False,
        {},
        f"Not run: {reason}",
        "Fix the earlier failed checks, then run this command again.",
    )


def _failed_check(name: CheckName, action: str, exc: Exception) -> CheckResult:
    """Return a failed result for a recoverable diagnostic operation."""
    return _result(
        name,
        False,
        {"error": type(exc).__name__},
        f"Could not {action}.",
        "Reinstall matching GDAL and Python bindings from conda-forge, then run this command again.",
    )


def _load_osgeo_modules() -> tuple[Any, Any, Any]:
    """Import GDAL bindings only when diagnostics are explicitly requested."""
    return (
        import_module("osgeo.gdal"),
        import_module("osgeo.ogr"),
        import_module("osgeo.osr"),
    )


def _version_parts(value: str) -> tuple[int, int, int] | None:
    """Return the first three numeric version components, if present."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _reported_version(value: object) -> str:
    """Return a canonical version suitable for a support report."""
    parts = _version_parts(str(value))
    return ".".join(map(str, parts)) if parts is not None else "unknown"


def _version_check(gdal: Any) -> CheckResult:
    binding = _reported_version(getattr(gdal, "__version__", "unknown"))
    native = _reported_version(gdal.VersionInfo("RELEASE_NAME") or "unknown")
    binding_parts = _version_parts(binding)
    native_parts = _version_parts(native)
    compatible = (
        binding_parts is not None
        and native_parts is not None
        and binding_parts[:2] == native_parts[:2]
    )
    return _result(
        "gdal_versions",
        compatible,
        {"binding": binding, "native": native},
        (
            "Python bindings and native GDAL are compatible."
            if compatible
            else "Python bindings and native GDAL must have the same major and minor version."
        ),
        (
            "Install GDAL and its Python bindings from the same conda-forge environment."
            if not compatible
            else "No action needed."
        ),
    )


def _supported_version_check(gdal: Any) -> CheckResult:
    native = _reported_version(gdal.VersionInfo("RELEASE_NAME") or "unknown")
    version = _version_parts(native)
    supported = version is not None and version >= _MIN_GDAL_VERSION
    return _result(
        "supported_gdal_version",
        supported,
        {"native": native, "minimum": ".".join(map(str, _MIN_GDAL_VERSION))},
        (
            "Installed GDAL is in the supported range (3.8 or later)."
            if supported
            else "Installed GDAL is too old or its version could not be determined."
        ),
        (
            "Install GDAL 3.8 or later. Conda-forge is the most reliable option."
            if not supported
            else "No action needed."
        ),
    )


def _driver_check(gdal: Any) -> tuple[CheckResult, Any | None]:
    driver = gdal.GetDriverByName("PMTiles")
    found = driver is not None
    return (
        _result(
            "pmtiles_driver",
            found,
            {"driver": "PMTiles" if found else "missing"},
            (
                "GDAL provides the PMTiles driver."
                if found
                else "GDAL does not provide the PMTiles driver."
            ),
            (
                "Install a GDAL build with PMTiles support, preferably from conda-forge."
                if not found
                else "No action needed."
            ),
        ),
        driver,
    )


def _capability_check(driver: Any) -> CheckResult:
    vector = str(driver.GetMetadataItem("DCAP_VECTOR") or "")
    create = str(
        driver.GetMetadataItem("DCAP_CREATE")
        or driver.GetMetadataItem("DCAP_CREATE_DATASOURCE")
        or ""
    )
    usable = vector.upper() == "YES" and create.upper() == "YES"
    return _result(
        "pmtiles_capabilities",
        usable,
        {"vector": vector or "missing", "create": create or "missing"},
        (
            "The PMTiles driver supports vector creation."
            if usable
            else "The PMTiles driver does not report vector creation capability."
        ),
        (
            "Use a GDAL build with writable PMTiles vector support and required GEOS/SQLite dependencies."
            if not usable
            else "No action needed."
        ),
    )


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("truncated protobuf value")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    raise ValueError("invalid protobuf value")


def _protobuf_fields(data: bytes) -> Iterator[tuple[int, int, bytes | int]]:
    """Yield protobuf fields needed to inspect a tiny MVT payload."""
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number, wire_type = key >> 3, key & 0x07
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            value, offset = data[offset : offset + 8], offset + 8
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            value, offset = data[offset : offset + length], offset + length
        elif wire_type == 5:
            value, offset = data[offset : offset + 4], offset + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        yield field_number, wire_type, value


def _mvt_has_property(tile: bytes, layer_name: str, key: str, value: str) -> bool:
    """Return whether a decoded MVT tile has the expected string property."""
    for field_number, wire_type, layer_data in _protobuf_fields(tile):
        if field_number != 3 or wire_type != 2:
            continue
        assert isinstance(layer_data, bytes)
        name = ""
        keys: list[str] = []
        values: list[str | None] = []
        features: list[bytes] = []
        for layer_field, layer_wire, layer_value in _protobuf_fields(layer_data):
            if layer_field == 1 and layer_wire == 2:
                assert isinstance(layer_value, bytes)
                name = layer_value.decode("utf-8")
            elif layer_field == 2 and layer_wire == 2:
                assert isinstance(layer_value, bytes)
                features.append(layer_value)
            elif layer_field == 3 and layer_wire == 2:
                assert isinstance(layer_value, bytes)
                keys.append(layer_value.decode("utf-8"))
            elif layer_field == 4 and layer_wire == 2:
                assert isinstance(layer_value, bytes)
                string_value = None
                for value_field, value_wire, value_data in _protobuf_fields(
                    layer_value
                ):
                    if value_field == 1 and value_wire == 2:
                        assert isinstance(value_data, bytes)
                        string_value = value_data.decode("utf-8")
                        break
                values.append(string_value)
        if name != layer_name:
            continue
        for feature in features:
            for feature_field, feature_wire, feature_value in _protobuf_fields(feature):
                if feature_field != 2 or feature_wire != 2:
                    continue
                assert isinstance(feature_value, bytes)
                tags: list[int] = []
                tag_offset = 0
                while tag_offset < len(feature_value):
                    tag, tag_offset = _read_varint(feature_value, tag_offset)
                    tags.append(tag)
                for key_index, value_index in zip(tags[::2], tags[1::2], strict=True):
                    if (
                        key_index < len(keys)
                        and value_index < len(values)
                        and keys[key_index] == key
                        and values[value_index] == value
                    ):
                        return True
    return False


def _smoke_check() -> CheckResult:
    """Write and independently reopen a minimal in-memory PMTiles archive."""
    try:
        import geopandas as gpd
        from pmtiles.reader import MemorySource, Reader
        from shapely.geometry import Point

        from geodataframe_to_pmtiles._writer import write

        output = BytesIO()
        frame = gpd.GeoDataFrame(
            {"result": ["ok"]},
            geometry=[Point(0, 0)],
            crs="EPSG:4326",
        )
        write({"diagnostics": frame}, output, min_zoom=0, max_zoom=0)
        data = output.getvalue()
        reader = Reader(MemorySource(data))
        header = reader.header()
        tile = reader.get(0, 0, 0)
        if tile is None:
            raise ValueError("archive has no 0/0/0 tile")
        if header["tile_compression"].name.lower() == "gzip":
            tile = gzip.decompress(tile)
        if not _mvt_has_property(tile, "diagnostics", "result", "ok"):
            raise ValueError(
                "independent MVT decode did not find diagnostics.result='ok'"
            )
    except _DIAGNOSTIC_FAILURES as exc:
        return _result(
            "pmtiles_smoke",
            False,
            {"error": type(exc).__name__},
            "The in-memory PMTiles write and independent reopen/decode smoke test failed.",
            "Check GDAL's GEOS and SQLite support, then reinstall a complete GDAL build from conda-forge.",
        )
    return _result(
        "pmtiles_smoke",
        True,
        {"layer": "diagnostics", "property": "result=ok"},
        "A tiny PMTiles archive was written, reopened, and decoded in memory.",
        "No action needed.",
    )


def check() -> CheckReport:
    """Check whether the active environment can write PMTiles archives.

    This function imports GDAL only when called. Ordinary runtime failures are
    returned in the report rather than raised.
    """
    try:
        gdal, _ogr, _osr = _load_osgeo_modules()
    except _DIAGNOSTIC_FAILURES as exc:
        bindings = _result(
            "python_bindings",
            False,
            {"error": type(exc).__name__},
            "GDAL Python bindings could not be imported.",
            "Install matching GDAL and Python bindings with conda-forge. On macOS, "
            "install GDAL with Homebrew and use bindings built for that GDAL. On "
            "Linux, install matching GDAL runtime and Python packages from one source.",
        )
        return CheckReport(
            False,
            (
                bindings,
                *(
                    _not_run(name, "GDAL Python bindings are unavailable")
                    for name in _CHECK_NAMES[1:]
                ),
            ),
        )

    bindings = _result(
        "python_bindings",
        True,
        {"gdal": "imported", "ogr": "imported", "osr": "imported"},
        "GDAL, OGR, and OSR Python bindings imported successfully.",
        "No action needed.",
    )
    try:
        versions = _version_check(gdal)
    except _DIAGNOSTIC_FAILURES as exc:
        versions = _failed_check(
            "gdal_versions", "read GDAL binding and native versions", exc
        )
    try:
        supported = _supported_version_check(gdal)
    except _DIAGNOSTIC_FAILURES as exc:
        supported = _failed_check(
            "supported_gdal_version", "read the native GDAL version", exc
        )
    try:
        driver, pmtiles_driver = _driver_check(gdal)
    except _DIAGNOSTIC_FAILURES as exc:
        driver = _failed_check("pmtiles_driver", "find the PMTiles driver", exc)
        pmtiles_driver = None
    if pmtiles_driver is None:
        capabilities = _not_run(
            "pmtiles_capabilities", "the PMTiles driver is unavailable"
        )
    else:
        try:
            capabilities = _capability_check(pmtiles_driver)
        except _DIAGNOSTIC_FAILURES as exc:
            capabilities = _failed_check(
                "pmtiles_capabilities",
                "read PMTiles driver capabilities",
                exc,
            )
    smoke = (
        _smoke_check()
        if versions.ok and supported.ok and driver.ok and capabilities.ok
        else _not_run("pmtiles_smoke", "a required GDAL or PMTiles check failed")
    )
    checks = (bindings, versions, supported, driver, capabilities, smoke)
    return CheckReport(all(result.ok for result in checks), checks)
