"""Triage CLI tests: success, failure, and adversarial patterns."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from err_o_roadrunner.triage import (
    EXIT_OK,
    EXIT_USAGE,
    MAX_FRAMES,
    MAX_READ_BYTES,
    Report,
    _split_python_trace_blocks,
    main,
    read_stream_arg,
    strip_terminal_controls,
    triage,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "err_o_roadrunner", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


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
    import err_o_roadrunner as pkg

    assert pkg.__version__ == "0.2.0"
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
    assert "FOUND: python chained traceback blocks: 2" in joined
    assert "python exception type (primary): ValueError" in joined
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
    blocks = _split_python_trace_blocks(text)
    assert len(blocks) == 2
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    joined = "\n".join(report.lines())
    assert "python exception type (primary): ValueError" in joined
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
