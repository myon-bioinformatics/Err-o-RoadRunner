"""Sentinel wrapper: idle monitoring, escalation, redaction, and fail-safe behaviour."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from junit_redact import find_corpus_leaks
from sentinel import (
    EXIT_GO,
    EXIT_STOP,
    SCHEMA_VERSION,
    RingBuffer,
    build_escalation_report,
    is_go_signal,
    is_stop_signal,
    run_sentinel,
)
from sentinel import RunOutcome, WatchState

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
README_STDERR = FIX / "readme" / "inputs" / "python_module_not_found.stderr"
ACTIONS_LOG = FIX / "actions_logs" / "two_failing_steps.log"


def _run_sentinel(
    *cmd: str,
    env: dict[str, str] | None = None,
    **kwargs: str,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "triage", "sentinel"]
    for key, value in kwargs.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value:
            argv.extend([flag, value])
    argv.extend(["--", *cmd])
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=ROOT,
        check=False,
    )


@pytest.mark.sentinel
def test_success_does_not_write_report_json(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel("true", report_json=str(report_path))
    assert proc.returncode == 0
    assert not report_path.exists()


@pytest.mark.sentinel
def test_exit_zero_does_not_invoke_triage_classifier() -> None:
    with mock.patch("sentinel.triage") as triage_mock:
        code = run_sentinel(["--", "true"])
    assert code == 0
    triage_mock.assert_not_called()


@pytest.mark.sentinel
def test_junit_not_scanned_on_exit_zero_without_junit_flag() -> None:
    with mock.patch("sentinel.junit_has_failures") as junit_mock:
        assert run_sentinel(["--", "true"]) == 0
    junit_mock.assert_not_called()


@pytest.mark.sentinel
def test_exit_mode_signal_stop_and_go() -> None:
    assert run_sentinel(["--exit-mode", "signal", "--", "true"]) == EXIT_STOP
    assert (
        run_sentinel(["--exit-mode", "signal", "--", "sh", "-c", "exit 3"])
        == EXIT_GO
    )


@pytest.mark.sentinel
def test_python_failure_escalates_json_and_triage(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    secret = "ghp_" + "A" * 40
    env = {"GITHUB_TOKEN": secret, "PYTHONPATH": ""}
    script = (
        "import sys\n"
        "raise ModuleNotFoundError(\"No module named 'totally_missing_pkg_xyz'\")\n"
    )
    proc = _run_sentinel(
        sys.executable,
        "-c",
        script,
        report_json=str(report_path),
        print_triage=True,
        env=env,
    )
    assert proc.returncode != 0
    assert report_path.is_file()
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["mode"] == "escalated"
    assert "non_zero_exit" in doc["escalation_reasons"]
    assert "ModuleNotFoundError" in "\n".join(doc["triage"]["found"])
    assert "FOUND: python exception type: ModuleNotFoundError" in proc.stdout
    blob = report_path.read_text(encoding="utf-8") + proc.stdout + proc.stderr
    assert secret not in blob
    assert not find_corpus_leaks(blob)


@pytest.mark.sentinel
def test_node_failure_via_log_fixture(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        "node",
        "-e",
        "console.error('Error: boom'); process.exit(1)",
        report_json=str(report_path),
        print_triage=True,
    )
    assert proc.returncode == 1
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    joined = "\n".join(doc["triage"]["found"] + doc["triage"]["hints"])
    assert "node" in joined.lower() or "exit code" in joined


@pytest.mark.sentinel
def test_timeout_escalation(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        timeout="0.3",
        report_json=str(report_path),
    )
    assert proc.returncode != 0
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["timed_out"] is True
    assert "timeout" in doc["escalation_reasons"]


@pytest.mark.sentinel
def test_junit_failure_with_zero_exit(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase name="t"><failure message="nope"/></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        "true",
        junit=str(junit),
        report_json=str(report_path),
    )
    assert proc.returncode == 0
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["junit_failure"] is True
    assert "junit_failure" in doc["escalation_reasons"]


@pytest.mark.sentinel
def test_actions_log_included_in_escalation(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        "false",
        actions_log=str(ACTIONS_LOG),
        report_json=str(report_path),
        print_triage=True,
    )
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    found = "\n".join(doc["triage"]["found"])
    assert "actions log" in found


@pytest.mark.sentinel
def test_escalation_failure_preserves_child_exit_code(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    with mock.patch(
        "sentinel.build_escalation_report",
        side_effect=RuntimeError("sentinel broke"),
    ):
        code = run_sentinel(
            ["--report-json", str(report_path), "--", "sh", "-c", "exit 7"],
        )
    assert code == 7


@pytest.mark.sentinel
def test_ring_buffer_trims_old_bytes() -> None:
    ring = RingBuffer(20)
    ring.append(b"0123456789", "stdout")
    ring.append(b"abcdefghij", "stdout")
    text = ring.text()
    assert len(text.encode()) <= 20
    assert text.endswith("abcdefghij") or "abc" in text


@pytest.mark.sentinel
def test_success_overhead_bounded() -> None:
    """In-process success path should stay cheap (no triage, no JSON)."""
    start = time.perf_counter()
    for _ in range(8):
        assert run_sentinel(["--", "true"]) == 0
    elapsed = time.perf_counter() - start
    # Generous ceiling for shared CI runners; catches accidental escalation work.
    assert elapsed < 8.0


@pytest.mark.sentinel
def test_diagnostic_hook_runs_on_escalation(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    hook_marker = tmp_path / "hook.ran"
    proc = _run_sentinel(
        "false",
        report_json=str(report_path),
        hook=f"touch {hook_marker}",
    )
    assert proc.returncode != 0
    assert hook_marker.is_file()
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc.get("diagnostic_hooks")


@pytest.mark.sentinel
def test_build_escalation_from_fixture_stderr() -> None:
    stderr = README_STDERR.read_text(encoding="utf-8")
    state = WatchState(
        command_argv=[sys.executable, "-m", "triage"],
        stdout_tail=RingBuffer(4096),
        stderr_tail=RingBuffer(4096),
    )
    state.stderr_tail.append(stderr.encode(), "stderr")
    outcome = RunOutcome(exit_code=1)
    doc = build_escalation_report(
        state,
        outcome,
        project_root=ROOT,
        junit_paths=[],
        actions_log_path=None,
        hooks=[],
        hook_timeout=5,
    )
    hints = doc["triage"]["hints"]
    assert hints, "expected HINT floor for standard Python stderr"
    assert not any(
        ln == "recognized failure pattern in log/stderr"
        for ln in doc["triage"]["not_found"]
    )


@pytest.mark.sentinel
def test_signal_escalation(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        sys.executable,
        "-c",
        "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
        report_json=str(report_path),
    )
    assert proc.returncode != 0
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["signal"] == "SIGTERM"
    assert any(r.startswith("signal:") for r in doc["escalation_reasons"])


@pytest.mark.sentinel
def test_stop_and_go_signal_matrix() -> None:
    assert is_go_signal(RunOutcome(exit_code=1), junit_failure=False)
    assert is_stop_signal(RunOutcome(exit_code=0), junit_failure=False)
    assert is_go_signal(RunOutcome(exit_code=0, timed_out=True), junit_failure=False)
    assert is_go_signal(RunOutcome(exit_code=0), junit_failure=True)
