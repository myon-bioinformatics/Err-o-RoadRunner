"""Triage CLI tests: success, failure, and adversarial patterns."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from err_o_roadrunner.triage import EXIT_OK, EXIT_USAGE, main, triage

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

    assert pkg.__version__ == "0.1.0"
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
    assert "path traversal rejected" in proc.stdout or "file not readable" in proc.stdout


@pytest.mark.adversarial
def test_adversarial_deep_stack() -> None:
    frames = "\n".join(f'  File "f{i}.py", line {i}, in g' for i in range(500))
    text = f"Traceback (most recent call last):\n{frames}\nRecursionError: too deep\n"
    report = triage(exit_code=1, stderr_text=text, log_text=None, junit_paths=[], project_root=ROOT)
    assert any("python traceback frame count:" in ln for ln in report.lines())


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
