"""Ensure checked-in JUnit corpus fixtures contain no host/path/token leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from err_o_roadrunner.junit_redact import find_corpus_leaks, redact_xml_document

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"


def _corpus_xml_paths() -> list[Path]:
    paths = sorted((CORPUS / "cases").glob("*.xml"))
    paths += sorted((CORPUS / "inputs").glob("*.xml"))
    return paths


@pytest.mark.junit_corpus
def test_redact_xml_document_strips_runner_hostname_attribute() -> None:
    raw = (
        '<?xml version="1.0"?><testsuites>'
        '<testsuite name="pytest" hostname="runnervmabc" timestamp="2026-10-11T12:00:00+00:00"/>'
        "</testsuites>"
    )
    out = redact_xml_document(raw)
    assert find_corpus_leaks(out) == []


@pytest.mark.junit_corpus
@pytest.mark.parametrize("xml_path", _corpus_xml_paths(), ids=lambda p: p.name)
def test_junit_corpus_xml_has_no_redaction_leaks(xml_path: Path) -> None:
    text = xml_path.read_text(encoding="utf-8")
    leaks = find_corpus_leaks(text)
    assert not leaks, f"{xml_path.name}: {', '.join(leaks)}"
