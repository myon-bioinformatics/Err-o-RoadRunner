"""GitHub Actions log parsing tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from err_o_roadrunner.triage import triage

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "actions_logs"


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
    joined = "\n".join(report.lines())
    assert "FOUND: actions log failing step" in joined
    assert "Run pytest" in joined
    assert "FOUND: actions log process exit code" in joined
    assert "FOUND: python exception type: AssertionError" in joined


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
    joined = "\n".join(report.lines())
    assert "FOUND: actions log workflow error (first)" in joined
    assert "compile failed" in joined


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
    joined = "\n".join(report.lines())
    assert "\x1b[" not in joined
    assert "FOUND: actions log process exit code" in joined
