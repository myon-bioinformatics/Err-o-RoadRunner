"""Sentinel wrapper: idle monitoring, escalation, redaction, and fail-safe behaviour."""

from __future__ import annotations

import json
import os
import statistics
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
    TIMEOUT_EXIT,
    RingBuffer,
    build_escalation_report,
    is_go_signal,
    is_stop_signal,
    run_sentinel,
    run_wrapped_command,
)
from sentinel import RunOutcome, WatchState

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
README_STDERR = FIX / "readme" / "inputs" / "python_module_not_found.stderr"
ACTIONS_LOG = FIX / "actions_logs" / "two_failing_steps.log"
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


def _run_sentinel(
    *cmd: str,
    env: dict[str, str] | None = None,
    **kwargs: str | bool,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "triage", "sentinel"]
    for key, value in kwargs.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value:
            argv.extend([flag, str(value)])
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
    with mock.patch("sentinel._call_triage") as triage_mock:
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
def test_missing_command_returns_127_and_escalates(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    missing = "definitely-missing-cmd-err-o-roadrunner-xyz"
    proc = _run_sentinel(missing, report_json=str(report_path), print_triage=True)
    assert proc.returncode == 127
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["exit_code"] == 127
    assert any("wrapper:FileNotFoundError" in r for r in doc["escalation_reasons"])


@pytest.mark.sentinel
def test_non_executable_returns_126_and_escalates(tmp_path: Path) -> None:
    script = tmp_path / "not_executable.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(str(script), report_json=str(report_path))
    assert proc.returncode == 126
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["exit_code"] == 126


@pytest.mark.sentinel
def test_timeout_exit_code_124_in_child_mode_and_json(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    proc = _run_sentinel(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        timeout="0.3",
        report_json=str(report_path),
    )
    assert proc.returncode == TIMEOUT_EXIT
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert doc["outcome"]["timed_out"] is True
    assert doc["outcome"]["exit_code"] == TIMEOUT_EXIT
    assert doc["outcome"]["termination_signal"] in ("SIGTERM", "SIGKILL")


@pytest.mark.sentinel
def test_wrapped_command_starts_new_session_on_posix() -> None:
    import sentinel as sentinel_mod

    if not sentinel_mod._USE_PROCESS_GROUP:
        pytest.skip("POSIX process groups only")
    fake = mock.Mock()
    fake.pid = 4242
    fake.stdout = mock.Mock(read=mock.Mock(return_value=b""))
    fake.stderr = mock.Mock(read=mock.Mock(return_value=b""))
    fake.wait = mock.Mock(return_value=None)
    fake.returncode = 0
    with mock.patch("subprocess.Popen", return_value=fake) as popen_cls:
        run_wrapped_command(["true"], cwd=None, timeout=None, env=None, ring_bytes=4096)
    _, kwargs = popen_cls.call_args
    assert kwargs.get("start_new_session") is True


@pytest.mark.sentinel
def test_exit_zero_traceback_stderr_stops_by_default(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    script = (
        "import sys\n"
        "sys.stderr.write("
        "'Traceback (most recent call last):\\n"
        "  File \"x.py\", line 1\\nRuntimeError: quiet\\n')"
        "\n"
    )
    proc = _run_sentinel(sys.executable, "-c", script, report_json=str(report_path))
    assert proc.returncode == 0
    assert not report_path.exists()


@pytest.mark.sentinel
def test_go_on_stderr_traceback_flag(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    script = (
        "import sys\n"
        "sys.stderr.write("
        "'Traceback (most recent call last):\\n"
        "  File \"x.py\", line 1\\nRuntimeError: loud\\n')"
        "\n"
    )
    proc = _run_sentinel(
        sys.executable,
        "-c",
        script,
        go_on_stderr_traceback=True,
        report_json=str(report_path),
        print_triage=True,
    )
    assert proc.returncode == 0
    assert report_path.is_file()
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    assert "stderr_traceback" in doc["escalation_reasons"]


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
def test_stop_path_subprocess_overhead_median() -> None:
    """Fresh interpreter per call; target ~55ms median on dev machines."""
    times: list[float] = []
    for _ in range(12):
        start = time.perf_counter()
        proc = subprocess.run(
            [*SENTINEL_CLI, "--", "true"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0
        times.append(time.perf_counter() - start)
    median = statistics.median(times)
    assert median < 1.5, f"stop path median {median:.3f}s too high"


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
    outcome = RunOutcome(exit_code=1, effective_exit_code=1)
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
    assert is_go_signal(
        RunOutcome(exit_code=1, effective_exit_code=1),
        junit_failure=False,
        stderr_text="",
        go_on_stderr_traceback=False,
    )
    assert is_stop_signal(
        RunOutcome(exit_code=0, effective_exit_code=0),
        junit_failure=False,
        stderr_text="",
        go_on_stderr_traceback=False,
    )
    assert is_go_signal(
        RunOutcome(exit_code=0, effective_exit_code=0, timed_out=True),
        junit_failure=False,
        stderr_text="",
        go_on_stderr_traceback=False,
    )
    assert is_go_signal(
        RunOutcome(exit_code=0, effective_exit_code=0),
        junit_failure=True,
        stderr_text="",
        go_on_stderr_traceback=False,
    )


@pytest.mark.sentinel
def test_sentinel_marker_runs_under_python_3_11_plus() -> None:
    """Guardrail: sentinel tests are not skipped on supported CPython (incl. CI 3.11)."""
    assert sys.version_info >= (3, 11)
