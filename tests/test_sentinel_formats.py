"""Sentinel stdout formats, fix_candidates rules, and error grouping."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sentinel import (
    FORMAT_FULL,
    FORMAT_PORCELAIN,
    FORMAT_SHORT,
    FORMAT_TABLE,
    SCHEMA_VERSION,
    _FIX_RULES,
    build_escalation_report,
    build_fix_candidates,
    build_groups,
    extract_normalized_errors,
    parse_group_by,
    render_stdout_format,
    render_stop_stdout,
)
from sentinel import RingBuffer, RunOutcome, WatchState

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "sentinel"
GOLDEN = FIX / "golden"


def _load_struct(name: str) -> dict[str, list[str]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _doc_from_structured(
    structured: dict[str, list[str]],
    outcome: RunOutcome | None = None,
    *,
    group_by: tuple[str, ...] = ("error_type",),
    sort: str = "count",
) -> dict:
    outcome = outcome or RunOutcome(exit_code=1, effective_exit_code=1)
    reproduce = "pytest -q"
    errors = extract_normalized_errors(structured, outcome)
    return {
        "schema": SCHEMA_VERSION,
        "mode": "escalated",
        "triage": structured,
        "command": {"reproduce_command": reproduce},
        "timing": {"elapsed_seconds": 0.0},
        "outcome": {
            "exit_code": outcome.effective_exit_code,
            "raw_exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "junit_failure": outcome.junit_failure,
        },
        "fix_candidates": build_fix_candidates(structured, reproduce_command=reproduce),
        "groups": build_groups(errors, group_by=group_by, sort=sort),
    }


@pytest.mark.sentinel
def test_parse_group_by_keys() -> None:
    assert parse_group_by("error_type,language") == ("error_type", "language")
    with pytest.raises(ValueError):
        parse_group_by("error_type,bogus")


@pytest.mark.sentinel
@pytest.mark.parametrize(
    "group_by",
    [
        ("error_type",),
        ("language",),
        ("phase",),
        ("signature",),
        ("location",),
        ("kind",),
    ],
)
def test_group_by_golden_keys(group_by: tuple[str, ...]) -> None:
    structured = _load_struct("pydantic_validation.found.json")
    outcome = RunOutcome(exit_code=1, effective_exit_code=1)
    errors = extract_normalized_errors(structured, outcome)
    groups = build_groups(errors, group_by=group_by, sort="count")
    golden_path = GOLDEN / f"groups_{'_'.join(group_by)}_pydantic.txt"
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(
        f"{b['name']}\t{b['count']}\t{b['first_seen']}" for b in groups["buckets"]
    ) + "\n"
    if not golden_path.exists():
        golden_path.write_text(rendered, encoding="utf-8")
    assert rendered == golden_path.read_text(encoding="utf-8")
    assert groups["buckets"][0]["errors"][0]["error_type"] == "ValidationError"


@pytest.mark.sentinel
def test_sql_error_type_grouping() -> None:
    structured = _load_struct("sql_operational.found.json")
    errors = extract_normalized_errors(structured, RunOutcome(exit_code=1, effective_exit_code=1))
    groups = build_groups(errors, group_by=("error_type",), sort="name")
    assert groups["buckets"][0]["key"]["error_type"] == "sqlite3.OperationalError"


@pytest.mark.sentinel
@pytest.mark.parametrize(
    ("rule", "line"),
    [
        (_FIX_RULES[0], "python exception type: ModuleNotFoundError"),
        (_FIX_RULES[1], "python exception type: ImportError"),
        (_FIX_RULES[2], "exit code: 127"),
        (_FIX_RULES[3], "exit code: 126"),
        (_FIX_RULES[4], "exit code: 124"),
        (_FIX_RULES[5], "Cannot find module '@playwright/test'"),
        (_FIX_RULES[6], "python exception type: AssertionError"),
        (_FIX_RULES[7], "junit failure (first) (x.xml): tests/view-mode.spec.js:104 expect(locator).toHaveCount"),
        (_FIX_RULES[8], "python exception type: SyntaxError"),
        (_FIX_RULES[9], "actions log failing step (log): Run tests"),
        (_FIX_RULES[10], "exit code: 9"),
    ],
    ids=[r.kind for r in _FIX_RULES],
)
def test_fix_rule_regex_matches_fixture_line(rule, line: str) -> None:
    assert rule.found_re.search(line), f"{rule.kind} should match {line}"


@pytest.mark.sentinel
def test_playwright_junit_fix_candidate() -> None:
    structured = {
        "found": [
            "junit failure (first) (j.xml): tests/view-mode.spec.js:104 expect(locator).toHaveCount failed",
            "junit testcase classname (first) (j.xml): view-mode.spec.js",
        ],
        "not_found": [],
        "hints": [],
    }
    fixes = build_fix_candidates(structured, reproduce_command="npx playwright test")
    assert fixes and fixes[0]["kind"] == "junit"
    assert "view-mode.spec.js" in (fixes[0].get("location") or {}).get("loc", "")


@pytest.mark.sentinel
def test_format_full_golden() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_FULL, tb="short", verbose=False)
    path = GOLDEN / "format_full.txt"
    if not path.exists():
        path.write_text(out, encoding="utf-8")
    assert out == path.read_text(encoding="utf-8")


@pytest.mark.sentinel
def test_format_short_golden() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(
        doc,
        fmt=FORMAT_SHORT,
        tb="short",
        verbose=True,
        outcome=RunOutcome(exit_code=1, effective_exit_code=1),
    )
    path = GOLDEN / "format_short.txt"
    if not path.exists():
        path.write_text(out, encoding="utf-8")
    assert "==" in out
    assert out == path.read_text(encoding="utf-8")


@pytest.mark.sentinel
def test_format_porcelain_header_and_c_rows() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_PORCELAIN, tb="no", verbose=False)
    assert out.startswith(f"# {SCHEMA_VERSION}\t")
    assert "C\tF1\t" in out
    assert "# totals\t" in out


@pytest.mark.sentinel
def test_format_table_uses_groups() -> None:
    doc = _doc_from_structured(_load_struct("pydantic_validation.found.json"))
    out = render_stdout_format(doc, fmt=FORMAT_TABLE, tb="no", verbose=False)
    assert "Group" in out and "Count" in out
    assert "error_type=ValidationError" in out


@pytest.mark.sentinel
def test_stop_short_line() -> None:
    line = render_stop_stdout(fmt=FORMAT_SHORT, exit_code=0, elapsed_seconds=0.05)
    assert line == "sentinel: STOP exit=0 0.05s"


@pytest.mark.sentinel
def test_stop_porcelain_empty() -> None:
    assert render_stop_stdout(fmt=FORMAT_PORCELAIN, exit_code=0, elapsed_seconds=0.01) == ""


@pytest.mark.sentinel
def test_report_json_includes_fix_candidates_and_groups(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "triage",
            "sentinel",
            "--report-json",
            str(report),
            "--print-triage",
            "--",
            sys.executable,
            "-c",
            "raise ModuleNotFoundError('no_pkg_xyz')",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc.get("fix_candidates")
    assert doc.get("groups", {}).get("buckets")


@pytest.mark.sentinel
def test_stdout_lines_max_200_chars() -> None:
    long_msg = "x" * 300
    structured = {
        "found": [f"python exception message: {long_msg}"],
        "not_found": [],
        "hints": [],
    }
    doc = _doc_from_structured(structured)
    out = render_stdout_format(doc, fmt=FORMAT_FULL, tb="short", verbose=False)
    for line in out.splitlines():
        assert len(line) <= 200
