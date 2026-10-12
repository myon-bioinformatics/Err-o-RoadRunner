"""Regression tests for PR #2 review gaps (mutations must not return)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from err_o_roadrunner.triage import (
    MAX_READ_BYTES,
    Report,
    _split_python_trace_blocks,
    safe_read_text,
    strip_terminal_controls,
    triage,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.gap
def test_safe_read_enforces_max_bytes_via_cli(tmp_path: Path) -> None:
    marker = b"BEYOND_CAP_MARKER"
    path = tmp_path / "huge.log"
    path.write_bytes(b"z" * MAX_READ_BYTES + marker)
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "err_o_roadrunner", "--exit-code", "1", "--log", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
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
    from err_o_roadrunner.actions_log import preprocess_actions_log

    raw = "\ufeff2026-10-11T13:00:00.0000000Z \x1b[31merror\x1b[0m\n"
    records, _ = preprocess_actions_log(raw)
    assert [rec.text for rec in records] == ["error"]


@pytest.mark.gap
def test_actions_log_line_crlf_rstrip_required() -> None:
    from err_o_roadrunner.actions_log import normalize_actions_log_line

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
