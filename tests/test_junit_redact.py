"""Unit tests for junit_redact leak detection and redaction."""

from __future__ import annotations

import pytest

from err_o_roadrunner.junit_redact import find_corpus_leaks, redact_xml_document


@pytest.mark.gap
def test_find_corpus_leaks_detects_runner_hostname_attribute() -> None:
    text = '<testsuite hostname="runnervmabc" name="pytest"/>'
    assert "hostname_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_non_runner_hostname_attribute() -> None:
    text = '<testsuite hostname="cursor" name="pytest"/>'
    assert "hostname_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_iso_timestamp_attribute() -> None:
    text = 'timestamp="2026-10-11T15:23:14.305804+00:00"'
    assert "iso_timestamp_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_runner_work_path() -> None:
    text = "ImportError in /home/runner/work/foo/bar/tests"
    assert "runner_work_path" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_clean_after_redact_xml_document() -> None:
    raw = (
        '<?xml version="1.0"?><testsuites>'
        '<testsuite name="pytest" hostname="cursor" timestamp="2026-10-11T12:00:00+00:00"/>'
        "</testsuites>"
    )
    out = redact_xml_document(raw)
    assert find_corpus_leaks(out) == []
