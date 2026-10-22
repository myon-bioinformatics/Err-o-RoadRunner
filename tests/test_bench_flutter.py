"""Golden benchmark: Flutter core JUnit records and dart-compile cascade."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import squad
from tests.benchmark_util import normalize_short_timing, run_benchmark

ROOT = Path(__file__).resolve().parents[1]
FLUTTER_CORE_CASES = sorted(
    (
        ROOT
        / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-0_78.xml",
        ROOT
        / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-1_77.xml",
    )
)
GOLDEN_DIR = ROOT / "tests/golden/benchmarks/flutter_core"


def _flutter_core_paths() -> list[str]:
    return [str(p) for p in FLUTTER_CORE_CASES]


def _records_without_language(doc: dict) -> list:
    out = []
    for rec in doc.get("records") or []:
        stripped = {k: v for k, v in rec.items() if k != "language"}
        out.append(stripped)
    return out


@pytest.mark.sentinel
def test_flutter_core_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(_flutter_core_paths())
    golden = normalize_short_timing(
        (GOLDEN_DIR / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_timing(short_text) == golden


@pytest.mark.sentinel
def test_flutter_core_benchmark_records_match_golden_except_language() -> None:
    _short_text, report = run_benchmark(_flutter_core_paths())
    golden_doc = json.loads((GOLDEN_DIR / "report.json").read_text(encoding="utf-8"))
    assert _records_without_language(report) == _records_without_language(golden_doc)
    records = report.get("records") or []
    assert len(records) == 12
    assert all(len(rec.get("variants") or []) == 2 for rec in records)
    languages = {rec.get("language") for rec in records}
    assert languages == {"dart"}
    error_types = {rec.get("error_type") for rec in records}
    assert "Unknown" not in error_types
    assert error_types == {"DartMissingImport"}


@pytest.mark.sentinel
def test_flutter_core_dart_compile_cascade_hypothesis() -> None:
    records = squad.load_failure_records_for_escalation(_flutter_core_paths())
    hyps = squad.build_cascade_hypotheses(records, ROOT, ("code",))
    dart_hyps = [h for h in hyps if h.get("summary") == "dart-compile-cascade"]
    assert len(dart_hyps) == 1
    hyp = dart_hyps[0]
    assert hyp.get("confidence") == "high"
    assert len(hyp.get("explains") or []) == 12


@pytest.mark.sentinel
def test_flutter_core_benchmark_no_missing_command_fix() -> None:
    _short_text, report = run_benchmark(_flutter_core_paths())
    kinds = {cand.get("kind") for cand in report.get("fix_candidates") or []}
    assert "missing_command" not in kinds
