"""Golden benchmark: Windows cli_args JUnit corpus (Case B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import squad
from tests.benchmark_util import normalize_short_timing, run_benchmark

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_GLOB = "cli_args_38186*junit-core-windows*.xml"
WINDOWS_CASES = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(WINDOWS_GLOB))
GOLDEN_DIR = ROOT / "tests/golden/benchmarks/cli_args_windows"


def _windows_paths() -> list[str]:
    return [str(p) for p in WINDOWS_CASES]


def _records_without_language(doc: dict) -> list:
    out = []
    for rec in doc.get("records") or []:
        stripped = {k: v for k, v in rec.items() if k != "language"}
        out.append(stripped)
    return out


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_has_five_junit_variants() -> None:
    assert len(WINDOWS_CASES) == 5


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(_windows_paths())
    golden = normalize_short_timing(
        (GOLDEN_DIR / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_timing(short_text) == golden
    assert "junit failures (5 variants): 1" in short_text


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_records_match_golden() -> None:
    _short_text, report = run_benchmark(_windows_paths())
    golden_doc = json.loads((GOLDEN_DIR / "report.json").read_text(encoding="utf-8"))
    assert _records_without_language(report) == _records_without_language(golden_doc)
    records = report.get("records") or []
    assert len(records) == 1
    rec = records[0]
    assert rec.get("error_type") != "Unknown"
    assert rec.get("variant") == "windows"
    assert rec.get("variants") == ["windows"]
    assert rec.get("expected_exception") == "FileExistsError"
    assert rec.get("source_file") == "tests/test_cli_args.py"
    assert rec.get("line") in (304, 311)
    assert rec.get("language") == "python"


@pytest.mark.sentinel
def test_windows_cli_args_platform_specific_hypothesis() -> None:
    records = squad.load_failure_records_for_escalation(_windows_paths())
    hyps = squad.build_cascade_hypotheses(records, ROOT, ("code",))
    platform = [h for h in hyps if h.get("summary") == "platform-specific failure"]
    assert platform
    assert platform[0]["confidence"] == "medium"
    test_id = "tests.test_cli_args::test_exclusive_output_never_overwrites_existing_file_or_symlink"
    assert test_id in platform[0]["explains"]

    enriched = squad.enrich_fix_candidates(
        [{"verify_command": "pytest", "id": "F0", "kind": "generic"}],
        platform,
    )
    assert enriched[0]["kind"] == "platform_specific"
