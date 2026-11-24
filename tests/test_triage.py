"""Triage CLI: success, failure and adversarial inputs, review-gap regressions, pasted errors, multi-error coverage (mutation_guard).

- triage: Triage CLI tests: success, failure, and adversarial patterns.
- triage_gaps: Regression tests for PR #2 review gaps (mutations must not return).
- direct_error_input: errors passed as words or with --paste.
- placeholder: the package entry point exits 0 on success.
- multi_error_coverage: Best-effort multi-error coverage and HINT floor for standard error formats.
"""

from __future__ import annotations

import io, os, re, sys, textwrap
from pathlib import Path
from unittest import mock

import pytest

from vendor import cli_args
import triage as triage_module
from errlib import actions_log
from triage import (
    _split_python_trace_blocks,
    EXIT_OK,
    EXIT_USAGE,
    main,
    MAX_FRAMES,
    MAX_READ_BYTES,
    read_stream_arg,
    Report,
    safe_read_text,
    strip_terminal_controls,
    triage,
)


# ──── triage ──────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*args: str) -> cli_args.CommandResult:
    return cli_args.run_command(cli_args.Command((sys.executable, "-m", "triage", *args)), cwd=ROOT)


def lines_of(text: str) -> list[str]:
    return [ln for ln in text.strip().splitlines() if ln]


# --- success ---


@pytest.mark.success
def test_success_exit_zero_minimal_report() -> None:
    report = triage(exit_code=0, stderr_text=None, log_text=None, junit_paths=[], project_root=ROOT)
    lines = report.lines()
    assert any("FOUND: exit code: 0" in ln for ln in lines)
    assert any("NOT FOUND: failure evidence" in ln for ln in lines)
    assert all(not ln.startswith("Traceback") for ln in lines)


@pytest.mark.success
def test_success_cli_exit_code_zero() -> None:
    proc = run_cli("--exit-code", "0")
    assert proc.returncode == EXIT_OK
    assert "FOUND: exit code: 0" in proc.stdout
    assert "NOT FOUND:" in proc.stdout


@pytest.mark.success
def test_success_junit_without_failures() -> None:
    xml = FIXTURES / "junit_ok.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(xml)],
        project_root=ROOT,
    )
    lines = report.lines()
    assert any("NOT FOUND: junit failures" in ln for ln in lines)


@pytest.mark.success
def test_success_import_and_version() -> None:
    import triage as pkg

    assert pkg.__version__ == "0.6.0"
    assert pkg.EXIT_OK == 0


# --- failure ---


PYTHON_TB = """\
Traceback (most recent call last):
  File "/app/module.py", line 10, in run
    raise ValueError("bad widget")
ValueError: bad widget
"""
NODE_ERR = """\
Error: Cannot find module 'missing'
    at Object.<anonymous> (/project/index.js:4:11)
    at Module._compile (internal/modules/cjs/loader.js:999:30)
"""


@pytest.mark.failure
def test_failure_python_traceback_facts() -> None:
    report = triage(exit_code=1, stderr_text=PYTHON_TB, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: ValueError" in joined
    assert "FOUND: python exception message: bad widget" in joined
    assert "FOUND: python traceback frame (first): /app/module.py:10 in run" in joined
    assert "HINT: web search keywords: ValueError bad widget" in joined


@pytest.mark.failure
def test_failure_node_stack() -> None:
    report = triage(exit_code=1, stderr_text=None, log_text=NODE_ERR, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: node error line:" in joined
    assert "FOUND: node stack frame (first):" in joined
    assert "/project/index.js:4" in joined


@pytest.mark.failure
def test_failure_junit_xml() -> None:
    xml = FIXTURES / "junit_fail.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(xml)],
        project_root=ROOT,
    )
    joined = "\n".join(report.lines())
    assert "FOUND: junit failures" in joined
    assert "AssertionError" in joined


@pytest.mark.failure
def test_failure_cli_with_stderr_file(tmp_path: Path) -> None:
    err = tmp_path / "stderr.txt"
    err.write_text(PYTHON_TB, encoding="utf-8")
    proc = run_cli("--exit-code", "1", "--stderr", str(err))
    assert proc.returncode == EXIT_OK
    assert "ValueError" in proc.stdout


@pytest.mark.failure
def test_failure_runtime_facts_present() -> None:
    report = triage(exit_code=1, stderr_text=PYTHON_TB, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python version:" in joined
    assert "FOUND: environment variable count:" in joined


# --- adversarial ---


@pytest.mark.adversarial
def test_adversarial_malformed_junit_xml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuite><testcase>", encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(bad)],
        project_root=ROOT,
    )
    assert any("NOT FOUND: junit xml parse" in ln for ln in report.lines())


@pytest.mark.adversarial
def test_adversarial_huge_xml_tag_limit(tmp_path: Path) -> None:
    # Many empty elements to exceed tag budget without huge string in memory at once
    inner = "<a/>" * 120_000
    huge = tmp_path / "huge.xml"
    huge.write_text(f"<testsuite>{inner}</testsuite>", encoding="utf-8")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(huge)],
        project_root=ROOT,
    )
    joined = "\n".join(report.lines())
    assert "junit xml truncated" in joined or "NOT FOUND: junit xml parse" in joined


@pytest.mark.adversarial
def test_adversarial_binary_utf8_invalid(tmp_path: Path) -> None:
    raw = tmp_path / "bin.log"
    raw.write_bytes(b"\xff\xfeTraceback (most recent call last):\n\x00\x00")
    proc = run_cli("--exit-code", "1", "--log", str(raw))
    assert proc.returncode == EXIT_OK
    assert "Traceback" not in proc.stderr


@pytest.mark.adversarial
def test_adversarial_ansi_escapes_stripped() -> None:
    text = "\x1b[31mTraceback (most recent call last):\x1b[0m\n  File \"x.py\", line 1, in f\n    pass\n\x1b[1mRuntimeError\x1b[0m: \x1b[31mno\x1b[0m\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "\x1b[" not in joined
    assert "FOUND: python exception type: RuntimeError" in joined


@pytest.mark.adversarial
def test_adversarial_path_traversal_rejected(tmp_path: Path) -> None:
    proc = run_cli("--exit-code", "1", "--stderr", str(tmp_path / ".." / ".." / "etc" / "passwd"))
    assert proc.returncode == EXIT_OK
    assert "unsafe path (path traversal rejected):" in proc.stdout
    assert "file not readable" not in proc.stdout


@pytest.mark.adversarial
def test_adversarial_symlink_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "real.log"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.log"
    link.symlink_to(target)
    proc = run_cli("--exit-code", "1", "--log", str(link))
    assert proc.returncode == EXIT_OK
    assert "symlink not followed:" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_directory_not_file(tmp_path: Path) -> None:
    proc = run_cli("--exit-code", "1", "--stderr", str(tmp_path))
    assert proc.returncode == EXIT_OK
    assert "not a regular file (directory):" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_missing_file_not_directory_message(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"
    proc = run_cli("--exit-code", "1", "--stderr", str(missing))
    assert proc.returncode == EXIT_OK
    assert "not a regular file (directory)" not in proc.stdout
    assert "file not readable:" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_read_size_cap_reported(tmp_path: Path) -> None:
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * (MAX_READ_BYTES + 1))
    proc = run_cli("--exit-code", "1", "--log", str(big))
    assert proc.returncode == EXIT_OK
    assert f"input truncated (size {MAX_READ_BYTES + 1} > {MAX_READ_BYTES})" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_nul_bytes_reported(tmp_path: Path) -> None:
    raw = tmp_path / "nul.log"
    raw.write_bytes(b"Traceback\x00rest")
    proc = run_cli("--exit-code", "1", "--log", str(raw))
    assert proc.returncode == EXIT_OK
    assert "binary or nul bytes present in:" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_deep_stack() -> None:
    frames = "\n".join(f'  File "f{i}.py", line {i}, in g' for i in range(MAX_FRAMES + 50))
    text = f"Traceback (most recent call last):\n{frames}\nRecursionError: too deep\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert f"python traceback frame count: {MAX_FRAMES}" in joined
    assert f"python traceback frame cap reached ({MAX_FRAMES})" in joined


@pytest.mark.adversarial
def test_adversarial_truncated_traceback_no_crash() -> None:
    text = "Traceback (most recent call last):\n  File \"a.py\", line 1, in main\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "NOT FOUND: python exception type" in joined


@pytest.mark.adversarial
def test_adversarial_empty_input() -> None:
    report = triage(exit_code=1, stderr_text="", log_text=None, junit_paths=[], project_root=ROOT)
    assert any("empty" in ln.lower() for ln in report.lines())


@pytest.mark.adversarial
def test_adversarial_injection_like_payload_no_exec(tmp_path: Path) -> None:
    payload = "__import__('os').system('id')\n" * 50
    payload += "eval(compile('1+1','','eval'))\n"
    log = tmp_path / "inject.log"
    log.write_text(payload, encoding="utf-8")
    proc = run_cli("--exit-code", "1", "--log", str(log))
    assert proc.returncode == EXIT_OK
    assert proc.stderr == ""


@pytest.mark.adversarial
def test_adversarial_cli_never_tracebacks_on_bad_flag() -> None:
    proc = run_cli("--not-a-real-flag")
    assert proc.returncode == EXIT_USAGE
    assert "Traceback" not in proc.stderr


@pytest.mark.adversarial
def test_adversarial_main_invalid_args_code() -> None:
    assert main(["--totally-unknown"]) == EXIT_USAGE


@pytest.mark.failure
def test_regression_junit_failure_message_attribute_without_body() -> None:
    xml = FIXTURES / "junit_failure_message_only.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(xml)],
        project_root=ROOT,
    )
    joined = "\n".join(report.lines())
    assert "FOUND: junit failure (first)" in joined
    assert "expected True is False" in joined


@pytest.mark.failure
def test_regression_junit_skipped_reported() -> None:
    xml = FIXTURES / "junit_skipped.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(xml)],
        project_root=ROOT,
    )
    joined = "\n".join(report.lines())
    assert "FOUND: junit skipped" in joined
    assert "not ready yet" in joined


@pytest.mark.failure
def test_regression_identifier_lines_are_hints_not_exception_type() -> None:
    text = textwrap.dedent(
        """\
        Traceback (most recent call last):
          File "a.py", line 2, in main
            pass
        not_a_real_exception
        ValueError: final error
        """
    ).lstrip("\n")
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: ValueError" in joined
    assert "FOUND: python exception type: not_a_real_exception" not in joined
    assert "HINT: uncertain traceback line: not_a_real_exception" in joined
    assert "HINT: trailing colon line: ValueError: final error" not in joined


@pytest.mark.failure
def test_regression_chained_python_exceptions() -> None:
    text = """\
Traceback (most recent call last):
  File "a.py", line 1, in first
    raise ValueError("one")
ValueError: one

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "b.py", line 2, in second
    raise TypeError("two")
TypeError: two
"""
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python chained traceback blocks: 1" in joined
    assert "FOUND: python exception type: ValueError" in joined
    assert "python exception type (chained-1): TypeError" in joined


@pytest.mark.failure
def test_regression_syntax_error_without_traceback_header() -> None:
    text = '  File "script.py", line 3\n    print(x\nSyntaxError: invalid syntax\n'
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: SyntaxError" in joined
    assert "invalid syntax" in joined


@pytest.mark.failure
def test_regression_node_custom_error_and_err_code() -> None:
    text = """\
CustomRangeError: value out of bounds
    at load (file:///tmp/app.mjs:8:3)
"""
    report = triage(exit_code=1, stderr_text=None, log_text=text, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "CustomRangeError" in joined
    assert "file:///tmp/app.mjs:8" in joined


@pytest.mark.failure
def test_regression_node_internal_frame_and_unhandled_rejection() -> None:
    text = """\
UnhandledPromiseRejectionWarning: Promise rejected
    at async main (node:internal/process/task_queues:95:5)
code: 'ERR_MODULE_NOT_FOUND'
"""
    report = triage(exit_code=1, stderr_text=None, log_text=text, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "UnhandledPromiseRejectionWarning" in joined
    assert "node:internal/process/task_queues:95" in joined
    assert "FOUND: node error code: ERR_MODULE_NOT_FOUND" in joined


@pytest.mark.adversarial
def test_regression_osc8_stripped_from_junit_message(tmp_path: Path) -> None:
    osc = "\x1b]8;;https://evil.example\x1b\\"
    xml = tmp_path / "osc.xml"
    xml.write_text(
        f'<?xml version="1.0"?><testsuite><testcase name="t">'
        f'<failure message="{osc}boom"/></testcase></testsuite>',
        encoding="utf-8",
    )
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        junit_paths=[str(xml)],
        project_root=ROOT,
    )
    joined = "\n".join(report.lines())
    assert "\x1b]" not in joined
    assert "boom" in joined


@pytest.mark.adversarial
def test_regression_osc_stripped_from_stderr_traceback() -> None:
    text = (
        "\x1b]8;;https://x.example\x1b\\Traceback (most recent call last):\n"
        '  File "x.py", line 1, in f\n'
        "RuntimeError: no\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "\x1b]" not in joined
    assert "FOUND: python exception type: RuntimeError" in joined


def _assert_no_control_chars(text: str) -> None:
    for ch in text:
        code = ord(ch)
        if ch in "\t\n":
            continue
        assert code >= 32 and code != 0x7F and not (0x80 <= code <= 0x9F)
    assert "\x1b" not in text


@pytest.mark.failure
def test_regression_log_colon_line_does_not_replace_exception_type() -> None:
    text = """\
Traceback (most recent call last):
  File "a.py", line 1, in main
    raise ValueError("real")
ValueError: real
INFO: retrying in 5s
"""
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: ValueError" in joined
    assert "FOUND: python exception type: INFO" not in joined
    assert "HINT: trailing colon line: INFO: retrying in 5s" in joined


@pytest.mark.adversarial
def test_regression_c1_osc_dcs_st_and_unterminated_osc_stripped() -> None:
    raw = (
        "boom\u009d8;;https://evil\u009c"
        "\u0090payload\u009c"
        "\x9b31m"
        "\x1b]8;;https://x"
        "\x1bc"
        "tail"
    )
    cleaned = strip_terminal_controls(raw)
    assert cleaned == "boomtail"
    report = Report()
    report.add_found(cleaned)
    _assert_no_control_chars("\n".join(report.lines()))


@pytest.mark.adversarial
def test_regression_fifo_not_treated_as_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.fifo"
    fifo_path = os.fsencode(fifo)
    os.mkfifo(fifo_path)
    proc = run_cli("--exit-code", "1", "--log", str(fifo))
    assert proc.returncode == EXIT_OK
    assert "not a regular file" in proc.stdout


@pytest.mark.adversarial
def test_regression_stdin_read_cap_and_nul(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"\x00" + (b"x" * (MAX_READ_BYTES + 10))
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": io.BytesIO(payload)})())
    report = Report()
    read_stream_arg("-", report, "stderr")
    joined = "\n".join(report.lines())
    assert f"input truncated (size > {MAX_READ_BYTES}): stderr stdin" in joined
    assert "binary or nul bytes present in: stderr stdin" in joined


@pytest.mark.failure
def test_regression_chain_marker_splits_trace_blocks() -> None:
    text = """\
Traceback (most recent call last):
  File "a.py", line 1, in first
ValueError: one
The above exception was the direct cause of the following exception:
Traceback (most recent call last):
  File "b.py", line 2, in second
TypeError: two
"""
    blocks, _unrelated = _split_python_trace_blocks(text)
    assert len(blocks) == 2
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: ValueError" in joined
    assert "python exception type (chained-1): TypeError" in joined


@pytest.mark.failure
def test_regression_syntax_error_branch_without_traceback_header() -> None:
    text = "SyntaxError: unexpected EOF while parsing\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: SyntaxError" in joined
    assert "NOT FOUND: recognized failure pattern" not in joined


@pytest.mark.failure
def test_regression_node_bracket_rejection_module_not_found_and_brace_frame() -> None:
    text = """\
[UnhandledPromiseRejection: missing export]
    at ModuleJob.run (node:internal/modules/esm/module_job:222:25) {
code: 'MODULE_NOT_FOUND'
"""
    report = triage(exit_code=1, stderr_text=None, log_text=text, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "[UnhandledPromiseRejection:" in joined
    assert "FOUND: node error code: MODULE_NOT_FOUND" in joined
    assert "node:internal/modules/esm/module_job:222" in joined


@pytest.mark.adversarial
def test_regression_dcs_c1_csi_removed_from_report_output() -> None:
    text = "\x1bP1;2;3\x1b\\\x9b0mTraceback (most recent call last):\n  File \"a.py\", line 1, in f\nValueError: x\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    _assert_no_control_chars("\n".join(report.lines()))
    assert "FOUND: python exception type: ValueError" in "\n".join(report.lines())


# --- --test-events: flutter/dart `test --machine` JSON lines (2026-11-07 corpus feedback) ---

FLUTTER_MACHINE_EVENTS = "\n".join([
    '{"type":"start","protocolVersion":"0.1.1"}',
    '{"type":"testStart","test":{"id":288,"name":"Screen3 compact template is reachable above Door","url":"file:///w/test/screens/generic_screen_test.dart"}}',
    '{"type":"print","testID":288,"message":"══╡ EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK ╞════\\nThe following TestFailure was thrown running a test:\\nExpected: a value less than or equal to <346.01>\\n  Actual: <355.0>\\n   Which: is not a value less than or equal to <346.01>\\n\\nWhen the exception was thrown, this was the stack:\\n#4      main.<anonymous closure> (file:///w/test/screens/generic_screen_test.dart:393:7)\\nThis was caught by the test expectation on the following line:\\n  file:///w/test/screens/generic_screen_test.dart line 393\\n"}',
    '{"type":"error","testID":288,"error":"Test failed. See exception logs above.\\nThe test description was: Screen3 compact template is reachable above Door","stackTrace":"","isFailure":false}',
    '{"type":"testDone","testID":288,"result":"error","hidden":false}',
    'not json at all',
    '{"type":"done","success":false}',
])


def test_test_events_pairs_error_with_print_block(tmp_path: Path) -> None:
    path = tmp_path / "flutter.jsonl"
    path.write_text(FLUTTER_MACHINE_EVENTS, encoding="utf-8")
    result = run_cli("--exit-code", "1", "--test-events", str(path), "--project-root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"FOUND: test-events errors ({path}): 1" in out
    assert f"FOUND: test-events error (first) ({path}): Screen3 compact template is reachable above Door — Test failed. See exception logs above." in out
    assert f"FOUND: test-events exception type ({path}): TestFailure" in out
    assert f"FOUND: test-events location ({path}): file:///w/test/screens/generic_screen_test.dart line 393" in out
    assert "HINT: test-events expectation (" in out and "Expected: a value less than or equal to <346.01> | Actual: <355.0> | Which:" in out
    assert f"FOUND: test-events invalid lines ({path}): 1" in out
    assert "failure evidence (no stderr" not in out


def test_test_events_without_errors_and_without_records(tmp_path: Path) -> None:
    clean = tmp_path / "clean.jsonl"
    clean.write_text('{"type":"testStart","test":{"id":1,"name":"ok"}}\n{"type":"testDone","testID":1,"result":"success"}\n', encoding="utf-8")
    out = run_cli("--exit-code", "1", "--test-events", str(clean), "--project-root", str(tmp_path)).stdout
    assert f"NOT FOUND: test-events errors ({clean})" in out
    empty = tmp_path / "empty.jsonl"
    empty.write_text("garbage\n", encoding="utf-8")
    out = run_cli("--exit-code", "1", "--test-events", str(empty), "--project-root", str(tmp_path)).stdout
    assert f"NOT FOUND: test-events json records ({empty})" in out


# ──── triage_gaps ─────────────────────────────────────────────────

@pytest.mark.gap
def test_safe_read_enforces_max_bytes_via_cli(tmp_path: Path) -> None:
    marker = b"BEYOND_CAP_MARKER"
    path = tmp_path / "huge.log"
    path.write_bytes(b"z" * MAX_READ_BYTES + marker)
    proc = run_cli("--exit-code", "1", "--log", str(path))
    assert proc.returncode == 0
    assert "BEYOND_CAP_MARKER" not in proc.stdout
    assert f"input truncated (size {MAX_READ_BYTES + len(marker)} > {MAX_READ_BYTES})" in proc.stdout


@pytest.mark.gap
def test_open_uses_nonblock_and_rechecks_inode(tmp_path: Path) -> None:
    target = tmp_path / "file.log"
    target.write_text("ok", encoding="utf-8")
    opened: list[int] = []
    real_open = os.open

    def fake_open(path, flags, *args, **kwargs):
        opened.append(flags)
        return real_open(path, flags, *args, **kwargs)

    report = Report()
    with mock.patch("os.open", side_effect=fake_open):
        safe_read_text(target, report)
    assert opened
    assert opened[0] & os.O_NONBLOCK


@pytest.mark.gap
def test_fstat_inode_mismatch_rejected(tmp_path: Path) -> None:
    target = tmp_path / "race.log"
    target.write_text("data", encoding="utf-8")
    st = target.lstat()

    def fake_fstat(fd: int) -> os.stat_result:
        return os.stat_result(
            (st.st_mode, st.st_ino + 99, st.st_dev, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        )

    report = Report()
    with mock.patch("os.fstat", side_effect=fake_fstat):
        safe_read_text(target, report)
    joined = "\n".join(report.lines())
    assert "file identity changed after open" in joined


@pytest.mark.gap
def test_c1_controls_stripped_blanket() -> None:
    raw = "ok\u0085mid\u009fend"
    assert strip_terminal_controls(raw) == "okmidend"


@pytest.mark.gap
def test_tab_preserved_in_report_output() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "t.py", line 1, in f\n'
        "ValueError: col1\tcol2\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "col1\tcol2" in joined


@pytest.mark.gap
def test_chain_marker_splits_blocks() -> None:
    text = (
        "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: one\n"
        "During handling of the above exception, another exception occurred:\n"
        "Traceback (most recent call last):\n  File \"b.py\", line 2\nTypeError: two\n"
    )
    blocks, _unrelated = _split_python_trace_blocks(text)
    assert len(blocks) == 2
    assert blocks[1].linked_to_previous


@pytest.mark.gap
def test_syntax_error_branch_without_traceback_header() -> None:
    text = "SyntaxError: leading token only\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    assert any("FOUND: python exception type: SyntaxError" in ln for ln in report.lines())


@pytest.mark.gap
def test_warning_line_does_not_replace_exception_type() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in main\n'
        "ValueError: real\n"
        "DeprecationWarning: old api\n"
    )
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "FOUND: python exception type: ValueError" in joined
    assert "FOUND: python exception type: DeprecationWarning" not in joined
    assert "warning line after traceback" in joined


@pytest.mark.gap
def test_osc_terminated_by_c1_st_stripped() -> None:
    raw = "safe\x1b]8;;https://example\x9cunsafe"
    assert strip_terminal_controls(raw) == "safeunsafe"


@pytest.mark.gap
def test_apc_pm_sos_sequences_stripped() -> None:
    raw = "a\x1b_Payload\x9cb\x1b^PM\x9cc\x1bXSOS\x9cd"
    assert strip_terminal_controls(raw) == "abcd"


@pytest.mark.gap
def test_actions_log_preprocess_strips_bom_and_controls() -> None:
    from errlib.actions_log import preprocess_actions_log

    raw = "\ufeff2026-10-11T13:00:00.0000000Z \x1b[31merror\x1b[0m\n"
    records, _ = preprocess_actions_log(raw)
    assert [rec.text for rec in records] == ["error"]


@pytest.mark.gap
def test_actions_log_line_crlf_rstrip_required() -> None:
    from errlib.actions_log import normalize_actions_log_line

    assert normalize_actions_log_line("payload\r") == "payload"


@pytest.mark.gap
def test_safe_read_text_read_bytes_capped_at_max_read_bytes(tmp_path: Path) -> None:
    path = tmp_path / "over_cap.bin"
    extra = 4096
    path.write_bytes(b"x" * (MAX_READ_BYTES + extra))
    report = Report()
    text = safe_read_text(path, report)
    assert text is not None
    assert len(text.encode("utf-8")) == MAX_READ_BYTES
    joined = "\n".join(report.lines())
    assert f"input truncated (size {MAX_READ_BYTES + extra} > {MAX_READ_BYTES})" in joined


# ──── direct_error_input ──────────────────────────────────────────

def test_direct_traceback_is_parsed(capsys):
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input'
    assert triage_module.main([text]) == 0
    assert 'python exception type: ValueError' in capsys.readouterr().out


def test_single_line_suggests_python_without_claiming_language(capsys):
    assert triage_module.main(['ModuleNotFoundError:', 'No', 'module', 'named', 'example']) == 0
    assert 'possible Python exception (single line; language unconfirmed): ModuleNotFoundError' in capsys.readouterr().out


def test_direct_text_and_stderr_conflict(capsys):
    assert triage_module.main(['error message', '--stderr', 'x']) == 2
    assert 'choose only one' in capsys.readouterr().err


def test_paste_multiline_from_stdin():
    from pathlib import Path
    script = Path(triage_module.__file__)
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input\n'
    result = cli_args.run_cli(script, '--paste', stdin=text)
    assert result.returncode == 0
    assert 'python exception type: ValueError' in result.stdout


def test_direct_tokens_from_shell():
    result = cli_args.run_cli(triage_module.__file__, 'ModuleNotFoundError:', 'No', 'module', 'named', 'example')
    assert result.returncode == 0
    assert 'possible Python exception' in result.stdout


def test_candidate_helper_is_reusable_without_cli():
    from errlib.error_input import python_exception_candidate
    assert python_exception_candidate('ValueError: invalid') == 'ValueError: invalid'
    assert python_exception_candidate('not an exception: invalid') is None
    assert python_exception_candidate('ValueError: first\nnext line') is None


# ──── placeholder ─────────────────────────────────────────────────

def test_package_entrypoint_exists() -> None:
    assert main(["--exit-code", "0"]) == EXIT_OK


# ──── multi_error_coverage ────────────────────────────────────────

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


@pytest.mark.multi_error
def test_go_panic_structured_hint() -> None:
    text = "panic: runtime error: index out of range [2] with length 1\n\ngoroutine 1 [running]:\nmain.main()\n\t/app/main.go:8 +0x1d\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("go panic (best effort): panic: runtime error: index out of range" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_go_compiler_error_structured_hint() -> None:
    text = "main.go:12:5: undefined: fmt.Printlnn\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("go compiler error (best effort): main.go:12:5: undefined: fmt.Printlnn" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
def test_typescript_compiler_error_structured_hint() -> None:
    text = "src/index.ts:15:3 - error TS2322: Type 'string' is not assignable to type 'number'.\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any("typescript error (best effort): src/index.ts:15:3: TS2322: Type 'string' is not assignable" in ln for ln in lines)
    assert not any(ln == "NOT FOUND: recognized failure pattern in log/stderr" for ln in lines)


@pytest.mark.multi_error
@pytest.mark.parametrize("path", ["src/index.ts", r"C:\src\index.ts", "src/my project/index.tsx"])
def test_typescript_plain_compiler_error_structured_hint(path: str) -> None:
    text = f"{path}(15,3): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    lines = _lines(report)
    assert any(f"typescript error (best effort): {path}:15:3: TS2322:" in line for line in lines)
    assert not any(line == "NOT FOUND: recognized failure pattern in log/stderr" for line in lines)


def test_combo_mutation_script_zero_survivors() -> None:
    proc = cli_args.run_cli(ROOT / "scripts" / "check_mutations.py", "combo", cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "survivors: 0" in proc.stdout.lower() or "0 survivor" in proc.stdout.lower()
