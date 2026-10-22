"""Golden benchmark: CI matrix variants (yourself run 37583297348)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from squad import parse_matrix_variant
from tests.benchmark_util import normalize_short_timing, run_benchmark

ROOT = Path(__file__).resolve().parents[1]
YOURSELF_GLOB = "yourself_37583297348_test-report-*_junit.xml"
YOURSELF_CASES = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(YOURSELF_GLOB))
GOLDEN_DIR = ROOT / "tests/golden/benchmarks/yourself_matrix"


def _yourself_paths() -> list[str]:
    return [str(p) for p in YOURSELF_CASES]


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
    short_text, _report = run_benchmark(_yourself_paths())
    golden = normalize_short_timing((GOLDEN_DIR / "short.txt").read_text(encoding="utf-8"))
    assert normalize_short_timing(short_text) == golden


@pytest.mark.sentinel
def test_yourself_matrix_report_has_invariant_hypothesis_and_matrix() -> None:
    short_text, report = run_benchmark(_yourself_paths())
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
    golden_doc = json.loads((GOLDEN_DIR / "report.json").read_text(encoding="utf-8"))
    assert report.get("matrix") == golden_doc.get("matrix")
    assert report.get("hypotheses") == golden_doc.get("hypotheses")
