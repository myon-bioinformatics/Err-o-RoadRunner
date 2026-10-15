"""Structural checks for the JSON / JSONL failure corpus (non-JUnit result formats)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from junit_redact import find_corpus_leaks

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "results_corpus"
MANIFEST = CORPUS / "manifest.json"
ENTRIES = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []


def test_manifest_ids_unique() -> None:
    ids = [e["id"] for e in ENTRIES]
    assert len(ids) == len(set(ids))


@pytest.mark.junit_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_results_corpus_entry(entry: dict) -> None:
    path = CORPUS / entry["data"]
    text = path.read_text(encoding="utf-8")
    assert entry["repo"].startswith("myon-bioinformatics/")
    assert entry["format"]
    if path.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert records and len(records) == entry.get("records", len(records))
    else:
        assert json.loads(text)
    assert find_corpus_leaks(text) == []
