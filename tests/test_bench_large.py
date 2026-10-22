"""Golden benchmark: Err-o-RoadRunner version-matrix JUnit (165KB single artifact)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import squad
from tests.benchmark_util import (
    median_merge_records_seconds,
    normalize_short_benchmark,
    run_benchmark,
)

ROOT = Path(__file__).resolve().parents[1]
EORR_CASE = (
    ROOT
    / "tests/fixtures/junit_corpus/cases/Err-o-RoadRunner_38216257471_junit-py3.10_junit.xml"
)
AOI_GLOB = "Aoi_37561005125_junit-py3.*_*.xml"
SMALL_CASE = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(AOI_GLOB))[0]
GOLDEN_DIR = ROOT / "tests/golden/benchmarks/eorr_version_matrix"
STALE_SUMMARY = "stale installed metadata or env override"


def _eorr_path() -> str:
    return str(EORR_CASE)


def _triage_group_lines(doc: dict) -> list[str]:
    return [
        line
        for line in doc.get("triage", {}).get("found") or []
        if re.search(r"junit failure group \d+/\d+", line)
    ]


@pytest.mark.sentinel
def test_eorr_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark([_eorr_path()], squads="code,history")
    golden = normalize_short_benchmark(
        (GOLDEN_DIR / "short.txt").read_text(encoding="utf-8"),
        ROOT,
    )
    assert normalize_short_benchmark(short_text, ROOT) == golden


@pytest.mark.sentinel
def test_eorr_benchmark_report_matches_golden_core_fields() -> None:
    _short_text, report = run_benchmark([_eorr_path()], squads="code,history")
    golden_doc = json.loads((GOLDEN_DIR / "report.json").read_text(encoding="utf-8"))

    assert len(report.get("records") or []) == 64
    assert all(rec.get("assert_left") and rec.get("assert_right") for rec in report["records"])
    assert len(_triage_group_lines(report)) == 3
    assert report.get("top_causes") == golden_doc.get("top_causes")
    assert len(report.get("top_causes") or []) == 3

    summaries = {hyp.get("summary") for hyp in report.get("hypotheses") or []}
    assert STALE_SUMMARY in summaries
    stale = next(h for h in report["hypotheses"] if h.get("summary") == STALE_SUMMARY)
    assert len(stale.get("explains") or []) >= 2

    fixes = report.get("fix_candidates") or []
    assert any(
        STALE_SUMMARY in (fix.get("suggestion") or "")
        or "pip install -e ." in (fix.get("suggestion") or "")
        for fix in fixes
    )


@pytest.mark.sentinel
def test_eorr_max_records_omitted_count() -> None:
    _short_text, report = run_benchmark([_eorr_path()], squads="none", max_records=10)
    assert len(report.get("records") or []) == 10
    assert report.get("records_omitted") == 54


@pytest.mark.sentinel
def test_eorr_parse_perf_relative_to_small_baseline() -> None:
    baseline = median_merge_records_seconds(str(SMALL_CASE))
    target = median_merge_records_seconds(str(EORR_CASE))
    assert target < baseline * 25 + 0.05, (
        f"eorr parse median {target:.4f}s vs small baseline {baseline:.4f}s"
    )


@pytest.mark.sentinel
def test_extract_assert_operands_redacts_and_caps() -> None:
    blob = "E       AssertionError: assert '/home/runner/work/pkg' == '7.7.7'\n"
    left, right = squad.extract_assert_operands("", blob, "")
    assert left is not None and "runner/work" not in left and "<" in left
    assert right == "'7.7.7'"
    home_left, _ = squad.extract_assert_operands("assert '/home/u/x' == 'ok'", "", "")
    assert home_left is not None and "<path>" in home_left and "/home/u" not in home_left
    long_val = "'" + ("x" * 120) + "'"
    left2, _ = squad.extract_assert_operands(f"assert {long_val} == '1'", "", "")
    assert left2 is not None and len(left2) <= 80
