"""Harvested corpus validation, run by the harvest workflow's "Validate corpus" step: JUnit goldens, JSON/JSONL structure, no redaction leaks.

- junit_corpus_golden: Golden tests for real failed-run JUnit XML corpus.
- results_corpus: Structural checks for the JSON / JSONL failure corpus (non-JUnit result formats).
- fixture_redaction_scan: Scan all checked-in fixture XML and synthetic logs for redaction leaks.
"""

from __future__ import annotations

import json, re
from pathlib import Path

import pytest

from errlib.junit_redact import find_corpus_leaks
from tests.support import FIXTURES, normalize_report, triage_cli


# ──── junit_corpus_golden ─────────────────────────────────────────

CORPUS = FIXTURES / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"


@pytest.mark.junit_corpus
@pytest.mark.parametrize(
    "entry",
    json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else [],
    ids=lambda e: e["id"],
)
def test_junit_corpus_golden(entry: dict) -> None:
    xml_path = CORPUS / entry["xml"]
    golden_path = CORPUS / entry["golden"]
    expected = normalize_report(golden_path.read_text(encoding="utf-8"))
    actual = normalize_report(triage_cli("--junit", xml_path))
    assert actual == expected
    assert re.search(r"FOUND: junit failures|FOUND: junit errors", actual)


# ──── results_corpus ──────────────────────────────────────────────

RESULTS_CORPUS = FIXTURES / "results_corpus"
RESULTS_MANIFEST = RESULTS_CORPUS / "manifest.json"
ENTRIES = json.loads(RESULTS_MANIFEST.read_text(encoding="utf-8")) if RESULTS_MANIFEST.exists() else []


def test_manifest_ids_unique() -> None:
    ids = [e["id"] for e in ENTRIES]
    assert len(ids) == len(set(ids))


@pytest.mark.junit_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_results_corpus_entry(entry: dict) -> None:
    path = RESULTS_CORPUS / entry["data"]
    text = path.read_text(encoding="utf-8")
    assert entry["repo"].startswith("myon-bioinformatics/")
    assert entry["format"]
    if path.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert records and len(records) == entry.get("records", len(records))
    else:
        assert json.loads(text)
    assert find_corpus_leaks(text) == []


# ──── fixture_redaction_scan ──────────────────────────────────────



# Full GitHub Actions job captures; runner paths are intentional test inputs.
LOG_SCAN_SKIP_PREFIX = FIXTURES / "actions_logs" / "real"
SCAN_SUFFIXES = (".xml", ".out", ".log", ".stderr", ".json", ".jsonl")


def _fixture_scan_paths() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        if path.is_relative_to(LOG_SCAN_SKIP_PREFIX):
            continue
        paths.append(path)
    return paths


@pytest.mark.junit_corpus
@pytest.mark.parametrize(
    "fixture_path",
    _fixture_scan_paths(),
    ids=lambda p: p.relative_to(FIXTURES).as_posix(),
)
def test_fixture_file_has_no_redaction_leaks(fixture_path: Path) -> None:
    text = fixture_path.read_text(encoding="utf-8", errors="replace")
    leaks = find_corpus_leaks(text)
    assert not leaks, f"{fixture_path.relative_to(FIXTURES)}: {', '.join(leaks)}"
