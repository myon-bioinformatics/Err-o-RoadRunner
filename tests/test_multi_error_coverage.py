"""Best-effort multi-error coverage and HINT floor for standard error formats."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from err_o_roadrunner import actions_log
from err_o_roadrunner.triage import triage

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "actions_logs"
MULTI = ROOT / "tests" / "fixtures" / "multi_error"


def _lines(report) -> list[str]:
    return report.lines()


def _assert_standard_hint_floor(lines: list[str], *, location: str) -> None:
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)
    hints = [ln for ln in lines if ln.startswith("HINT:")]
    assert hints, "expected at least one HINT for standard error"
    joined = "\n".join(hints)
    assert location in joined or any(location in ln for ln in lines if ln.startswith("FOUND:"))


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_actions_two_failing_steps_collected() -> None:
    text = (FIX / "two_failing_steps.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log failing steps (actions-log): 2" for ln in lines)
    assert any("lint" in ln and "failing step 1/" in ln for ln in lines)
    assert any("tests" in ln and "failing step 2/" in ln for ln in lines)
    assert any("##[error] markers (actions-log): 2" in ln for ln in lines)
    assert any("file=src/app.ts,line=12" in ln for ln in lines)


@pytest.mark.multi_error
def test_actions_cancel_without_exit_code() -> None:
    text = (FIX / "cancel_no_exit_code.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("cancel or timeout" in ln for ln in lines)
    assert any(ln == "NOT FOUND: actions log process exit code (actions-log)" for ln in lines)
    _assert_standard_hint_floor(lines, location="canceled")  # American spelling in runner text


@pytest.mark.multi_error
def test_actions_continue_on_error_does_not_mask_real_failure() -> None:
    text = (FIX / "continue_on_error_step.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("[continue-on-error]" in ln for ln in lines)
    assert any(ln == "FOUND: actions log failing step (actions-log): required tests" for ln in lines)
    assert any("FAILED tests/test_core.py" in ln for ln in lines)


@pytest.mark.multi_error
def test_actions_matrix_failing_jobs() -> None:
    text = (FIX / "matrix_failing_jobs.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("failing jobs (actions-log): matrix-a×1, matrix-b×1" in ln for ln in lines)
    assert any(ln == "FOUND: actions log failing steps (actions-log): 2" for ln in lines)


@pytest.mark.multi_error
def test_flutter_real_log_three_gh_errors() -> None:
    text = (FIX / "real" / "flutter_navigation_basic_114481586746.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log ##[error] markers (actions-log): 3" for ln in lines)
    assert any("No files were found with the provided path" in ln for ln in lines)


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_python_exception_group_cpython_real_output() -> None:
    text = (MULTI / "exception_group_cpython.stderr").read_text(encoding="utf-8")
    assert "+ Exception Group Traceback" in text
    report = triage(
        exit_code=1,
        stderr_text=text,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert not any(ln == "NOT FOUND: python traceback" for ln in lines)
    assert any(ln.startswith("FOUND: python exception type: ExceptionGroup") for ln in lines)
    assert any("exception group sub-exception" in ln and "ValueError" in ln for ln in lines)
    assert any("exception group sub-exception" in ln and "TypeError" in ln for ln in lines)
    assert any("<string>:1" in ln for ln in lines)
    _assert_standard_hint_floor(lines, location="<string>:1")


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_two_unrelated_tracebacks_not_chained() -> None:
    text = (MULTI / "two_unrelated_tracebacks.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=text,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: python unrelated traceback blocks: 1" for ln in lines)
    assert not any("python chained traceback blocks" in ln for ln in lines)
    assert any("python exception type (unrelated-1): TypeError" in ln for ln in lines)


@pytest.mark.multi_error
def test_node_err_module_not_found_bracket_headline() -> None:
    text = (ROOT / "tests/fixtures/readme/inputs/node_module_not_found.stderr").read_text(
        encoding="utf-8"
    )
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert not any(ln == "NOT FOUND: node error headline" for ln in lines)
    assert any("FOUND: node error code: ERR_MODULE_NOT_FOUND" in ln for ln in lines)
    assert any("Error [ERR_MODULE_NOT_FOUND]" in ln for ln in lines)


@pytest.mark.multi_error
def test_node_syntax_error_not_python_type() -> None:
    text = (ROOT / "tests/fixtures/failure_suite/inputs/node_syntax_error.stderr").read_text(
        encoding="utf-8"
    )
    report = triage(
        exit_code=1,
        stderr_text=text,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert not any(ln == "FOUND: python exception type: SyntaxError" for ln in lines)
    assert any(ln.startswith("FOUND: node error line: SyntaxError") for ln in lines)


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_node_user_script_syntax_location() -> None:
    text = (MULTI / "node_user_script_syntax.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(
        ln.startswith("FOUND: node syntax error location:") and "s_syntax.js:1" in ln
        for ln in lines
    )
    assert any(ln.startswith("FOUND: node error line: SyntaxError") for ln in lines)
    _assert_standard_hint_floor(lines, location="s_syntax.js:1")


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_node_unhandled_promise_rejection_strict() -> None:
    text = (MULTI / "node_unhandled_promise_rejection_strict.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert not any(ln == "NOT FOUND: node error headline" for ln in lines)
    assert any("UnhandledPromiseRejection:" in ln for ln in lines)
    assert any(ln == "FOUND: node error code: ERR_UNHANDLED_REJECTION" for ln in lines)
    _assert_standard_hint_floor(lines, location="UnhandledPromiseRejection")


@pytest.mark.multi_error
def test_node_err_assertion_headline() -> None:
    text = (MULTI / "node_err_assertion.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("AssertionError [ERR_ASSERTION]" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: node error headline" for ln in lines)


@pytest.mark.multi_error
def test_deno_wrapped_error_single_hint() -> None:
    text = (MULTI / "deno_wrapped_type_error.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=text,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln.startswith("FOUND: node error line:") and "TypeError" in ln for ln in lines)
    compiler_hints = [ln for ln in lines if "compiler error line (not node)" in ln]
    assert len(compiler_hints) == 0


def _overflow_actions_log(n: int) -> str:
    lines = ["2026-10-11T16:00:00.0000000Z ##[group]Run cap-test"]
    for i in range(n):
        lines.append(f"2026-10-11T16:00:00.{i:04d}Z ##[error]synthetic error {i}")
    lines.append("2026-10-11T16:00:01.0000000Z ##[error]Process completed with exit code 1.")
    lines.append("2026-10-11T16:00:01.0000001Z ##[endgroup]")
    return "\n".join(lines) + "\n"


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_actions_log_gh_error_cap_and_omitted_count() -> None:
    report = triage(
        exit_code=1,
        actions_log_text=_overflow_actions_log(45),
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log ##[error] markers (actions-log): 46" for ln in lines)
    assert any(
        ln == "FOUND: actions log ##[error] markers 40/46 (actions-log): synthetic error 39"
        for ln in lines
    )
    assert any(
        ln == "FOUND: actions log ##[error] markers omitted (actions-log): +6 omitted (total ##[error] markers 46)"
        for ln in lines
    )


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_actions_log_workflow_error_cap_and_omitted_count() -> None:
    body = "\n".join(
        f"2026-10-11T16:00:00.{i:04d}Z ::error file=f{i}.ts,line={i}::msg {i}"
        for i in range(45)
    )
    text = (
        "2026-10-11T16:00:00.0000000Z ##[group]Run wf-cap\n"
        f"{body}\n"
        "2026-10-11T16:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
        "2026-10-11T16:00:01.0000001Z ##[endgroup]\n"
    )
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log ::error:: commands (actions-log): 45" for ln in lines)
    assert any(ln.startswith("FOUND: actions log workflow error 40/45") for ln in lines)
    assert any(
        ln == "FOUND: actions log workflow error omitted (actions-log): +5 omitted (total ::error 45)"
        for ln in lines
    )
    wf_shown = [
        ln
        for ln in lines
        if re.match(r"^FOUND: actions log workflow error \d+/45 ", ln)
    ]
    assert len(wf_shown) == 40


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_actions_log_pytest_failed_cap_and_omitted_count() -> None:
    body = "\n".join(
        f"2026-10-11T16:00:00.{i:04d}Z FAILED tests/t_{i}.py::test_x - assert {i}"
        for i in range(45)
    )
    text = (
        "2026-10-11T16:00:00.0000000Z ##[group]Run pytest-cap\n"
        "2026-10-11T16:00:00.0000001Z ##[endgroup]\n"
        f"{body}\n"
        "2026-10-11T16:00:01.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln == "FOUND: actions log pytest failed lines (actions-log): 45" for ln in lines)
    assert any(ln.startswith("FOUND: actions log pytest failed lines 40/45") for ln in lines)
    assert any(
        ln
        == "FOUND: actions log pytest failed lines omitted (actions-log): +5 omitted (total pytest failed lines 45)"
        for ln in lines
    )


@pytest.mark.multi_error
def test_python_recursion_no_duplicate_node_line() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "r.py", line 1, in f\n'
        "RecursionError: maximum recursion depth exceeded\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("FOUND: python exception type: RecursionError" in ln for ln in lines)
    assert not any(ln.startswith("FOUND: node error line:") for ln in lines)


@pytest.mark.multi_error
def test_keyboard_interrupt_no_message_not_found() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "t.py", line 1, in main\n'
        "KeyboardInterrupt\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("FOUND: python exception type: KeyboardInterrupt" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: python exception message" for ln in lines)


@pytest.mark.multi_error
def test_node_error_with_cause() -> None:
    text = (MULTI / "node_with_cause.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any(ln.startswith("FOUND: node error causes:") for ln in lines)
    assert any("index.mjs:3" in ln or "index.mjs:1" in ln for ln in lines)
    _assert_standard_hint_floor(lines, location="index.mjs")


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_mixed_python_and_node_log() -> None:
    text = (MULTI / "mixed_python_node.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("FOUND: python exception type: RuntimeError" in ln for ln in lines)
    assert any(ln.startswith("FOUND: node error line:") for ln in lines)
    assert not any(
        ln.startswith("FOUND: node error line:") and "RuntimeError" in ln for ln in lines
    )
    _assert_standard_hint_floor(lines, location="runner.py:2")


@pytest.mark.multi_error
def test_chain_marker_only_emits_hint() -> None:
    text = (MULTI / "chain_marker_only.stderr").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=text,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    lines = _lines(report)
    assert any("chain marker without traceback" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_gh_run_view_threshold_three_lines() -> None:
    text = (
        "job\tStep A\t2026-10-11T12:00:00.0000000Z line one\n"
        "job\tStep B\t2026-10-11T12:00:01.0000000Z line two\n"
        "job\tStep C\t2026-10-11T12:00:02.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    _, is_gh = actions_log.preprocess_actions_log(text)
    assert is_gh is True


@pytest.mark.multi_error
@pytest.mark.mutation_guard
def test_gh_run_view_threshold_two_lines() -> None:
    text = (
        "job\tStep A\t2026-10-11T12:00:00.0000000Z line one\n"
        "job\tStep B\t2026-10-11T12:00:01.0000000Z line two\n"
        "plain log line without tabs\n"
    )
    _, is_gh = actions_log.preprocess_actions_log(text)
    assert is_gh is False


@pytest.mark.multi_error
def test_gcc_structured_hint_includes_line_column() -> None:
    text = "main.c:3:5: error: expected ';' before '}' token\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("gcc error (best effort): main.c:3:5:" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_rustc_error_code_hint_suppresses_not_found() -> None:
    text = "error[E0425]: cannot find value `x` in this scope\n --> src/main.rs:2:5\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("rustc error (best effort):" in ln and "E0425" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_asyncio_cancelled_error_no_message_not_found() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 1, in main\n'
        "asyncio.exceptions.CancelledError\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any(
        ln.endswith(": CancelledError") or ln.endswith(": asyncio.exceptions.CancelledError")
        for ln in lines
        if ln.startswith("FOUND: python exception type:")
    )
    assert not any(ln == "NOT FOUND: python exception message" for ln in lines)


@pytest.mark.multi_error
def test_deprecation_warning_as_primary_exception_type() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 1, in main\n'
        "DeprecationWarning: legacy API\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any(ln == "FOUND: python exception type: DeprecationWarning" for ln in lines)
    assert not any(ln == "NOT FOUND: python exception type" for ln in lines)


@pytest.mark.multi_error
def test_rustc_lowercase_error_is_hint_not_node() -> None:
    text = "error: expected one of `;` or `}`\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("compiler error line (not node)" in ln for ln in lines)
    assert not any(ln.startswith("FOUND: node error line:") for ln in lines)
    assert any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_truncated_traceback_hint_has_frame() -> None:
    text = 'Traceback (most recent call last):\n  File "a.py", line 1, in main\n'
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any(ln == "NOT FOUND: python exception type" for ln in lines)
    assert any(
        "python traceback without exception summary" in ln and "a.py:1" in ln
        for ln in lines
        if ln.startswith("HINT:")
    )


@pytest.mark.mutation_guard
def test_mutation_guard_flutter_three_gh_errors() -> None:
    text = (FIX / "real" / "flutter_navigation_basic_114481586746.log").read_text(encoding="utf-8")
    report = triage(
        exit_code=1,
        actions_log_text=text,
        stderr_text=None,
        log_text=None,
        junit_paths=[],
        project_root=ROOT,
    )
    assert any(ln == "FOUND: actions log ##[error] markers (actions-log): 3" for ln in _lines(report))


@pytest.mark.mutation_guard
def test_mutation_guard_gh_hits_threshold() -> None:
    """Guard: preprocess must treat 3+ gh-run-view lines as that format."""
    text = (
        "j\ts\t2026-10-11T12:00:00.0000000Z a\n"
        "j\ts\t2026-10-11T12:00:01.0000000Z b\n"
        "j\ts\t2026-10-11T12:00:02.0000000Z c\n"
    )
    _, is_gh = actions_log.preprocess_actions_log(text)
    assert is_gh


@pytest.mark.mutation_guard
def test_mutation_guard_truncated_traceback_frame_hint() -> None:
    text = 'Traceback (most recent call last):\n  File "guard.py", line 9, in main\n'
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    hints = [ln for ln in report.lines() if ln.startswith("HINT:")]
    assert any(
        "python traceback without exception summary" in ln and "guard.py:9" in ln for ln in hints
    )


@pytest.mark.mutation_guard
def test_mutation_guard_chained_exceptions_linked() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in first\n'
        "ValueError: one\n"
        "During handling of the above exception, another exception occurred:\n"
        "Traceback (most recent call last):\n"
        '  File "b.py", line 2, in second\n'
        "TypeError: two\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any(ln == "FOUND: python chained traceback blocks: 1" for ln in lines)
    assert any("python exception type (chained-1): TypeError" in ln for ln in lines)


@pytest.mark.mutation_guard
def test_mutation_guard_chain_marker_hint() -> None:
    text = (MULTI / "chain_marker_only.stderr").read_text(encoding="utf-8")
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    assert any("chain marker without traceback" in ln for ln in report.lines())


def test_combo_mutation_script_zero_survivors() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "combo_mutation.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "survivors: 0" in proc.stdout.lower() or "0 survivor" in proc.stdout.lower()
