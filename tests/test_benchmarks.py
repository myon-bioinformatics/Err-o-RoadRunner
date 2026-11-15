"""Golden sentinel benchmarks over the JUnit corpus (tests/golden/benchmarks) and their normalization.

- bench_flutter: Golden benchmark: Flutter core JUnit records and dart-compile cascade.
- bench_large: Golden benchmark: Err-o-RoadRunner version-matrix JUnit (165KB single artifact).
- bench_matrix: Golden benchmark: CI matrix variants (yourself run 37583297348).
- bench_windows: Golden benchmark: Windows cli_args JUnit corpus (Case B).
- benchmark_aoi: Golden benchmark: Aoi corpus short stdout and report stability.
- benchmark_util: the benchmark normalization helpers in tests/support.py.
"""

from __future__ import annotations

import json, re

import pytest

import squad
from squad import parse_matrix_variant
from tests.support import (
    CORPUS_CASES,
    corpus_cases,
    median_merge_records_seconds,
    normalize_report_benchmark,
    normalize_short_benchmark,
    records_without,
    ROOT,
    run_benchmark,
)


# ──── bench_flutter ───────────────────────────────────────────────

FLUTTER_CORE_CASES = sorted(
    (
        ROOT
        / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-0_78.xml",
        ROOT
        / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-1_77.xml",
    )
)
FLUTTER_GOLDEN = ROOT / "tests/golden/benchmarks/flutter_core"


def _flutter_core_paths() -> list[str]:
    return [str(p) for p in FLUTTER_CORE_CASES]


@pytest.mark.sentinel
def test_flutter_core_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(_flutter_core_paths())
    golden = normalize_short_benchmark(
        (FLUTTER_GOLDEN / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_benchmark(short_text) == golden


@pytest.mark.sentinel
def test_flutter_core_benchmark_records_match_golden_except_language() -> None:
    _short_text, report = run_benchmark(_flutter_core_paths())
    golden_doc = json.loads((FLUTTER_GOLDEN / "report.json").read_text(encoding="utf-8"))
    assert records_without(report, "language") == records_without(golden_doc, "language")
    records = report.get("records") or []
    assert len(records) == 12
    assert all(len(rec.get("variants") or []) == 2 for rec in records)
    languages = {rec.get("language") for rec in records}
    assert languages == {"dart"}
    error_types = {rec.get("error_type") for rec in records}
    assert "Unknown" not in error_types
    assert error_types == {"DartMissingImport"}


@pytest.mark.sentinel
@pytest.mark.sentinel
def test_flutter_core_primary_import_location() -> None:
    records = squad.load_failure_records_for_escalation(_flutter_core_paths())
    rec = records[0]
    assert rec.get("source_file") == "lib/shared/catalog/catalog_search.dart"
    assert rec.get("line") == 1
    assert rec.get("source_file_secondary") == "lib/shared/catalog/catalog_search.dart"
    assert rec.get("line_secondary") == 7


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


# ──── bench_large ─────────────────────────────────────────────────

EORR_CASE = (
    ROOT
    / "tests/fixtures/junit_corpus/cases/Err-o-RoadRunner_38216257471_junit-py3.10_junit.xml"
)
AOI_GLOB = "Aoi_37561005125_junit-py3.*_*.xml"
SMALL_CASE = corpus_cases(AOI_GLOB)[0]
EORR_GOLDEN = ROOT / "tests/golden/benchmarks/eorr_version_matrix"
STALE_SUMMARY = "stale installed metadata or env override"


def _triage_group_lines(doc: dict) -> list[str]:
    return [
        line
        for line in doc.get("triage", {}).get("found") or []
        if re.search(r"junit failure group \d+/\d+", line)
    ]


@pytest.mark.sentinel
def test_eorr_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark([str(EORR_CASE)], squads="code,history")
    golden = normalize_short_benchmark(
        (EORR_GOLDEN / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_benchmark(short_text) == golden


@pytest.mark.sentinel
def test_eorr_benchmark_report_matches_golden_core_fields() -> None:
    _short_text, report = run_benchmark([str(EORR_CASE)], squads="code,history")
    golden_doc = json.loads((EORR_GOLDEN / "report.json").read_text(encoding="utf-8"))

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
    _short_text, report = run_benchmark([str(EORR_CASE)], squads="none", max_records=10)
    assert len(report.get("records") or []) == 10
    assert report.get("records_omitted") == 54


@pytest.mark.sentinel
def test_eorr_parse_perf_relative_to_small_baseline() -> None:
    baseline = median_merge_records_seconds(SMALL_CASE)
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


# ──── bench_matrix ────────────────────────────────────────────────

YOURSELF_GLOB = "yourself_37583297348_test-report-*_junit.xml"
YOURSELF_GOLDEN = ROOT / "tests/golden/benchmarks/yourself_matrix"


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (
            "repo_37583297348_test-report-ubuntu-latest-py3.10_junit.xml",
            {
                "os": "ubuntu-latest",
                "python": "py3.10",
                "flavor": "test-report",
            },
        ),
        (
            "Aoi_37561005125_junit-py3.11_foo.xml",
            {"python": "py3.11", "flavor": "junit"},
        ),
        (
            "Ironmate_38012731947_junit-controlled-py3.12_59.xml",
            {"python": "py3.12", "flavor": "controlled"},
        ),
    ],
)
def test_parse_matrix_variant(artifact: str, expected: dict[str, str]) -> None:
    assert parse_matrix_variant(artifact) == expected


@pytest.mark.sentinel
def test_yourself_matrix_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(corpus_cases(YOURSELF_GLOB))
    golden = normalize_short_benchmark(
        (YOURSELF_GOLDEN / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_benchmark(short_text) == golden


@pytest.mark.sentinel
def test_yourself_matrix_report_has_invariant_hypothesis_and_matrix() -> None:
    short_text, report = run_benchmark(corpus_cases(YOURSELF_GLOB))
    assert len(report.get("records") or []) == 1
    record = report["records"][0]
    assert len(record.get("variants") or []) == 5
    assert record.get("variant_axes", {}).get("os") == [
        "macos-latest",
        "ubuntu-latest",
        "windows-latest",
    ]
    matrix = report.get("matrix") or {}
    assert matrix.get("total") == 7
    assert matrix.get("failing") == 7
    assert matrix.get("axes") == {"flavor": 1, "os": 3, "python": 5}
    hypotheses = report.get("hypotheses") or []
    invariant = [h for h in hypotheses if h.get("summary") == "matrix-invariant failure"]
    assert len(invariant) == 1
    assert invariant[0].get("classification") == "invariant"
    assert "M fails on all 7 variants (os: 3/3, python: 5/5)" in short_text
    golden_doc = json.loads((YOURSELF_GOLDEN / "report.json").read_text(encoding="utf-8"))
    normalized = normalize_report_benchmark(report)
    assert normalized.get("matrix") == golden_doc.get("matrix")
    assert normalized.get("hypotheses") == golden_doc.get("hypotheses")


# ──── bench_windows ───────────────────────────────────────────────

WINDOWS_GLOB = "cli_args_38186*junit-core-windows*.xml"
WINDOWS_CASES = sorted(CORPUS_CASES.glob(WINDOWS_GLOB))
WINDOWS_GOLDEN = ROOT / "tests/golden/benchmarks/cli_args_windows"


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_has_five_junit_variants() -> None:
    assert len(WINDOWS_CASES) == 5


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(corpus_cases(WINDOWS_GLOB))
    golden = normalize_short_benchmark(
        (WINDOWS_GOLDEN / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_benchmark(short_text) == golden
    assert "junit failures (5 variants): 1" in short_text


@pytest.mark.sentinel
def test_windows_cli_args_benchmark_records_match_golden() -> None:
    _short_text, report = run_benchmark(corpus_cases(WINDOWS_GLOB))
    golden_doc = json.loads((WINDOWS_GOLDEN / "report.json").read_text(encoding="utf-8"))
    assert records_without(report, "language") == records_without(golden_doc, "language")
    records = report.get("records") or []
    assert len(records) == 1
    rec = records[0]
    assert rec.get("error_type") != "Unknown"
    assert str(rec.get("variant") or "").startswith("windows:")
    variants = rec.get("variants") or []
    assert len(variants) == 5
    assert all(str(v).startswith("windows:") for v in variants)
    axes = rec.get("variant_axes") or {}
    assert axes.get("os") == ["windows"]
    assert len(axes.get("flavor") or []) == 5
    assert rec.get("expected_exception") == "FileExistsError"
    assert rec.get("source_file") == "tests/test_cli_args.py"
    assert rec.get("line") in (304, 311)
    assert rec.get("language") == "python"


@pytest.mark.sentinel
def test_windows_cli_args_platform_specific_hypothesis() -> None:
    records = squad.load_failure_records_for_escalation(corpus_cases(WINDOWS_GLOB))
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


# ──── benchmark_aoi ───────────────────────────────────────────────

AOI_GOLDEN = ROOT / "tests/golden/benchmarks/aoi_37561005125"


@pytest.mark.sentinel
def test_aoi_benchmark_short_stdout_matches_golden() -> None:
    short_text, _report = run_benchmark(corpus_cases(AOI_GLOB))
    golden = normalize_short_benchmark(
        (AOI_GOLDEN / "short.txt").read_text(encoding="utf-8")
    )
    assert normalize_short_benchmark(short_text) == golden


@pytest.mark.sentinel
def test_aoi_benchmark_records_match_golden_except_language() -> None:
    _short_text, report = run_benchmark(corpus_cases(AOI_GLOB))
    golden_doc = json.loads((AOI_GOLDEN / "report.json").read_text(encoding="utf-8"))
    assert records_without(report, "language", "assert_left", "assert_right") == records_without(golden_doc, "language", "assert_left", "assert_right")
    languages = {rec.get("language") for rec in report.get("records") or []}
    assert languages == {"python"}


# ──── benchmark_util ──────────────────────────────────────────────

BENCHMARK_REPORT_GLOB = "tests/golden/benchmarks/*/report.json"
_FORBIDDEN_ENV_VALUE_RE = re.compile(
    r'"(?:HOME|PATH|PWD|OLDPWD|CURSOR_[A-Z0-9_]+)":\s*"(?!<(?:masked|redacted)>)[^"]+'
)


def test_normalize_short_benchmark_masks_tool_versions() -> None:
    raw = (
        "F cmd\n"
        "  python version: 3.12.3 (cpython)\n"
        "  node version: v22.23.4\n"
        "  gh version: gh version 2.102.0 (2026-09-30)\n"
        "== 1 failed in 0.01s ==\n"
    )
    out = normalize_short_benchmark(raw)
    assert "3.12.3" not in out
    assert "python version: <masked>" in out
    assert "node version: <masked>" in out
    assert "gh version: <masked>" in out


@pytest.mark.sentinel
def test_normalize_report_benchmark_clears_environment_values() -> None:
    doc = {
        "environment": {
            "variable_count": 2,
            "variable_names": ["HOME", "PATH"],
            "values_redacted": {"HOME": "/secret", "PATH": "/bin"},
        }
    }
    out = normalize_report_benchmark(doc)
    assert out["environment"]["values_redacted"] == {}
    assert out["environment"]["variable_names"] == ["<masked>"]


@pytest.mark.sentinel
def test_benchmark_golden_reports_contain_no_raw_env_values() -> None:
    paths = sorted(ROOT.glob(BENCHMARK_REPORT_GLOB))
    assert paths, "expected benchmark report goldens"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not _FORBIDDEN_ENV_VALUE_RE.search(text), (
            f"{path.relative_to(ROOT)} contains raw HOME/PATH/CURSOR_* env values"
        )
        doc = json.loads(text)
        values = (doc.get("environment") or {}).get("values_redacted") or {}
        assert values == {}, f"{path.name} values_redacted must be empty"
