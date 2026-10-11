"""Scan all checked-in fixture XML and synthetic logs for redaction leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from err_o_roadrunner.junit_redact import find_corpus_leaks

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

# Full GitHub Actions job captures; runner paths are intentional test inputs.
LOG_SCAN_SKIP_PREFIX = FIXTURES / "actions_logs" / "real"


def _fixture_xml_paths() -> list[Path]:
    return sorted(FIXTURES.rglob("*.xml"))


def _fixture_log_paths() -> list[Path]:
    out: list[Path] = []
    for path in sorted(FIXTURES.rglob("*.log")):
        if path.is_relative_to(LOG_SCAN_SKIP_PREFIX):
            continue
        out.append(path)
    return out


@pytest.mark.junit_corpus
@pytest.mark.parametrize("xml_path", _fixture_xml_paths(), ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_fixture_xml_has_no_redaction_leaks(xml_path: Path) -> None:
    text = xml_path.read_text(encoding="utf-8")
    leaks = find_corpus_leaks(text)
    assert not leaks, f"{xml_path.relative_to(FIXTURES)}: {', '.join(leaks)}"


@pytest.mark.junit_corpus
@pytest.mark.parametrize("log_path", _fixture_log_paths(), ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_fixture_log_has_no_redaction_leaks(log_path: Path) -> None:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    leaks = find_corpus_leaks(text)
    assert not leaks, f"{log_path.relative_to(FIXTURES)}: {', '.join(leaks)}"
