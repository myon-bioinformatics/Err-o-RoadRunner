"""Matrix hypotheses require the same failure, not only the same testcase."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from tests.benchmark_util import run_benchmark


def _junit(path: Path, failures: list[tuple[str, str]]) -> str:
    suite = ET.Element("testsuite", tests="1", failures=str(len(failures)))
    case = ET.SubElement(suite, "testcase", classname="matrix", name="test_shared")
    for error_type, message in failures:
        failure = ET.SubElement(case, "failure", type=error_type, message=f"{error_type}: {message}")
        failure.text = f"{error_type}: {message}"
    ET.ElementTree(suite).write(path, encoding="unicode")
    return str(path)


@pytest.mark.sentinel
@pytest.mark.parametrize(
    ("other_os", "other_python", "classification"),
    [("windows", "3.12", "os-specific"), ("ubuntu", "3.13", "python-specific")],
)
def test_no_squads_preserves_axis_specific_hypotheses(
    tmp_path: Path, other_os: str, other_python: str, classification: str,
) -> None:
    paths = [
        _junit(tmp_path / "test-report-ubuntu-latest-py3.12.xml", [("ValueError", "alpha")]),
        _junit(tmp_path / f"test-report-{other_os}-latest-py{other_python}.xml", []),
    ]
    short, report = run_benchmark(paths, squads="none")
    assert [hyp["classification"] for hyp in report["hypotheses"]] == [classification]
    assert report["squads"] == []
    assert "M fails on all" not in short


@pytest.mark.sentinel
@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("ValueError", "alpha"), ("ValueError", "beta")),
        (("ValueError", "shared message"), ("TypeError", "shared message")),
    ],
)
def test_different_failures_in_one_testcase_are_not_matrix_invariant(
    tmp_path: Path, first: tuple[str, str], second: tuple[str, str],
) -> None:
    paths = [
        _junit(tmp_path / "test-report-ubuntu-latest-py3.12.xml", [first]),
        _junit(tmp_path / "test-report-windows-latest-py3.12.xml", [second]),
    ]
    short, report = run_benchmark(paths, squads="none")
    # Preserve the existing public testcase grouping; classify its failures separately.
    assert len(report["records"]) == 1
    assert report["matrix"]["failing"] == 2
    assert [hyp["classification"] for hyp in report["hypotheses"]] == ["os-specific"]
    assert "M fails on all" not in short


@pytest.mark.sentinel
def test_shared_failure_remains_invariant_without_exposing_private_cells(tmp_path: Path) -> None:
    shared = ("ValueError", "shared message")
    paths = [
        _junit(tmp_path / "test-report-ubuntu-latest-py3.12.xml", [shared, ("TypeError", "other")]),
        _junit(tmp_path / "test-report-windows-latest-py3.12.xml", [shared]),
    ]
    short, report = run_benchmark(paths, squads="none")
    assert "invariant" in [hyp["classification"] for hyp in report["hypotheses"]]
    assert "M fails on all 2 variants" in short
    assert not any(key.startswith("matrix_") for record in report["records"] for key in record)
