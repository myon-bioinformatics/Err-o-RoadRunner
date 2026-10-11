"""Behaviour and mutation-sensitive tests for JUnit triage and actions logs."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from err_o_roadrunner import actions_log
from err_o_roadrunner.triage import triage

TRIAGE_MOD = importlib.import_module("err_o_roadrunner.triage")

ROOT = Path(__file__).resolve().parents[1]
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"
FIX = ROOT / "tests" / "fixtures" / "actions_logs"


def run_cli(xml_path: Path) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "err_o_roadrunner",
            "--exit-code",
            "1",
            "--project-root",
            str(EMPTY),
            "--junit",
            str(xml_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    return proc.stdout


@pytest.mark.gap
def test_junit_assertion_error_does_not_emit_dart_error(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="assert 0">E   AssertionError: boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "assertion.xml"
    path.write_text(xml, encoding="utf-8")
    report = triage(
        exit_code=1,
        junit_paths=[str(path)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    assert not any(ln.startswith("FOUND: junit dart error") for ln in report.lines())


@pytest.mark.gap
def test_testsuite_level_import_error_reports_collection_via_cli(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="0" errors="1">
  <error message="collection failure">ImportError while importing test module 'tests/conftest.py'.</error>
</testsuite>"""
    path = tmp_path / "suite_import.xml"
    path.write_text(xml, encoding="utf-8")
    out = run_cli(path)
    assert "FOUND: junit collection errors (" in out
    assert "ImportError while importing" in out


@pytest.mark.gap
def test_junit_failure_duplicate_message_and_body_printed_once(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="boom">boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "dedup.xml"
    path.write_text(xml, encoding="utf-8")
    out = run_cli(path)
    failure_lines = [ln for ln in out.splitlines() if ln.startswith("FOUND: junit failure (first)")]
    assert len(failure_lines) == 1
    assert failure_lines[0].count("boom") == 1


@pytest.mark.gap
def test_junit_xml_crlf_entities_produce_no_cr_in_cli_output(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="line1&#13;&#10;line2"/>
  </testcase>
</testsuite>"""
    path = tmp_path / "crlf.xml"
    path.write_text(xml, encoding="utf-8")
    out = run_cli(path)
    assert "\r" not in out
    failure = next(ln for ln in out.splitlines() if ln.startswith("FOUND: junit failure (first)"))
    assert "line1\\nline2" in failure


@pytest.mark.gap
def test_junit_flatten_normalizes_crlf_before_escaping() -> None:
    flat = TRIAGE_MOD._junit_flatten_for_report("line1\r\nline2")
    assert "\r" not in flat
    assert flat == "line1\\nline2"
    assert "line1\\n\\nline2" != flat


@pytest.mark.gap
def test_mutation_dropping_dart_core_filter_pollutes_diff_hint() -> None:
    import re

    blob = "- 'Object' is from 'dart:core'.\n"
    assert "dart:core" not in TRIAGE_MOD._junit_diff_hint(blob)
    with mock.patch.object(TRIAGE_MOD, "DART_NOISE_LINE_RE", re.compile("$^")):
        hint = TRIAGE_MOD._junit_diff_hint(blob)
    assert "dart:core" in hint


@pytest.mark.gap
def test_actions_log_preprocess_strips_cr_from_crlf_bytes() -> None:
    raw = (FIX / "crlf_bom.log").read_bytes()
    assert b"\r" in raw
    records, _ = actions_log.preprocess_actions_log(raw.decode("utf-8-sig"))
    assert all("\r" not in rec.text for rec in records)
