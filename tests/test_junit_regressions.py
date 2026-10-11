"""JUnit parser regressions from PR #4 review."""

from __future__ import annotations

from pathlib import Path

import pytest

from err_o_roadrunner.triage import triage

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"


@pytest.mark.junit_corpus
def test_aoi_pytest_collection_failure_is_found() -> None:
    xml = CORPUS / "cases" / "Aoi_37559206978_junit-py3.11_100.xml"
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    lines = report.lines()
    assert any(ln.startswith("FOUND: junit collection errors (") for ln in lines)
    assert any(
        "collection failure" in ln or "ImportError while importing" in ln
        for ln in lines
        if ln.startswith("FOUND: junit collection error (first)")
    )


@pytest.mark.gap
def test_junit_setup_error_is_not_collection_error() -> None:
    xml = CORPUS / "inputs" / "setup_error.junit.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        junit_paths=[str(xml)],
        project_root=EMPTY,
    )
    lines = report.lines()
    assert any(ln.startswith("FOUND: junit errors") for ln in lines)
    assert any(ln.startswith("NOT FOUND: junit collection errors (") for ln in lines)


@pytest.mark.junit_corpus
@pytest.mark.parametrize(
    "case_id,expected_substr",
    [
        (
            "flutter_navigation_basic_38139683688_junit-flutter-core-0_78",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683688_junit-flutter-core-1_77",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683671_junit-flutter-refactor_74",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683671_junit-flutter-refactor_75",
            ".dart:",
        ),
    ],
)
def test_junit_flutter_corpus_dart_error(case_id: str, expected_substr: str) -> None:
    xml = CORPUS / "cases" / f"{case_id}.xml"
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    dart_lines = [
        ln
        for ln in report.lines()
        if ln.startswith("FOUND: junit dart error (first)") and expected_substr in ln
    ]
    assert len(dart_lines) == 1
    joined = "\n".join(report.lines())
    assert not any("dart:core" in ln and ln.startswith("HINT:") for ln in report.lines())


@pytest.mark.junit_corpus
def test_junit_flutter_xml_run_38139681545_golden() -> None:
    from tests.report_assert import assert_report_has_exact_line

    xml = CORPUS / "cases" / "flutter_navigation_basic_38139681545_flutter.xml"
    assert xml.is_file()
    label = str(xml).replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    lines = [ln.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>") for ln in report.lines()]
    dart = [ln for ln in lines if ln.startswith("FOUND: junit dart error (first)")]
    assert len(dart) == 1
    assert ".dart:" in dart[0] and "Error:" in dart[0]
    assert any(ln.startswith("NOT FOUND: junit collection errors (") for ln in lines)
