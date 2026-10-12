"""GitHub Actions log parsing tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from actions_log import normalize_actions_log_line, preprocess_actions_log
from triage import MAX_READ_BYTES, triage
from tests.actions_log_golden_util import normalize_report, triage_actions_log, triage_actions_log_cli
from tests.report_assert import assert_report_has_exact_line

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "actions_logs"
REAL = FIX / "real"
REAL_MANIFEST = REAL / "manifest.json"


def _lines(report) -> list[str]:
    return report.lines()


@pytest.mark.actions
def test_actions_log_parses_timestamps_groups_and_exit_code() -> None:
    text = (FIX / "python_fail.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        actions_log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert_report_has_exact_line(lines, "FOUND: actions log failing step (actions-log): pytest -q")
    assert_report_has_exact_line(lines, "FOUND: actions log process exit code (actions-log): 1")
    assert any(ln == "FOUND: python exception type: AssertionError" for ln in lines)


@pytest.mark.actions
def test_actions_log_workflow_error_command() -> None:
    text = (FIX / "workflow_error.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert_report_has_exact_line(
        lines, "FOUND: actions log workflow error (first) (actions-log): compile failed"
    )


@pytest.mark.actions
def test_actions_log_bom_and_ansi_stripped() -> None:
    text = (FIX / "bom_ansi.log").read_text(encoding="utf-8-sig")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    joined = "\n".join(_lines(report))
    assert "\x1b[" not in joined
    assert_report_has_exact_line(_lines(report), "FOUND: actions log process exit code (actions-log): 3")


@pytest.mark.actions
def test_actions_log_timestamp_strips_single_space_only() -> None:
    raw = "2026-10-11T13:00:01.1234567Z     leading spaces preserved"
    assert normalize_actions_log_line(raw) == "    leading spaces preserved"


@pytest.mark.actions
def test_actions_log_preprocess_crlf_and_bom() -> None:
    path = FIX / "crlf_bom.log"
    raw = path.read_bytes()
    assert b"\r\n" in raw
    text = raw.decode("utf-8-sig")
    records, is_gh = preprocess_actions_log(text)
    lines = [rec.text for rec in records]
    assert not is_gh
    assert all("\r" not in ln for ln in lines)
    assert lines[0].startswith("##[group]Run echo ok")
    assert any(ln == "##[error]Process completed with exit code 7." for ln in lines)


@pytest.mark.actions
def test_actions_log_cli_read_cap() -> None:
    import subprocess

    marker = b"ACTIONS_LOG_BEYOND_CAP"
    path = FIX / "_tmp_huge_actions.log"
    try:
        path.write_bytes(b"z" * MAX_READ_BYTES + marker)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "triage",
                "--exit-code",
                "1",
                "--actions-log",
                str(path),
                "--project-root",
                str(ROOT),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "ACTIONS_LOG_BEYOND_CAP" not in proc.stdout
        assert f"input truncated (size {MAX_READ_BYTES + len(marker)} > {MAX_READ_BYTES})" in proc.stdout
        assert marker.decode() not in proc.stdout
        assert proc.stdout.count("z") <= MAX_READ_BYTES
    finally:
        if path.exists():
            path.unlink()


@pytest.mark.actions
def test_actions_log_inline_error_marker_is_hint_not_failing_parse() -> None:
    text = (FIX / "inline_user_error.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln.startswith("HINT: actions log inline error marker") for ln in lines)
    assert_report_has_exact_line(lines, "FOUND: actions log process exit code (actions-log): 2")


@pytest.mark.actions
def test_actions_log_truncated_traceback_not_found_as_exception() -> None:
    text = (FIX / "midline_exception_fragment.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "NOT FOUND: python exception type" for ln in lines)


@pytest.fixture(scope="module")
def real_manifest() -> list[dict]:
    return json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.actions
@pytest.mark.parametrize(
    "entry",
    json.loads(REAL_MANIFEST.read_text(encoding="utf-8")) if REAL_MANIFEST.exists() else [],
    ids=lambda e: e["id"],
)
def test_actions_log_real_golden(entry: dict) -> None:
    log_path = REAL / entry["log"]
    golden_path = REAL / entry["golden"]
    assert log_path.is_file(), log_path
    assert golden_path.is_file(), golden_path
    expected = golden_path.read_text(encoding="utf-8")
    actual = normalize_report(triage_actions_log(log_path))
    assert actual == expected


@pytest.mark.actions
def test_actions_log_gh_run_view_named_step_column() -> None:
    text = (FIX / "gh_run_view_named_steps.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log format (actions-log): gh run view" for ln in lines)
    assert_report_has_exact_line(lines, "FOUND: actions log failing step (actions-log): ci / Run tests")
    assert_report_has_exact_line(
        lines,
        "FOUND: actions log pytest failed (first) (actions-log): FAILED tests/test_pr_status.py::test_cli_json_text_and_exclude_override - assert False",
    )


@pytest.mark.actions
def test_actions_log_empty_log_failed_file_is_not_found() -> None:
    text = (REAL / "empty_log_failed.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(
        ln in ("NOT FOUND: actions log content (actions-log)", "NOT FOUND: actions log text (empty)")
        for ln in lines
    )
    assert not any(ln.startswith("FOUND: actions log failing step") for ln in lines)


@pytest.mark.actions
def test_gh_run_view_line_with_embedded_tab_not_extra_prefix() -> None:
    text = (
        "job\tUNKNOWN STEP\t2026-10-11T12:00:00.0000000Z ##[group]Run checkout\n"
        "job\tUNKNOWN STEP\t2026-10-11T12:00:00.1000000Z hint: \tgit branch\n"
        "job\tUNKNOWN STEP\t2026-10-11T12:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    records, is_gh = preprocess_actions_log(text)
    assert is_gh
    assert records[1].text == "hint: \tgit branch"


@pytest.mark.actions
def test_substring_found_does_not_match_not_found_regression() -> None:
    """Earlier tests used `FOUND: foo in joined`, which also matched NOT FOUND: foo."""
    lines = [
        "FOUND: exit code: 1",
        "NOT FOUND: actions log failing step (actions-log)",
    ]
    needle = "FOUND: actions log failing step"
    assert not any(ln == needle for ln in lines)
    assert any(ln.startswith("NOT FOUND: actions log failing step") for ln in lines)


@pytest.mark.actions
def test_capture_real_goldens_script_smoke() -> None:
    """Guardrail: real goldens must be generated from triage_actions_log_cli."""
    entry = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))[0]
    log_path = REAL / entry["log"]
    cli = normalize_report(triage_actions_log_cli(log_path))
    api = normalize_report(triage_actions_log(log_path))
    assert cli == api
