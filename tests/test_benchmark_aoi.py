"""Golden benchmark: Aoi corpus short stdout and report stability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmark_util import normalize_short_timing, run_benchmark

ROOT = Path(__file__).resolve().parents[1]
AOI_GLOB = "Aoi_37561005125_junit-py3.*_*.xml"
AOI_CASES = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(AOI_GLOB))
GOLDEN_DIR = ROOT / "tests/golden/benchmarks/aoi_37561005125"


def _aoi_paths() -> list[str]:
    return [str(p) for p in AOI_CASES]


def _records_without_language(doc: dict) -> list:
    out = []
    for rec in doc.get("records") or []:
        stripped = {
            k: v
            for k, v in rec.items()
            if k not in ("language", "assert_left", "assert_right")
        }
        out.append(stripped)
    return out


@pytest.mark.sentinel
def test_aoi_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(_aoi_paths())
    golden = normalize_short_timing(
        (GOLDEN_DIR / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_timing(short_text) == golden


@pytest.mark.sentinel
def test_aoi_benchmark_records_match_golden_except_language() -> None:
    _short_text, report = run_benchmark(_aoi_paths())
    golden_doc = json.loads((GOLDEN_DIR / "report.json").read_text(encoding="utf-8"))
    assert _records_without_language(report) == _records_without_language(golden_doc)
    languages = {rec.get("language") for rec in report.get("records") or []}
    assert languages == {"python"}
