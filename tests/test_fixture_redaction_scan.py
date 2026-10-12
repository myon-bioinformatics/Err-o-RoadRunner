"""Scan all checked-in fixture XML and synthetic logs for redaction leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from junit_redact import find_corpus_leaks

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

# Full GitHub Actions job captures; runner paths are intentional test inputs.
LOG_SCAN_SKIP_PREFIX = FIXTURES / "actions_logs" / "real"

SCAN_SUFFIXES = (".xml", ".out", ".log", ".stderr", ".json")


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
