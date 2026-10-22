"""Error squads and per-testcase JUnit failure records."""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

import squad
from sentinel import (
    build_escalation_report,
    build_groups,
    dedupe_multisource_triage,
    parse_group_by,
    resolve_default_corpus_dirs,
)
from sentinel import RunOutcome, WatchState

ROOT = Path(__file__).resolve().parents[1]
AOI_GLOB = "Aoi_37561005125_junit-py3.*_*.xml"
AOI_CASES = sorted((ROOT / "tests/fixtures/junit_corpus/cases").glob(AOI_GLOB))
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


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
def test_aoi_merged_records_distinct_error_types_and_variants() -> None:
    records = squad.load_failure_records_for_escalation(_aoi_paths())
    assert len(records) == 19
    assert all(len(rec.get("variants") or []) == 5 for rec in records)
    error_types = {rec["error_type"] for rec in records}
    assert len(error_types) >= 7
    assert "JUnitFailure" not in error_types

    errors = squad.records_to_normalized_errors(records)
    groups = build_groups(errors, group_by=parse_group_by("error_type"), sort="count")
    assert groups["error_count"] == 19
    assert len(groups["buckets"]) >= 7


@pytest.mark.sentinel
def test_aoi_h1_without_fake_repo_modules() -> None:
    records = squad.load_failure_records_for_escalation(_aoi_paths())
    hyps = squad.merge_squad_hypotheses([], records=records, project_root=ROOT)
    import_hyp = next(
        h for h in hyps if h.get("summary") == "import-path contamination"
    )
    assert import_hyp["confidence"] == "medium"
    assert len(import_hyp.get("explains") or []) >= 2


@pytest.mark.sentinel
def test_merge_hypotheses_assigns_unique_ids_for_multiple_cascades() -> None:
    flutter_paths = [
        str(
            ROOT
            / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-0_78.xml"
        ),
        str(
            ROOT
            / "tests/fixtures/junit_corpus/cases/flutter_navigation_basic_38139683688_junit-flutter-core-1_77.xml"
        ),
    ]
    records = squad.load_failure_records_for_escalation(_aoi_paths() + flutter_paths)
    hyps = squad.merge_squad_hypotheses([], records=records, project_root=ROOT)
    summaries = {h.get("summary") for h in hyps}
    assert "import-path contamination" in summaries
    assert "dart-compile-cascade" in summaries
    ids = [h.get("id") for h in hyps]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"H1", "H2"}


@pytest.mark.sentinel
def test_normalize_signature_strips_did_you_mean() -> None:
    a = squad.normalize_signature("No module named 'sets'")
    b = squad.normalize_signature("No module named 'sets'. Did you mean: 'tests'?")
    assert a == b


@pytest.mark.sentinel
def test_benchmark_default_corpus_keeps_nineteen_records(tmp_path: Path) -> None:
    import json

    junit_args = sum([["--junit", str(p)] for p in AOI_CASES], [])
    report = tmp_path / "bench.json"
    subprocess.run(
        [
            *SENTINEL_CLI,
            "--squads",
            "code,history",
            "--offline",
            "--report-json",
            str(report),
            *junit_args,
            "--",
            "sh",
            "-c",
            "exit 0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    doc = json.loads(report.read_text(encoding="utf-8"))
    records = doc.get("records") or []
    assert len(records) == 19
    assert all(len(rec.get("variants") or []) == 5 for rec in records)
    h1 = next(h for h in doc.get("hypotheses") or [] if h.get("id") == "H1")
    assert len(h1.get("explains") or []) <= len(records)
    assert len(h1["explains"]) >= 2


@pytest.mark.sentinel
def test_triage_dedupe_across_identical_junit_files(tmp_path: Path) -> None:
    import json

    junit_args = sum([["--junit", str(p)] for p in AOI_CASES], [])
    report = tmp_path / "r.json"

    def _run(extra_junit: list[str]) -> dict:
        subprocess.run(
            [
                *SENTINEL_CLI,
                "--squads",
                "none",
                "--report-json",
                str(report),
                *extra_junit,
                "--",
                "sh",
                "-c",
                "exit 0",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(report.read_text(encoding="utf-8"))

    multi = _run(junit_args)
    single = _run(["--junit", str(AOI_CASES[0])])
    assert len(multi["triage"]["found"]) == len(single["triage"]["found"])
    assert len(multi["triage"]["found"]) < 92
    assert any("variants" in line for line in multi["triage"]["found"])


@pytest.mark.sentinel
def test_short_stdout_dedupes_junit_variants(tmp_path: Path) -> None:
    junit_args = sum([["--junit", str(p)] for p in AOI_CASES], [])
    proc = subprocess.run(
        [
            *SENTINEL_CLI,
            "--squads",
            "none",
            "--format",
            "short",
            *junit_args,
            "--",
            "sh",
            "-c",
            "exit 0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.count("variants") >= 1
    assert proc.stdout.count("junit failures") == 1


@pytest.mark.sentinel
def test_porcelain_stdout_dedupes_junit_variants(tmp_path: Path) -> None:
    junit_args = sum([["--junit", str(p)] for p in AOI_CASES], [])
    proc = subprocess.run(
        [
            *SENTINEL_CLI,
            "--squads",
            "none",
            "--format",
            "porcelain",
            *junit_args,
            "--",
            "sh",
            "-c",
            "exit 0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    found_rows = [ln for ln in proc.stdout.splitlines() if ln.startswith("F\tfound\t")]
    junit_rows = [ln for ln in found_rows if "junit failures" in ln]
    assert len(junit_rows) == 1
    assert "variants" in junit_rows[0]


@pytest.mark.sentinel
def test_default_corpus_dir_when_present() -> None:
    resolved = resolve_default_corpus_dirs([], ROOT)
    assert resolved
    assert "junit_corpus" in resolved[0]


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
    records = [
        {
            "error_type": "ModuleNotFoundError",
            "message": f"token=abc secret at {ROOT / 'nagoyaction/tests/test_inbox.py'}",
            "signature": "no module",
            "test_id": "pkg.tests.test_x::test_y",
            "source_file": "nagoyaction/tests/test_inbox.py",
            "line": 10,
            "variants": ["py3.12"],
        }
    ]
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
    joined = " ".join(web.get("queries") or [])
    assert "token=abc" not in joined
    assert str(ROOT) not in joined
    assert "test_inbox" not in joined.lower() or "<path>" in joined.lower()


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
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
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
        project_root_explicit=True,
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
    test_file = tmp_path / "test_sample.py"
    test_file.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    pack = squad.run_code_squad(
        squad.SquadContext(
            records=[
                {
                    "test_id": "t::test_x",
                    "source_file": "test_sample.py",
                    "line": 2,
                }
            ],
            project_root=tmp_path,
            project_root_explicit=True,
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
def test_code_squad_aoi_does_not_flag_err_o_roadrunner_tests() -> None:
    records = squad.load_failure_records_for_escalation(_aoi_paths())
    pack = squad.run_code_squad(
        squad.SquadContext(
            records=records,
            project_root=ROOT,
            project_root_explicit=False,
            junit_paths=tuple(_aoi_paths()),
            corpus_dirs=(),
            offline=True,
            web_endpoints=(),
            timeout=5.0,
        )
    )
    flagged = {
        f.get("file", "")
        for f in pack["findings"]
        if f.get("kind") in ("sys.path.insert", "sys.modules")
    }
    assert not flagged
    for finding in pack["findings"]:
        file_ref = str(finding.get("file") or "")
        assert "/workspace" not in file_ref
        assert "test_harvest_junit_corpus" not in file_ref
        assert "test_squad.py" not in file_ref


@pytest.mark.sentinel
def test_history_corpus_matches_earlier_aoi_runs() -> None:
    corpus = str(ROOT / "tests/fixtures/junit_corpus/cases")
    records = squad.load_failure_records_for_escalation(_aoi_paths())
    target_runs = {"37559206978", "37560026164"}
    pack = squad.run_error_squads(
        enabled=frozenset({squad.SQUAD_HISTORY}),
        records=records,
        project_root=ROOT,
        junit_paths=tuple(_aoi_paths()),
        corpus_dirs=(corpus,),
        offline=True,
        web_endpoints=(),
        squad_timeout=5.0,
        squad_budget=10.0,
        squad_workers=1,
    )
    hist = pack["squads"][0]
    run_hits = {
        rid
        for finding in hist.get("findings") or []
        for rid in finding.get("corpus_run_ids") or []
        if rid in target_runs
    }
    assert run_hits


@pytest.mark.sentinel
def test_code_squad_skips_repo_walk_without_resolved_tests(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text("import sys\nsys.path.insert(0, 'x')\n", encoding="utf-8")
    pack = squad.run_code_squad(
        squad.SquadContext(
            records=[],
            project_root=tmp_path,
            project_root_explicit=False,
            junit_paths=(),
            corpus_dirs=(),
            offline=True,
            web_endpoints=(),
            timeout=5.0,
        )
    )
    assert not any(f.get("kind") == "sys.path.insert" for f in pack["findings"])


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
    code_none = subprocess.run(
        [*common[:6], "--squads", "none", *common[6:]], cwd=ROOT, capture_output=True, text=True
    ).returncode
    code_squads = subprocess.run(
        [*common[:6], "--squads", "code,history", "--offline", *common[6:]],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).returncode
    assert code_none == code_squads == 0


_STOP_PATH_IMPORT_PROBE = """
import sys
from pathlib import Path

root = Path({root})
sys.path.insert(0, str(root))
from sentinel import run_sentinel

before = set(sys.modules)
code = run_sentinel(["--", "true"])
assert code == 0
after = set(sys.modules)
new = after - before
bad = sorted(
    name
    for name in new
    if name == "squad"
    or name.startswith("urllib")
    or name == "http.client"
)
if bad:
    raise SystemExit("stop path imported: " + ", ".join(bad))
"""


def _median_subprocess_seconds(cmd: list[str], *, rounds: int = 8) -> float:
    subprocess.run(cmd, cwd=ROOT, capture_output=True, check=False)
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        subprocess.run(cmd, cwd=ROOT, capture_output=True, check=False)
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


@pytest.mark.sentinel
def test_sentinel_stop_path_does_not_import_squad_or_urllib() -> None:
    probe = _STOP_PATH_IMPORT_PROBE.format(root=repr(str(ROOT)))
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


@pytest.mark.sentinel
def test_sentinel_stop_path_timing_sane_vs_baseline() -> None:
    baseline_cmd = [sys.executable, "-c", "pass"]
    sentinel_cmd = [*SENTINEL_CLI, "--", "true"]
    baseline = _median_subprocess_seconds(baseline_cmd)
    sentinel = _median_subprocess_seconds(sentinel_cmd)
    assert sentinel < 0.5, f"sentinel -- true median {sentinel:.3f}s exceeds 500ms"
    assert sentinel < baseline * 6 + 0.08, (
        f"sentinel median {sentinel:.3f}s vs baseline {baseline:.3f}s"
    )


@pytest.mark.sentinel
def test_squad_budget_with_single_worker() -> None:
    def slow_code(_ctx: squad.SquadContext) -> dict:
        time.sleep(0.35)
        return {
            "name": squad.SQUAD_CODE,
            "status": "ok",
            "elapsed_ms": 350,
            "findings": [],
            "hypotheses": [],
        }

    def slow_history(_ctx: squad.SquadContext) -> dict:
        time.sleep(0.35)
        return {
            "name": squad.SQUAD_HISTORY,
            "status": "ok",
            "elapsed_ms": 350,
            "findings": [],
            "hypotheses": [],
        }

    with mock.patch.dict(
        squad._SQUAD_RUNNERS,
        {squad.SQUAD_CODE: slow_code, squad.SQUAD_HISTORY: slow_history},
    ):
        started = time.monotonic()
        pack = squad.run_error_squads(
            enabled=frozenset({squad.SQUAD_CODE, squad.SQUAD_HISTORY}),
            records=[],
            project_root=ROOT,
            junit_paths=[],
            corpus_dirs=[],
            offline=True,
            web_endpoints=[],
            squad_timeout=2.0,
            squad_budget=0.35,
            squad_workers=1,
        )
        elapsed = time.monotonic() - started
    assert elapsed < 0.55, f"squads exceeded budget wall time ({elapsed:.3f}s)"
    statuses = {item["status"] for item in pack["squads"]}
    assert statuses & {"timeout", "skipped"}


@pytest.mark.sentinel
def test_escalation_report_includes_squad_fields(contamination_root: Path) -> None:
    state = WatchState(command_argv=["pytest", "-q"], cwd=str(ROOT))
    outcome = RunOutcome(exit_code=1, effective_exit_code=1, junit_failure=True)
    doc = build_escalation_report(
        state,
        outcome,
        project_root=contamination_root,
        project_root_explicit=True,
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
    assert top and top.get("kind") == "import_cascade"
    assert top.get("source_squads")


@pytest.mark.sentinel
def test_dedupe_multisource_triage_helper() -> None:
    structured = {
        "found": [
            "junit failures (a.xml): 19",
            "junit failures (b.xml): 19",
        ],
        "not_found": [],
        "hints": [],
    }
    out = dedupe_multisource_triage(structured, junit_artifact_count=5)
    assert len(out["found"]) == 1
    assert "5 variants" in out["found"][0]
