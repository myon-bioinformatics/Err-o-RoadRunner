"""Harvest script must fail closed on gh/download errors without touching manifest."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARVEST = ROOT / "scripts" / "harvest_junit_corpus.py"


def _load_harvest_module() -> object:
    name = "harvest_junit_corpus_under_test"
    spec = importlib.util.spec_from_file_location(name, HARVEST)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed_manifest(path: Path) -> None:
    path.write_text(json.dumps([{"id": "keep-me", "xml": "cases/keep.xml"}]) + "\n", encoding="utf-8")


@pytest.mark.gap
def test_harvest_main_exits_nonzero_and_leaves_manifest_unchanged(tmp_path: Path) -> None:
    mod = _load_harvest_module()
    manifest = tmp_path / "manifest.json"
    _seed_manifest(manifest)
    mod.CORPUS = tmp_path
    mod.MANIFEST = manifest
    failure = mod.HarvestFailure(
        repo="gh_identity",
        run_id=None,
        artifact=None,
        stage="list_failed_runs",
        detail="gh: authentication required",
    )
    with mock.patch.object(mod, "harvest", return_value=([], [failure], [])):
        code = mod.main()
    assert code == 1
    assert json.loads(manifest.read_text(encoding="utf-8")) == [{"id": "keep-me", "xml": "cases/keep.xml"}]


@pytest.mark.gap
def test_harvest_main_does_not_write_manifest_on_failure_before_success(tmp_path: Path) -> None:
    mod = _load_harvest_module()
    manifest = tmp_path / "manifest.json"
    _seed_manifest(manifest)
    mod.CORPUS = tmp_path
    mod.MANIFEST = manifest
    pending = [mod.PendingCorpusWrite(rel_path=Path("cases/new.xml"), content="<x/>")]
    failure = mod.HarvestFailure(
        repo="gh_identity",
        run_id=1,
        artifact="junit-py3.12",
        stage="download_artifact",
        detail="download failed",
    )
    with mock.patch.object(mod, "harvest", return_value=([{"id": "new"}], [failure], pending)):
        assert mod.main() == 1
    assert not (tmp_path / "cases" / "new.xml").exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))[0]["id"] == "keep-me"


@pytest.mark.gap
def test_harvest_main_writes_on_full_success(tmp_path: Path) -> None:
    mod = _load_harvest_module()
    manifest = tmp_path / "manifest.json"
    _seed_manifest(manifest)
    mod.CORPUS = tmp_path
    mod.MANIFEST = manifest
    pending = [mod.PendingCorpusWrite(rel_path=Path("cases/new.xml"), content="<testsuites/>")]
    entries = [{"id": "new", "xml": "cases/new.xml"}]
    def successful_harvest(**kwargs):
        kwargs["coverage"]["gh_identity"] = {"status": "complete", "checked_files": 1}
        return entries, [], pending

    with mock.patch.object(mod, "harvest", side_effect=successful_harvest):
        assert mod.main() == 0
    assert (tmp_path / "cases" / "new.xml").read_text(encoding="utf-8") == "<testsuites/>"
    # Harvest is incremental: existing entries are kept and new ones appended.
    assert json.loads(manifest.read_text(encoding="utf-8")) == [{"id": "keep-me", "xml": "cases/keep.xml"}, *entries]


@pytest.mark.gap
def test_list_failed_runs_invalid_json_records_failure() -> None:
    mod = _load_harvest_module()
    failures: list[mod.HarvestFailure] = []

    def fake_gh(*_a, **_k):
        return "not-json"

    with mock.patch.object(mod, "run_gh", fake_gh):
        runs = mod.list_failed_runs("gh_identity", failures)
    assert runs == []
    assert any(f.stage == "list_failed_runs" and "invalid json" in f.detail for f in failures)


@pytest.mark.gap
def test_list_artifacts_invalid_json_records_failure() -> None:
    mod = _load_harvest_module()
    failures: list[mod.HarvestFailure] = []

    def fake_gh(*_a, **_k):
        return "{broken"

    with mock.patch.object(mod, "run_gh", fake_gh):
        arts = mod.list_artifacts("gh_identity", 1, failures)
    assert arts == []
    assert any(f.stage == "list_artifacts" and "invalid json" in f.detail for f in failures)


@pytest.mark.gap
def test_unrelated_expired_artifact_does_not_fail_harvest() -> None:
    mod = _load_harvest_module()

    def fake_list_runs(repo: str, failures: list) -> list[dict]:
        return [{"databaseId": 1, "createdAt": "2026-01-01T00:00:00Z"}]

    def fake_list_art(repo: str, run_id: int, failures: list) -> list[dict]:
        return [
            {"name": "coverage-html", "id": 1, "expired": True},
            {"name": "junit-py3.12", "id": 2, "expired": True},
        ]

    with (
        mock.patch.object(mod, "list_failed_runs", fake_list_runs),
        mock.patch.object(mod, "list_artifacts", fake_list_art),
        mock.patch.object(mod, "download_artifact", lambda *a, **k: False),
        mock.patch.object(mod, "REPOS", ["gh_identity"]),
    ):
        entries, failures, _pending = mod.harvest(entries_limit_per_repo=1)

    assert entries == []
    assert len(failures) == 1
    assert failures[0].stage == "artifact_expired"
    assert failures[0].artifact == "junit-py3.12"


@pytest.mark.gap
def test_download_failure_recorded() -> None:
    mod = _load_harvest_module()

    def fake_list_runs(repo: str, failures: list) -> list[dict]:
        return [{"databaseId": 1, "createdAt": "2026-01-01T00:00:00Z"}]

    def fake_list_art(repo: str, run_id: int, failures: list) -> list[dict]:
        return [{"name": "junit-py3.12", "id": 2, "expired": False}]

    def fail_download(repo, run_id, name, dest, failures):  # noqa: ANN001
        failures.append(
            mod.HarvestFailure(
                repo=repo,
                run_id=run_id,
                artifact=name,
                stage="download_artifact",
                detail="network error",
            )
        )
        return False

    with (
        mock.patch.object(mod, "list_failed_runs", fake_list_runs),
        mock.patch.object(mod, "list_artifacts", fake_list_art),
        mock.patch.object(mod, "download_artifact", fail_download),
        mock.patch.object(mod, "REPOS", ["gh_identity"]),
    ):
        _entries, failures, _pending = mod.harvest(entries_limit_per_repo=1)

    assert any(f.stage == "download_artifact" for f in failures)


@pytest.mark.gap
def test_all_public_repos_and_result_artifact_names_are_covered() -> None:
    mod = _load_harvest_module()
    assert len(mod.REPOS) == len(set(mod.REPOS)) >= 18
    assert "mcp-toolcall-lab" in mod.REPOS and "xprobe" in mod.REPOS
    for name in ("junit-py3.12", "python-failure-identity", "test-report-ubuntu-latest-py3.12", "locked-junit-py3.10", "galleria-junit", "playwright-report"):
        assert mod.is_junit_artifact(name), name
    assert not mod.is_junit_artifact("coverage-html")


@pytest.mark.gap
def test_copilot_runs_do_not_crowd_out_test_runs() -> None:
    mod = _load_harvest_module()
    raw = [{"databaseId": i, "workflowName": "Copilot"} for i in range(30)] + [{"databaseId": 99, "workflowName": "test"}]
    with mock.patch.object(mod, "_list_failed_runs_raw", lambda *a, **k: raw):
        runs = mod.list_failed_runs("mcp-toolcall-lab", [])
    assert [r["databaseId"] for r in runs] == [99]


@pytest.mark.gap
def test_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    import io
    import zipfile

    mod = _load_harvest_module()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pytest.xml", "<testsuite/>")
        zf.writestr("../evil.txt", "x")
        zf.writestr("/abs.txt", "x")
    written = mod.safe_extract(buf.getvalue(), tmp_path / "out")
    assert [p.name for p in written] == ["pytest.xml"]
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.gap
def test_flutter_machine_jsonl_keeps_only_failing_test() -> None:
    mod = _load_harvest_module()
    lines = [
        {"type": "testStart", "test": {"id": 1, "name": "ok"}},
        {"type": "testDone", "testID": 1, "result": "success", "skipped": False},
        {"type": "testStart", "test": {"id": 2, "name": "bad"}},
        {"type": "error", "testID": 2, "error": "Expected: 1 Actual: 2", "stackTrace": "/home/runner/work/x/y.dart 3:4"},
        {"type": "testDone", "testID": 2, "result": "failure", "skipped": False},
    ]
    kind, kept = mod.extract_failing_jsonl("\n".join(json.dumps(x) for x in lines))
    assert kind == "flutter-machine-jsonl"
    assert {r.get("testID", r.get("test", {}).get("id")) for r in kept} == {2}
    redacted = json.dumps(mod.redact_json(kept))
    assert "/home/runner/work/" not in redacted


@pytest.mark.gap
def test_pytest_events_jsonl_and_playwright_json() -> None:
    mod = _load_harvest_module()
    ev = "\n".join(json.dumps(x) for x in [
        {"nodeid": "t::a", "outcome": "passed"},
        {"nodeid": "t::b", "outcome": "failed", "longrepr": "AssertionError"},
    ])
    kind, kept = mod.extract_failing_jsonl(ev)
    assert kind == "pytest-events-jsonl" and [r["nodeid"] for r in kept] == ["t::b"]
    pw = {"config": {}, "suites": [{"title": "a.spec.ts", "specs": [
        {"title": "works", "file": "a.spec.ts", "tests": [{"status": "expected", "results": [{"status": "passed"}]}]},
        {"title": "breaks", "file": "a.spec.ts", "tests": [{"status": "unexpected", "projectName": "chromium", "results": [{"status": "failed", "errors": [{"message": "boom"}]}]}]},
    ]}]}
    kind, data = mod.extract_failing_json(pw)
    assert kind == "playwright-json"
    assert [f["title"][-1] for f in data["failures"]] == ["breaks"]
    assert mod.extract_failing_json({"config": {}, "suites": []}) is None


@pytest.mark.parametrize("scenario,expected_code,expected_status", [
    ("all_failed", 1, "unavailable"),
    ("partial", 0, "partial"),
    ("normal", 0, "complete"),
    ("no_runs", 0, "complete"),
    ("expired", 1, "unavailable"),
    ("no_targets", 1, "unavailable"),
    ("empty_artifact", 1, "unavailable"),
    ("invalid_xml", 1, "unavailable"),
])
def test_collection_coverage_end_to_end(tmp_path, monkeypatch, scenario, expected_code, expected_status):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "REPOS", ["good", "bad"])
    monkeypatch.setattr(mod, "CORPUS", tmp_path / "junit")
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "junit" / "manifest.json")
    monkeypatch.setattr(mod, "RESULTS_CORPUS", tmp_path / "results")
    monkeypatch.setattr(mod, "RESULTS_MANIFEST", tmp_path / "results" / "manifest.json")

    def runs(repo, failures):
        if scenario == "all_failed" or (scenario == "partial" and repo == "bad"):
            failures.append(mod.HarvestFailure(repo, None, None, "list_failed_runs", "authentication required"))
            return []
        if scenario == "no_runs":
            return []
        return [{"databaseId": 1, "createdAt": "2026-10-15"}]

    def artifacts(repo, run_id, failures):
        if scenario == "no_targets":
            return []
        return [{"name": "junit", "id": 1, "expired": scenario == "expired"}]

    def download(repo, run_id, name, dest, failures):
        if scenario != "empty_artifact":
            (dest / "result.xml").write_text("<broken" if scenario == "invalid_xml" else
                '<testsuite><testcase name="bad"><failure>boom</failure></testcase></testsuite>')
        return True

    monkeypatch.setattr(mod, "list_failed_runs", runs)
    monkeypatch.setattr(mod, "list_artifacts", artifacts)
    monkeypatch.setattr(mod, "download_artifact", download)
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    assert mod.main(["--allow-partial", "--report", str(report), "--summary", str(summary)]) == expected_code
    data = json.loads(report.read_text())
    assert data["status"] == expected_status
    assert data["exit_code"] == expected_code
    assert set(data["repositories"]) == {"good", "bad"}
    assert summary.read_text() == mod.report_markdown(data)
    if expected_code:
        assert not mod.MANIFEST.exists()
        assert data["failures"]
    if scenario == "partial":
        assert data["repositories"]["good"]["status"] == "complete"
        assert data["repositories"]["bad"]["status"] == "unavailable"
        assert "authentication required" in summary.read_text()
        assert "bad" in summary.read_text()
    if scenario == "no_runs":
        assert data["new_entries"] == 0
        assert data["repositories"]["good"]["no_failed_runs"]
        assert "No new corpus entries" in summary.read_text()
    if scenario == "expired":
        assert {f["stage"] for f in data["failures"]} == {"artifact_expired"}


def test_download_failure_in_one_repository_with_other_confirmed_empty(tmp_path, monkeypatch):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "REPOS", ["good", "bad"])
    def runs(repo, failures):
        return [] if repo == "good" else [{"databaseId": 1}]
    def download(repo, run_id, name, dest, failures):
        failures.append(mod.HarvestFailure(repo, run_id, name, "download_artifact", "network error"))
        return False
    monkeypatch.setattr(mod, "list_failed_runs", runs)
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: [{"name": "junit", "expired": False}])
    monkeypatch.setattr(mod, "download_artifact", download)
    coverage = {}
    entries, failures, _ = mod.harvest(coverage=coverage)
    assert not entries
    assert coverage["good"]["status"] == "complete"
    assert coverage["bad"]["status"] == "unavailable"
    assert failures[0].detail == "network error"


def test_workflow_publishes_coverage_summary():
    workflow = (ROOT / ".github/workflows/harvest-corpus.yml").read_text()
    assert '--summary "$RUNNER_TEMP/harvest-summary.md"' in workflow
    assert 'cat "$RUNNER_TEMP/harvest-summary.md" >> "$GITHUB_STEP_SUMMARY"' in workflow
    assert '--body-file "$RUNNER_TEMP/harvest-summary.md"' in workflow
    assert "if: always() && steps.harvest.outcome" in workflow
    assert 'continue-on-error' not in workflow


def test_duplicate_corpus_still_checks_artifact(monkeypatch):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "REPOS", ["good"])
    monkeypatch.setattr(mod, "list_failed_runs", lambda *args: [{"databaseId": 1}])
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: [{"name": "junit", "expired": True}])
    coverage = {}
    mod.harvest(existing=[{"repo": mod.ORG + "/good", "run_id": 1, "artifact": "junit"}], coverage=coverage)
    assert coverage["good"]["status"] == "unavailable"


@pytest.mark.parametrize("raw", ["", "null", "{}", '[{"databaseId": "oops"}]'])
def test_invalid_run_response_is_not_confirmed_empty(raw, monkeypatch):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "run_gh", lambda *args, **kwargs: raw)
    failures = []
    assert mod.list_failed_runs("repo", failures) == []
    assert failures and failures[0].stage == "list_failed_runs"


def test_same_repository_partial_and_expired_with_valid_target(monkeypatch):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "REPOS", ["repo"])
    monkeypatch.setattr(mod, "list_failed_runs", lambda *args: [{"databaseId": 1}])
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: [
        {"name": "junit-old", "expired": True},
        {"name": "junit-new", "expired": False},
    ])
    def download(repo, run_id, name, dest, failures):
        (dest / "passing.xml").write_text('<testsuite><testcase name="passing"/></testsuite>')
        return True
    monkeypatch.setattr(mod, "download_artifact", download)
    coverage = {}
    entries, failures, _ = mod.harvest(coverage=coverage)
    assert not entries
    assert coverage["repo"]["status"] == "partial"
    assert coverage["repo"]["checked_files"] == 1
    assert failures[0].stage == "artifact_expired"


def test_gh_calls_never_prompt_and_time_out_as_stage_failures(tmp_path):
    """fetch-guard: a stuck gh becomes a recorded failure for that stage; collection goes on."""
    import subprocess

    mod = _load_harvest_module()
    seen = []

    def hang(argv, **kwargs):
        seen.append(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    failures = []
    with mock.patch.object(mod.subprocess, "run", hang):
        assert mod.run_gh(["api", "x"], failures, repo="r", run_id=1, artifact=None, stage="list_runs") is None
        with mock.patch.object(mod, "_artifact_id", lambda *a: 7):
            assert mod.download_artifact("r", 1, "junit", tmp_path / "d", failures) is False
    assert [f.stage for f in failures] == ["list_runs", "download_artifact"]
    assert all("timed out after" in f.detail for f in failures)
    assert [k["timeout"] for k in seen] == [mod.GH_TIMEOUT_SECONDS, mod.ARTIFACT_TIMEOUT_SECONDS]
    assert all(k["env"]["GH_PROMPT_DISABLED"] == "1" and k["stdin"] is subprocess.DEVNULL for k in seen)
