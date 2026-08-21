"""Tests for post-install GDAL and PMTiles diagnostics."""

from __future__ import annotations

import importlib
import json
from typing import Final

import pytest

import geodataframe_to_pmtiles as gpm
from geodataframe_to_pmtiles import __main__ as cli
from geodataframe_to_pmtiles import _diagnostics as diagnostics


class _FakeDriver:
    def __init__(self, *, vector: str = "YES", create: str = "YES") -> None:
        self.vector = vector
        self.create = create

    def GetMetadataItem(self, name: str) -> str | None:  # noqa: N802
        return {
            "DCAP_VECTOR": self.vector,
            "DCAP_CREATE": self.create,
        }.get(name)


class _DefaultDriver:
    pass


_DEFAULT_DRIVER: Final = _DefaultDriver()


class _FakeGdal:
    def __init__(
        self,
        *,
        binding: str = "3.12.2",
        native: str = "3.12.2",
        driver: _FakeDriver | _DefaultDriver | None = _DEFAULT_DRIVER,
    ) -> None:
        self.__version__ = binding
        self.native = native
        self.driver = _FakeDriver() if driver is _DEFAULT_DRIVER else driver

    def VersionInfo(self, name: str) -> str:  # noqa: N802
        assert name == "RELEASE_NAME"
        return self.native

    def GetDriverByName(self, name: str) -> _FakeDriver | None:  # noqa: N802
        assert name == "PMTiles"
        return self.driver


def _fake_modules(gdal: _FakeGdal) -> tuple[object, object, object]:
    return gdal, object(), object()


def _successful_smoke() -> diagnostics.CheckResult:
    return diagnostics.CheckResult(
        "pmtiles_smoke",
        True,
        {"layer": "diagnostics", "property": "result=ok"},
        "A tiny PMTiles archive was written, reopened, and decoded in memory.",
        "No action needed.",
    )


def test_check_reports_missing_bindings_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing osgeo returns stable failed results rather than an exception."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: (_ for _ in ()).throw(ImportError("no module named osgeo")),
    )

    report = diagnostics.check()

    assert not report.ok
    assert [result.name for result in report.checks] == [
        "python_bindings",
        "gdal_versions",
        "supported_gdal_version",
        "pmtiles_driver",
        "pmtiles_capabilities",
        "pmtiles_smoke",
    ]
    assert report.checks[0].observed["error"] == "ImportError"
    assert all(not result.ok for result in report.checks)
    assert json.loads(json.dumps(report.to_dict(), sort_keys=True)) == report.to_dict()


def test_check_reports_partially_importable_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import failure in OGR or OSR is reported without exposing file paths."""
    imported: list[str] = []

    def import_module(name: str) -> object:
        imported.append(name)
        if name == "osgeo.osr":
            raise OSError("native library mismatch")
        return object()

    monkeypatch.setattr(diagnostics, "import_module", import_module)

    report = diagnostics.check()

    assert not report.ok
    assert imported == ["osgeo.gdal", "osgeo.ogr", "osgeo.osr"]
    assert report.checks[0].observed["error"] == "OSError"


def test_check_does_not_import_gdal_until_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the public package never initializes the diagnostic loader."""
    calls: list[str] = []

    def fail_if_called() -> tuple[object, object, object]:
        calls.append("called")
        raise AssertionError("GDAL diagnostics ran during package import")

    monkeypatch.setattr(diagnostics, "_load_osgeo_modules", fail_if_called)
    importlib.reload(gpm)

    assert calls == []


def test_successful_binding_report_omits_module_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful reports contain stable status values, not local module paths."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal()),
    )
    monkeypatch.setattr(diagnostics, "_smoke_check", _successful_smoke)

    report = diagnostics.check()

    assert report.checks[0].observed == {
        "gdal": "imported",
        "ogr": "imported",
        "osr": "imported",
    }


def test_check_reports_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismatched binding and native versions fail without running the smoke test."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal(binding="3.11.4", native="3.12.2")),
    )
    smoke_called = False

    def smoke() -> diagnostics.CheckResult:
        nonlocal smoke_called
        smoke_called = True
        return _successful_smoke()

    monkeypatch.setattr(diagnostics, "_smoke_check", smoke)

    report = diagnostics.check()

    assert not report.ok
    assert not report.checks[1].ok
    assert report.checks[-1].message.startswith("Not run:")
    assert not smoke_called


def test_check_version_values_omit_private_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Version metadata is canonicalized before it reaches a support report."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(
            _FakeGdal(
                binding="3.12.2 /Users/alice/.venv token=secret",
                native="3.12.2 HOME=/Users/alice",
            )
        ),
    )
    monkeypatch.setattr(diagnostics, "_smoke_check", _successful_smoke)

    rendered = json.dumps(diagnostics.check().to_dict())

    assert "3.12.2" in rendered
    assert "/Users/alice" not in rendered
    assert "token=secret" not in rendered
    assert "HOME=" not in rendered


def test_check_reports_unsupported_gdal_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GDAL versions older than the documented range are rejected."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal(binding="3.7.3", native="3.7.3")),
    )
    monkeypatch.setattr(diagnostics, "_smoke_check", _successful_smoke)

    report = diagnostics.check()

    assert not report.ok
    assert not report.checks[2].ok
    assert report.checks[-1].message.startswith("Not run:")


def test_check_reports_missing_pmtiles_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent PMTiles driver has a focused failure result."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal(driver=None)),
    )
    monkeypatch.setattr(diagnostics, "_smoke_check", _successful_smoke)

    report = diagnostics.check()

    assert not report.ok
    assert not report.checks[3].ok
    assert report.checks[4].message.startswith("Not run:")


def test_check_reports_driver_metadata_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected driver metadata errors remain failed report entries."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal()),
    )
    monkeypatch.setattr(
        diagnostics,
        "_driver_check",
        lambda _gdal: (_ for _ in ()).throw(RuntimeError("driver registry failed")),
    )

    report = diagnostics.check()

    assert not report.ok
    assert report.checks[3].observed["error"] == "RuntimeError"
    assert report.checks[4].message.startswith("Not run:")
    assert report.checks[-1].message.startswith("Not run:")


def test_check_reports_non_creating_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-only driver cannot pass the write smoke check."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal(driver=_FakeDriver(create="NO"))),
    )
    monkeypatch.setattr(diagnostics, "_smoke_check", _successful_smoke)

    report = diagnostics.check()

    assert not report.ok
    assert not report.checks[4].ok
    assert report.checks[-1].message.startswith("Not run:")


def test_check_reports_smoke_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real-write failure is returned in the final result."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: _fake_modules(_FakeGdal()),
    )
    monkeypatch.setattr(
        diagnostics,
        "_smoke_check",
        lambda: diagnostics.CheckResult(
            "pmtiles_smoke",
            False,
            {"error": "RuntimeError: write failed"},
            "The in-memory PMTiles write and independent reopen/decode smoke test failed.",
            "Install a complete GDAL build from conda-forge.",
        ),
    )

    report = diagnostics.check()

    assert not report.ok
    assert not report.checks[-1].ok
    assert report.checks[-1].observed["error"] == "RuntimeError: write failed"


def test_check_error_output_omits_local_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native loader errors cannot leak paths through the JSON report."""
    monkeypatch.setattr(
        diagnostics,
        "_load_osgeo_modules",
        lambda: (_ for _ in ()).throw(
            OSError(
                "dlopen(/Users/alice/.venv/lib/python/osgeo/_gdal.so): image not found"
            )
        ),
    )

    report = diagnostics.check()
    rendered = json.dumps(report.to_dict())

    assert report.checks[0].observed == {"error": "OSError"}
    assert "/Users/alice" not in rendered


def test_assertion_error_in_diagnostic_failures() -> None:
    """AssertionError is included so MVT decoder failures don't escape check()."""
    assert AssertionError in diagnostics._DIAGNOSTIC_FAILURES


@pytest.mark.integration
def test_smoke_check_catches_assertion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AssertionError from the MVT protobuf decoder is caught as a failure."""
    pytest.importorskip("geopandas")
    pytest.importorskip("pmtiles")
    monkeypatch.setattr(
        diagnostics,
        "_mvt_has_property",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected non-bytes value in protobuf decoder")
        ),
    )

    result = diagnostics._smoke_check()

    assert not result.ok
    assert result.observed == {"error": "AssertionError"}


def test_cli_human_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Human CLI output explains failed checks and returns a nonzero exit code."""
    report = diagnostics.CheckReport(False, (_successful_smoke(),))
    monkeypatch.setattr(cli, "check", lambda: report)

    assert cli.main(["check"]) == 1

    assert "[ok] pmtiles_smoke" in capsys.readouterr().out


def test_cli_json_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON CLI output is stable, machine-readable, and successful when usable."""
    report = diagnostics.CheckReport(True, (_successful_smoke(),))
    monkeypatch.setattr(cli, "check", lambda: report)

    assert cli.main(["check", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == report.to_dict()


def test_cli_help_does_not_call_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI help remains available when GDAL is not installed."""
    monkeypatch.setattr(
        cli,
        "check",
        lambda: (_ for _ in ()).throw(AssertionError("diagnostics should not run")),
    )

    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"])


@pytest.mark.integration
def test_real_gdal_diagnostics_smoke() -> None:
    """A complete GDAL runtime passes the public diagnostics smoke test."""
    pytest.importorskip("osgeo.gdal")

    report = gpm.check()

    assert report.ok, report.to_dict()
