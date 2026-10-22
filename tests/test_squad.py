"""Error squads and per-testcase JUnit failure records."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

import squad
from sentinel import build_escalation_report, build_groups, parse_group_by
from sentinel import RunOutcome, WatchState

ROOT = Path(__file__).resolve().parents[1]
AOI_GLOB = "Aoi_37561005125_junit-py3.*_*.xml"
AOI_CASES = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(AOI_GLOB))


def _aoi_paths() -> list[str]:
    return [str(p) for p in AOI_CASES]


@pytest.fixture
def contamination_root(tmp_path: Path) -> Path:
    (tmp_path / "neighbors.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "sets.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.sentinel
def test_aoi_merged_records_distinct_error_types_and_variants(contamination_root: Path) -> None:
    records = squad.load_failure_records_for_escalation(_aoi_paths(), [])
    assert records
    error_types = {rec["error_type"] for rec in records}
    assert len(error_types) >= 7
    assert "JUnitFailure" not in error_types
    assert max(len(rec.get("variants") or []) for rec in records) == 5

    errors = squad.records_to_normalized_errors(records)
    groups = build_groups(errors, group_by=parse_group_by("error_type"), sort="count")
    assert groups["error_count"] == len(records)
    assert len(groups["buckets"]) >= 7

    hyps = squad.build_cascade_hypotheses(records, contamination_root, ("code",))
    assert hyps
    h1 = hyps[0]
    assert h1["summary"] == "import-path contamination"
    explained = h1["explains"]
    assert len(explained) >= 5
    assert len(explained) <= 19


@pytest.mark.sentinel
def test_squad_timeout_does_not_block_other_squads() -> None:
    def slow_code(_ctx: squad.SquadContext) -> dict:
        time.sleep(2.0)
        return {
            "name": squad.SQUAD_CODE,
            "status": "ok",
            "elapsed_ms": 2000,
            "findings": [],
            "hypotheses": [],
        }

    with mock.patch.dict(squad._SQUAD_RUNNERS, {squad.SQUAD_CODE: slow_code}):
        pack = squad.run_error_squads(
            enabled=frozenset({squad.SQUAD_CODE, squad.SQUAD_HISTORY}),
            records=[],
            project_root=ROOT,
            junit_paths=[],
            corpus_dirs=[],
            offline=True,
            web_endpoints=[],
            squad_timeout=0.15,
            squad_budget=5.0,
            squad_workers=2,
        )
    by_name = {s["name"]: s for s in pack["squads"]}
    assert by_name[squad.SQUAD_CODE]["status"] == "timeout"
    assert by_name[squad.SQUAD_HISTORY]["status"] in ("ok", "skipped")


@pytest.mark.sentinel
def test_web_squad_offline_skips_network_and_redacts_queries() -> None:
    records = squad.merge_failure_records([AOI_CASES[0]])
    with mock.patch("urllib.request.urlopen") as urlopen_mock:
        pack = squad.run_error_squads(
            enabled=frozenset({squad.SQUAD_WEB}),
            records=records,
            project_root=ROOT,
            junit_paths=[],
            corpus_dirs=[],
            offline=True,
            web_endpoints=[],
            squad_timeout=1.0,
            squad_budget=5.0,
            squad_workers=1,
        )
    urlopen_mock.assert_not_called()
    web = next(s for s in pack["squads"] if s["name"] == squad.SQUAD_WEB)
    assert web["status"] == "skipped"
    assert web.get("queries")
    joined = " ".join(web["queries"])
    assert "/workspace" not in joined
    assert "token" not in joined.lower()


@pytest.mark.sentinel
def test_web_squad_not_in_default_squads() -> None:
    assert squad.SQUAD_WEB not in squad.DEFAULT_SQUADS
    assert squad.SQUAD_WEB not in squad.parse_squads_flag("code,history")


@pytest.mark.sentinel
def test_history_squad_git_blame_and_log(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    sample = tmp_path / "sample.py"
    sample.write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )
    records = [
        {
            "error_type": "AssertionError",
            "message": "assert False",
            "signature": "assert false",
            "test_id": "t::test_x",
            "source_file": "sample.py",
            "line": 2,
            "variants": ["py3.12"],
        }
    ]
    pack = squad.run_error_squads(
        enabled=frozenset({squad.SQUAD_HISTORY}),
        records=records,
        project_root=tmp_path,
        junit_paths=[],
        corpus_dirs=[],
        offline=True,
        web_endpoints=[],
        squad_timeout=5.0,
        squad_budget=10.0,
        squad_workers=1,
    )
    hist = pack["squads"][0]
    assert hist["status"] == "ok"
    assert hist["findings"]


@pytest.mark.sentinel
def test_sys_path_insert_flagged_in_code_squad(tmp_path: Path) -> None:
    conftest = tmp_path / "conftest.py"
    conftest.write_text("import sys\nsys.path.insert(0, 'bogus')\n", encoding="utf-8")
    pack = squad.run_code_squad(
        squad.SquadContext(
            records=[],
            project_root=tmp_path,
            junit_paths=(),
            corpus_dirs=(),
            offline=True,
            web_endpoints=(),
            timeout=5.0,
        )
    )
    kinds = {f.get("kind") for f in pack["findings"]}
    assert "sys.path.insert" in kinds


@pytest.mark.sentinel
def test_sentinel_exit_code_unchanged_with_squads(tmp_path: Path) -> None:
    junit = AOI_CASES[0]
    common = [
        sys.executable,
        str(ROOT / "sentinel.py"),
        "--junit",
        str(junit),
        "--report-json",
        str(tmp_path / "a.json"),
        "--",
        "sh",
        "-c",
        "exit 0",
    ]
    code_none = subprocess.run([*common[:6], "--squads", "none", *common[6:]], cwd=ROOT, capture_output=True, text=True).returncode
    code_squads = subprocess.run(
        [*common[:6], "--squads", "code,history", "--offline", *common[6:]],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).returncode
    assert code_none == code_squads == 0


@pytest.mark.sentinel
def test_squads_none_overhead_small() -> None:
    started = time.perf_counter()
    squad.run_error_squads(
        enabled=frozenset(),
        records=[],
        project_root=ROOT,
        junit_paths=[],
        corpus_dirs=[],
        offline=True,
        web_endpoints=[],
    )
    assert time.perf_counter() - started < 0.005


@pytest.mark.sentinel
def test_escalation_report_includes_squad_fields(contamination_root: Path) -> None:
    state = WatchState(command_argv=["pytest", "-q"], cwd=str(ROOT))
    outcome = RunOutcome(exit_code=1, effective_exit_code=1, junit_failure=True)
    doc = build_escalation_report(
        state,
        outcome,
        project_root=contamination_root,
        junit_paths=_aoi_paths(),
        corpus_dirs=[str(ROOT / "tests/fixtures/junit_corpus/cases")],
        actions_log_path=None,
        hooks=[],
        hook_timeout=5.0,
        squads_enabled=frozenset({squad.SQUAD_CODE, squad.SQUAD_HISTORY}),
        squad_offline=True,
    )
    assert doc.get("records")
    assert doc.get("squads")
    assert any(h.get("id") == "H1" for h in doc.get("hypotheses") or [])
    top = (doc.get("fix_candidates") or [None])[0]
    assert top and top.get("source_squads")
