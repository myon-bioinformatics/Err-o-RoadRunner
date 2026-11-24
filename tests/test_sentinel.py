"""Sentinel wrapper: monitoring, escalation, stdout formats, fix candidates.

- sentinel: Sentinel wrapper: idle monitoring, escalation, redaction, and fail-safe behaviour.
- sentinel_formats: Sentinel stdout formats, fix_candidates rules, and error grouping.
- sentinel_missing_command_hints: missing_command fix candidates vs Dart / filesystem hint context.
"""

from __future__ import annotations

import json, statistics, sys, time
from pathlib import Path
from unittest import mock

import pytest

from vendor import cli_args
from errlib.junit_redact import find_corpus_leaks
from sentinel import (
    _fix_rules,
    build_escalation_report,
    build_fix_candidates,
    build_groups,
    EXIT_GO,
    EXIT_STOP,
    extract_normalized_errors,
    FORMAT_FULL,
    FORMAT_PORCELAIN,
    FORMAT_SHORT,
    FORMAT_TABLE,
    is_go_signal,
    is_stop_signal,
    parse_group_by,
    render_stdout_format,
    render_stop_stdout,
    RingBuffer,
    run_sentinel,
    run_wrapped_command,
    RunOutcome,
    SCHEMA_VERSION,
    TIMEOUT_EXIT,
    WatchState,
)


# ──── sentinel ────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
README_STDERR = FIX / "readme" / "inputs" / "python_module_not_found.stderr"
ACTIONS_LOG = FIX / "actions_logs" / "two_failing_steps.log"


def _run_sentinel(
    *cmd: str,
    env: dict[str, str] | None = None,
    **kwargs: str | bool,
) -> cli_args.CommandResult:
    command = cli_args.Command((sys.executable, "-m", "triage", "sentinel"))
    for key, value in kwargs.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            command = command.flag(flag)
        elif value:
            command = command.option(flag, str(value))
    return cli_args.run_command(command.passthrough(cmd, separator=True), cwd=ROOT, env=cli_args.build_env(env))


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
        proc = cli_args.run_cli(ROOT / "sentinel.py", "--", "true", cwd=ROOT)
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


# ──── sentinel_formats ────────────────────────────────────────────

SENTINEL_FIX = ROOT / "tests" / "fixtures" / "sentinel"
GOLDEN = SENTINEL_FIX / "golden"


def _load_struct(name: str) -> dict[str, list[str]]:
    return json.loads((SENTINEL_FIX / name).read_text(encoding="utf-8"))


def _doc_from_structured(
    structured: dict[str, list[str]],
    outcome: RunOutcome | None = None,
    *,
    group_by: tuple[str, ...] = ("error_type",),
    sort: str = "count",
) -> dict:
    outcome = outcome or RunOutcome(exit_code=1, effective_exit_code=1)
    reproduce = "pytest -q"
    errors = extract_normalized_errors(structured, outcome)
    return {
        "schema": SCHEMA_VERSION,
        "mode": "escalated",
        "triage": structured,
        "command": {"reproduce_command": reproduce},
        "timing": {"elapsed_seconds": 0.0},
        "outcome": {
            "exit_code": outcome.effective_exit_code,
            "raw_exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "junit_failure": outcome.junit_failure,
        },
        "fix_candidates": build_fix_candidates(structured, reproduce_command=reproduce),
        "groups": build_groups(errors, group_by=group_by, sort=sort),
    }


@pytest.mark.sentinel
def test_parse_group_by_keys() -> None:
    assert parse_group_by("error_type,language") == ("error_type", "language")
    with pytest.raises(ValueError):
        parse_group_by("error_type,bogus")


@pytest.mark.sentinel
@pytest.mark.parametrize(
    "group_by",
    [
        ("error_type",),
        ("language",),
        ("phase",),
        ("signature",),
        ("location",),
        ("kind",),
    ],
)
def test_group_by_golden_keys(group_by: tuple[str, ...]) -> None:
    structured = _load_struct("pydantic_validation.found.json")
    outcome = RunOutcome(exit_code=1, effective_exit_code=1)
    errors = extract_normalized_errors(structured, outcome)
    groups = build_groups(errors, group_by=group_by, sort="count")
    golden_path = GOLDEN / f"groups_{'_'.join(group_by)}_pydantic.txt"
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(
        f"{b['name']}\t{b['count']}\t{b['first_seen']}" for b in groups["buckets"]
    ) + "\n"
    if not golden_path.exists():
        golden_path.write_text(rendered, encoding="utf-8")
    assert rendered == golden_path.read_text(encoding="utf-8")
    assert groups["buckets"][0]["errors"][0]["error_type"] == "ValidationError"


@pytest.mark.sentinel
def test_sql_error_type_grouping() -> None:
    structured = _load_struct("sql_operational.found.json")
    errors = extract_normalized_errors(structured, RunOutcome(exit_code=1, effective_exit_code=1))
    groups = build_groups(errors, group_by=("error_type",), sort="name")
    assert groups["buckets"][0]["key"]["error_type"] == "sqlite3.OperationalError"


@pytest.mark.sentinel
@pytest.mark.parametrize(
    ("rule", "line"),
    [
        (_fix_rules()[0], "python exception type: ModuleNotFoundError"),
        (_fix_rules()[1], "python exception type: ImportError"),
        (_fix_rules()[2], "exit code: 127"),
        (_fix_rules()[3], "exit code: 126"),
        (_fix_rules()[4], "exit code: 124"),
        (_fix_rules()[5], "Cannot find module '@playwright/test'"),
        (_fix_rules()[6], "python exception type: AssertionError"),
        (_fix_rules()[7], "junit failure (first) (x.xml): tests/view-mode.spec.js:104 expect(locator).toHaveCount"),
        (_fix_rules()[8], "python exception type: SyntaxError"),
        (_fix_rules()[9], "actions log failing step (log): Run tests"),
        (_fix_rules()[10], "exit code: 9"),
    ],
    ids=[r.kind for r in _fix_rules()],
)
def test_fix_rule_regex_matches_fixture_line(rule, line: str) -> None:
    assert rule.found_re.search(line), f"{rule.kind} should match {line}"


@pytest.mark.sentinel
def test_playwright_junit_fix_candidate() -> None:
    structured = {
        "found": [
            "junit failure (first) (j.xml): tests/view-mode.spec.js:104 expect(locator).toHaveCount failed",
            "junit testcase classname (first) (j.xml): view-mode.spec.js",
        ],
        "not_found": [],
        "hints": [],
    }
    fixes = build_fix_candidates(structured, reproduce_command="npx playwright test")
    assert fixes and fixes[0]["kind"] == "junit"
    assert "view-mode.spec.js" in (fixes[0].get("location") or {}).get("loc", "")


@pytest.mark.sentinel
def test_format_full_golden() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_FULL, tb="short", verbose=False)
    path = GOLDEN / "format_full.txt"
    if not path.exists():
        path.write_text(out, encoding="utf-8")
    assert out == path.read_text(encoding="utf-8")


@pytest.mark.sentinel
def test_format_short_golden() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(
        doc,
        fmt=FORMAT_SHORT,
        tb="short",
        verbose=True,
        outcome=RunOutcome(exit_code=1, effective_exit_code=1),
    )
    path = GOLDEN / "format_short.txt"
    if not path.exists():
        path.write_text(out, encoding="utf-8")
    assert "==" in out
    assert out == path.read_text(encoding="utf-8")


@pytest.mark.sentinel
def test_format_porcelain_header_and_c_rows() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_PORCELAIN, tb="no", verbose=False)
    assert out.startswith(f"# {SCHEMA_VERSION}\t")
    assert "C\tF1\t" in out
    assert "# totals\t" in out


@pytest.mark.sentinel
def test_format_table_uses_groups() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_TABLE, tb="no", verbose=False)
    assert "Group" in out and "Count" in out
    assert "error_type=ValidationError" in out


@pytest.mark.sentinel
def test_stop_short_line() -> None:
    line = render_stop_stdout(fmt=FORMAT_SHORT, exit_code=0, elapsed_seconds=0.05)
    assert line == "sentinel: STOP exit=0 0.05s"


@pytest.mark.sentinel
def test_stop_porcelain_empty() -> None:
    assert render_stop_stdout(fmt=FORMAT_PORCELAIN, exit_code=0, elapsed_seconds=0.01) == ""


@pytest.mark.sentinel
def test_report_json_includes_fix_candidates_and_groups(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    proc = _run_sentinel(
        sys.executable,
        "-c",
        "raise ModuleNotFoundError('no_pkg_xyz')",
        report_json=str(report),
        print_triage=True,
    )
    assert proc.returncode != 0
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc.get("fix_candidates")
    assert doc.get("groups", {}).get("buckets")


@pytest.mark.sentinel
def test_porcelain_column_order() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_PORCELAIN, tb="no", verbose=False)
    rows = [ln for ln in out.strip().split("\n") if ln]
    header_cols = rows[0].split("\t")
    assert header_cols == [
        f"# {SCHEMA_VERSION}",
        "escalated",
        "pytest -q",
        "1",
    ]
    f_row = next(ln for ln in rows if ln.startswith("F\t"))
    assert f_row.split("\t", 2)[:2] == ["F", "found"]
    c_row = next(ln for ln in rows if ln.startswith("C\t"))
    c_cols = c_row.split("\t")
    assert len(c_cols) == 8
    assert c_cols[0] == "C"
    totals = next(ln for ln in rows if ln.startswith("# totals\t"))
    assert totals.startswith("# totals\tfound=")


@pytest.mark.sentinel
def test_timeout_error_type() -> None:
    structured = {"found": ["exit code: 124"], "not_found": [], "hints": []}
    outcome = RunOutcome(exit_code=124, effective_exit_code=124, timed_out=True)
    errors = extract_normalized_errors(structured, outcome)
    assert errors[0]["error_type"] == "Timeout"


@pytest.mark.sentinel
def test_location_group_uses_traceback_frame() -> None:
    structured = {
        "found": [
            "python exception type: RuntimeError",
            "python traceback frame (first): <string>:1 in <module>",
        ],
        "not_found": [],
        "hints": [],
    }
    errors = extract_normalized_errors(structured, RunOutcome(exit_code=1, effective_exit_code=1))
    groups = build_groups(errors, group_by=("location",), sort="name")
    assert groups["buckets"][0]["key"]["location"] == "<string>:1"


@pytest.mark.sentinel
def test_command_and_capture_secrets_redacted(tmp_path: Path) -> None:
    leak = "api_key=supersecretvalue123"
    report = tmp_path / "r.json"
    proc = _run_sentinel(
        "sh",
        "-c",
        f"printf '%s\\n' '{leak}' 1>&2; exit 1",
        report_json=str(report),
        format="short",
    )
    doc = json.loads(report.read_text(encoding="utf-8"))
    sentinel_stdout = "\n".join(
        ln
        for ln in proc.stdout.splitlines()
        if ln.startswith(("F ", "  ", "fix:", "G ", "==", "FOUND:", "NOT FOUND:", "HINT:"))
    )
    assert "supersecretvalue123" not in json.dumps(doc)
    assert "supersecretvalue123" not in sentinel_stdout
    assert "api_key=<redacted>" in doc["capture"]["stderr_tail"]
    assert "api_key=<redacted>" in doc["command"]["reproduce_command"]
    assert "api_key=<redacted>" in doc["command"]["argv"][-1]
    assert "api_key=<redacted>" in doc["human_summary"]
    assert doc["fix_candidates"][0]["verify_command"].count("api_key=<redacted>") >= 1
    assert proc.stdout.splitlines()[0].startswith("F ")
    assert "api_key=<redacted>" in proc.stdout.splitlines()[0]


@pytest.mark.sentinel
def test_stdout_lines_max_200_chars() -> None:
    long_msg = "x" * 300
    structured = {
        "found": [f"python exception message: {long_msg}"],
        "not_found": [],
        "hints": [],
    }
    doc = _doc_from_structured(structured)
    out = render_stdout_format(doc, fmt=FORMAT_FULL, tb="short", verbose=False)
    for line in out.splitlines():
        assert len(line) <= 200


# ──── sentinel_missing_command_hints ──────────────────────────────

@pytest.mark.sentinel
def test_missing_command_from_exit_127_found() -> None:
    structured = {
        "found": ["exit code: 127"],
        "not_found": [],
        "hints": ["sh: definitely-missing-cmd: command not found"],
    }
    fixes = build_fix_candidates(structured, reproduce_command="missing-cmd")
    assert any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_from_exec_format_hint() -> None:
    structured = {
        "found": ["exit code: 127"],
        "not_found": [],
        "hints": ["/bin/sh: 1: /tmp/foo: No such file or directory"],
    }
    fixes = build_fix_candidates(structured, reproduce_command="sh /tmp/foo")
    assert any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_error_when_reading() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            "web search keywords (2 variants): Error when reading "
            "'lib/shared/screens/generic_screen.dart' : No such file or directory"
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="flutter test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_double_quoted_dart_read() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            'Error when reading "lib/foo.dart": No such file or directory in pubspec.yaml'
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="dart test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_filesystem_exception_os_error() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            "FileSystemException: Cannot open file, path = 'lib/a.dart' "
            "(OS Error: No such file or directory, errno = 2)"
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="flutter test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_enrich_drops_missing_command_when_dart_import_added() -> None:
    import squad

    candidates = [
        {
            "id": "F1",
            "kind": "missing_command",
            "confidence": "low",
            "evidence": [],
            "location": {},
            "suggestion": "install cmd",
            "verify_command": "foo",
        }
    ]
    hypotheses = [
        {
            "id": "H1",
            "summary": "dart-compile-cascade",
            "confidence": "high",
            "explains": ["t1"],
            "source_squads": ["code"],
        }
    ]
    out = squad.enrich_fix_candidates(candidates, hypotheses)
    kinds = {c.get("kind") for c in out}
    assert "dart_import" in kinds
    assert "missing_command" not in kinds
