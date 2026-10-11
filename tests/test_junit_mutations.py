"""Mutation-sensitive tests for JUnit and actions-log preprocessing."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest import mock

import pytest

from err_o_roadrunner import actions_log
from err_o_roadrunner.triage import triage

TRIAGE_MOD = importlib.import_module("err_o_roadrunner.triage")

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"
FIX = ROOT / "tests" / "fixtures" / "actions_logs"


@pytest.mark.gap
def test_junit_assertion_error_does_not_emit_dart_error() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="assert 0">E   AssertionError: boom</failure>
  </testcase>
</testsuite>"""
    path = CORPUS / "inputs" / "_assertion_no_dart.xml"
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
def test_mutation_dropping_testsuite_collection_scan_misses_suite_error() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="0" errors="1">
  <error message="collection failure">could not load</error>
</testsuite>"""
    path = CORPUS / "inputs" / "_suite_collection.xml"
    path.write_text(xml, encoding="utf-8")
    with mock.patch.object(
        TRIAGE_MOD,
        "_junit_testsuite_collection_errors",
        return_value=[],
    ):
        report = triage(
            exit_code=1,
            junit_paths=[str(path)],
            stderr_text=None,
            log_text=None,
            actions_log_text=None,
            project_root=EMPTY,
        )
    assert any(ln.startswith("NOT FOUND: junit collection errors (") for ln in report.lines())


@pytest.mark.gap
def test_mutation_dropping_dart_core_filter_pollutes_diff_hint() -> None:
    import re

    blob = "- 'Object' is from 'dart:core'.\n"
    assert "dart:core" not in TRIAGE_MOD._junit_diff_hint(blob)
    with mock.patch.object(TRIAGE_MOD, "DART_NOISE_LINE_RE", re.compile("$^")):
        hint = TRIAGE_MOD._junit_diff_hint(blob)
    assert "dart:core" in hint


@pytest.mark.gap
def test_mutation_dropping_crlf_rstrip_leaves_carriage_returns() -> None:
    raw = b"2026-10-11T13:00:00.0000000Z line\r\n"
    with (
        mock.patch.object(actions_log, "_strip_cr_from_log_line", lambda s: s),
        mock.patch.object(actions_log, "strip_terminal_controls", lambda s: s),
    ):
        records, _ = actions_log.preprocess_actions_log(raw.decode("utf-8"))
    assert any("\r" in rec.text for rec in records)


@pytest.mark.gap
def test_crlf_preprocess_strips_cr_from_crlf_bytes() -> None:
    raw = (FIX / "crlf_bom.log").read_bytes()
    assert b"\r" in raw
    records, _ = actions_log.preprocess_actions_log(raw.decode("utf-8-sig"))
    assert all("\r" not in rec.text for rec in records)
