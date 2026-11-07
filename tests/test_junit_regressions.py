"""JUnit parser regressions from PR #4 review."""

from __future__ import annotations

from pathlib import Path

import pytest

from triage import triage

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


# --- 2026-11-07 corpus feedback: the "partial" classes measured over 458 harvested cases ---

def _junit_lines(xml_text: str, tmp_path: Path) -> list[str]:
    path = tmp_path / "case.xml"
    path.write_text(xml_text, encoding="utf-8")
    report = triage(exit_code=1, junit_paths=[str(path)], stderr_text=None, log_text=None,
                    actions_log_text=None, project_root=EMPTY)
    return report.lines()


def _suite(*testcases: str) -> str:
    body = "".join(testcases)
    return f'<testsuite name="pytest" tests="{len(testcases)}" failures="{len(testcases)}">{body}</testsuite>'


PLAIN_ASSERT = _suite(
    '<testcase classname="tests.test_sum" name="test_total">'
    '<failure message="assert 12 == 6">def test_total():\n'
    '&gt;       assert total([3, 3]) == 6\nE       assert 12 == 6\n\n'
    'tests/test_sum.py:60: AssertionError</failure></testcase>'
)
DID_NOT_RAISE = _suite(
    '<testcase classname="tests.test_io" name="test_exists">'
    '<failure message="Failed: DID NOT RAISE &lt;class \'FileExistsError\'&gt;">'
    'tests\\test_io.py:311: in test_exists\n    with pytest.raises(FileExistsError):\n'
    'E   Failed: DID NOT RAISE &lt;class \'FileExistsError\'&gt;</failure></testcase>'
)
PLAYWRIGHT = _suite(
    '<testcase classname="home.spec.ts" name="Home › draw opens Door">'
    '<failure message="home.spec.ts:125:7 draw opens Door" type="expect.toBeVisible">'
    '[chromium] › home.spec.ts:125:7 › Home › draw opens Door\n\n'
    'Error: expect(locator).toBeVisible() failed\n\nLocator: locator(\'[role="menuitem"]\').first()\n'
    'Expected: visible\nTimeout: 3000ms\n\nCall log:\n  - Expect "toBeVisible" with timeout 3000ms\n'
    '  - waiting for locator(\'[role="menuitem"]\').first()\n\n    at home.spec.ts:131:51</failure></testcase>',
    '<testcase classname="home.spec.ts" name="Home › draw opens Door (webkit)">'
    '<failure message="home.spec.ts:125:7 draw opens Door" type="expect.toBeVisible">'
    '[webkit] › home.spec.ts:125:7 › Home › draw opens Door\n\nError: expect(locator).toBeVisible() failed\n</failure></testcase>'
)
FLUTTER = (
    '<testsuite name="flutter" tests="1" errors="1"><testcase classname="/w/test/screens/generic_screen_test.dart" '
    'name="Screen3 compact template is reachable above Door"><error message="Flutter test error">'
    'Test failed. See exception logs above.\nThe test description was: Screen3 compact template is reachable above Door'
    '</error></testcase></testsuite>'
)
IMPORT_MISMATCH = (
    '<testsuite name="pytest" tests="1" errors="1"><testcase classname="blueprobe.tests.test_vendor_lock" name="">'
    '<error message="collection failure">import file mismatch:\n'
    "imported module 'test_vendor_lock' has this __file__ attribute:\n  /w/tests/test_vendor_lock.py\n"
    'which is not the same as the test file we want to collect:\n  /w/blueprobe/tests/test_vendor_lock.py\n'
    'HINT: remove __pycache__ / .pyc files and/or use a unique basename for your test file modules'
    '</error></testcase></testsuite>'
)


def test_plain_pytest_assert_reports_assertion_error_type(tmp_path: Path) -> None:
    lines = _junit_lines(PLAIN_ASSERT, tmp_path)
    assert any(ln.startswith("FOUND: junit pytest exception type (") and ln.endswith(": AssertionError") for ln in lines)
    assert not any(ln.startswith("NOT FOUND: junit pytest exception type") for ln in lines)


def test_did_not_raise_is_typed_and_explained(tmp_path: Path) -> None:
    lines = _junit_lines(DID_NOT_RAISE, tmp_path)
    assert any(ln.endswith("junit pytest exception type (" + str(tmp_path / "case.xml") + "): Failed") for ln in lines)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit pytest outcome ("))
    assert "pytest.raises(FileExistsError) finished without an exception (DID NOT RAISE)" in hint


def test_playwright_block_is_located_typed_and_hinted(tmp_path: Path) -> None:
    lines = _junit_lines(PLAYWRIGHT, tmp_path)
    assert any(ln.startswith("FOUND: junit playwright test (") and ln.endswith("[chromium] home.spec.ts:125:7 › Home › draw opens Door") for ln in lines)
    assert any(ln.endswith("junit playwright error (" + str(tmp_path / "case.xml") + "): Error: expect(locator).toBeVisible() failed") for ln in lines)
    assert any(ln.startswith("HINT: junit playwright call log (") for ln in lines)
    assert any(ln.startswith("HINT: junit framework guess (") and ln.endswith("playwright") for ln in lines)
    # The JUnit type attribute names the group when nothing in the text does.
    assert any("junit failure group 1/1 (" in ln and ": 2× expect.toBeVisible: " in ln for ln in lines)


def test_flutter_junit_description_and_redirect_to_test_events(tmp_path: Path) -> None:
    lines = _junit_lines(FLUTTER, tmp_path)
    assert any(ln.endswith("junit flutter test description (" + str(tmp_path / "case.xml") + "): Screen3 compact template is reachable above Door") for ln in lines)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit flutter detail ("))
    assert "--test-events" in hint and "EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK" in hint


def test_import_file_mismatch_hint_names_both_paths(tmp_path: Path) -> None:
    lines = _junit_lines(IMPORT_MISMATCH, tmp_path)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit collection ("))
    assert "module 'test_vendor_lock' is already imported from /w/tests/test_vendor_lock.py" in hint
    assert "collection wants /w/blueprobe/tests/test_vendor_lock.py" in hint
    assert "--import-mode=importlib" in hint


def test_unrelated_failures_keep_old_output(tmp_path: Path) -> None:
    xml = _suite('<testcase classname="t" name="x"><failure message="boom">RuntimeError: boom</failure></testcase>')
    lines = _junit_lines(xml, tmp_path)
    assert not any("playwright" in ln or "flutter" in ln or "pytest outcome" in ln or "junit collection (" in ln for ln in lines)
