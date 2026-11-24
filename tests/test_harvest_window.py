"""scripts/harvest_corpus.py: failed runs older than the raw-result retention window.

- harvest_window: a failed run whose raw results expired (only receipts remain) is outside
  the harvest scope, is counted per repository, and never turns a repository unavailable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import harvest_corpus as harvest

# Far from the wall clock, so an ignored ``now`` cannot pass by accident.
NOW = datetime(2020, 3, 1, 0, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=harvest.RUN_WINDOW_DAYS)


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("run,older", [
    ({"updatedAt": _stamp(CUTOFF - timedelta(seconds=1))}, True),
    ({"updatedAt": _stamp(CUTOFF)}, False),
    ({"createdAt": _stamp(CUTOFF - timedelta(days=3))}, True),
    # A rerun refreshes updatedAt (and its artifacts) although createdAt stays old.
    ({"createdAt": _stamp(CUTOFF - timedelta(days=3)), "updatedAt": _stamp(NOW)}, False),
    ({"updatedAt": "2020-02-01T00:00:00+00:00"}, True),
    ({"updatedAt": "2020-02-15T23:59:59Z"}, True),
    ({}, False),
    ({"updatedAt": 1}, False),
    ({"updatedAt": "not a time"}, False),
    ({"updatedAt": "2020-01-01T00:00:00"}, False),
])
def test_unknown_age_stays_in_scope(run, older):
    assert harvest._older_than(run, CUTOFF) is older


def test_window_matches_the_raw_result_retention():
    """The window is the ``retention-days`` of raw JUnit uploads (this repository's included)."""
    ci = (Path(harvest.__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    days = {int(line.split(":")[1]) for line in ci.splitlines() if line.strip().startswith("retention-days:")}
    assert days == {harvest.RUN_WINDOW_DAYS} == {14}


def test_window_applies_before_the_limit_and_is_counted(monkeypatch):
    old = _stamp(CUTOFF - timedelta(days=1))
    new = _stamp(NOW - timedelta(days=1))
    raw = ([{"databaseId": 1, "workflowName": "Copilot", "updatedAt": old}]
           + [{"databaseId": 10 + i, "workflowName": "CI", "updatedAt": new} for i in range(3)]
           + [{"databaseId": 20 + i, "workflowName": "CI", "updatedAt": old} for i in range(4)]
           + [{"databaseId": 30, "workflowName": "CI"}])
    monkeypatch.setattr(harvest, "_list_failed_runs_raw", lambda repo, failures, limit: raw)
    monkeypatch.setattr(harvest, "OUTSIDE_WINDOW", {})
    runs = harvest.list_failed_runs("repo", [], limit=3, now=NOW)
    assert [r["databaseId"] for r in runs] == [10, 11, 12]
    assert harvest.OUTSIDE_WINDOW == {"repo": 4}
    assert [r["databaseId"] for r in harvest.list_failed_runs("repo", [], now=NOW)] == [10, 11, 12, 30]


def test_dependabot_update_runs_are_not_test_runs(monkeypatch):
    """GitHub's dynamic Dependabot workflow never uploads test results, like Copilot review runs."""
    recent = _stamp(NOW - timedelta(days=1))
    raw = [{"databaseId": 1, "workflowName": "Dependabot Updates", "updatedAt": recent},
           {"databaseId": 2, "workflowName": "CI", "updatedAt": recent}]
    monkeypatch.setattr(harvest, "_list_failed_runs_raw", lambda repo, failures, limit: raw)
    monkeypatch.setattr(harvest, "OUTSIDE_WINDOW", {})
    assert [r["databaseId"] for r in harvest.list_failed_runs("repo", [], now=NOW)] == [2]
    assert harvest.OUTSIDE_WINDOW == {"repo": 0}


def test_failed_run_query_requests_update_time(monkeypatch):
    seen = []

    def fake_run_gh(args, failures, **kwargs):
        seen.append(args)
        return "[]"

    monkeypatch.setattr(harvest, "run_gh", fake_run_gh)
    assert harvest._list_failed_runs_raw("repo", [], limit=3) == []
    fields = seen[0][seen[0].index("--json") + 1].split(",")
    assert {"createdAt", "updatedAt", "databaseId", "workflowName"} <= set(fields)


def test_receipt_only_history_is_complete_and_reported(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    by_repo = {
        "aged": [{"databaseId": 1, "workflowName": "CI", "updatedAt": _stamp(now - timedelta(days=harvest.RUN_WINDOW_DAYS + 1))}],
        "fresh": [{"databaseId": 2, "workflowName": "CI", "updatedAt": _stamp(now - timedelta(days=1))}],
    }
    monkeypatch.setattr(harvest, "REPOS", list(by_repo))
    for attr, sub in (("CORPUS", "junit"), ("RESULTS_CORPUS", "results")):
        monkeypatch.setattr(harvest, attr, tmp_path / sub)
    monkeypatch.setattr(harvest, "MANIFEST", tmp_path / "junit" / "manifest.json")
    monkeypatch.setattr(harvest, "RESULTS_MANIFEST", tmp_path / "results" / "manifest.json")
    monkeypatch.setattr(harvest, "_list_failed_runs_raw", lambda repo, failures, limit: by_repo[repo])

    def artifacts(repo, run_id, failures):
        assert run_id == 2, "a run outside the window must not be inspected"
        return [{"name": "junit-py3.15", "id": 7, "expired": False}]

    def download(repo, run_id, name, dest, failures, *, artifact_id):
        (dest / "junit.xml").write_text('<testsuite><testcase name="ok"/></testsuite>', encoding="utf-8")
        return True

    monkeypatch.setattr(harvest, "list_artifacts", artifacts)
    monkeypatch.setattr(harvest, "download_artifact", download)
    harvest.OUTSIDE_WINDOW["stale"] = 9  # a previous call must not leak into this report
    report, summary = tmp_path / "report.json", tmp_path / "summary.md"
    assert harvest.main(["harvest", "--allow-partial", "--report", str(report), "--summary", str(summary)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "complete" and data["run_window_days"] == harvest.RUN_WINDOW_DAYS
    aged, fresh = data["repositories"]["aged"], data["repositories"]["fresh"]
    assert (aged["status"], aged["failed_runs"], aged["no_failed_runs"], aged["older_failed_runs"]) == ("complete", 0, True, 1)
    assert (fresh["status"], fresh["checked_files"], fresh["older_failed_runs"]) == ("complete", 1, 0)
    text = summary.read_text(encoding="utf-8")
    assert text == harvest.report_markdown(data)
    assert f"more than {harvest.RUN_WINDOW_DAYS} days ago" in text
    assert "- aged: 1" in text and "- fresh:" not in text.split("### Collection")[0]
    assert "stale" not in harvest.OUTSIDE_WINDOW


def test_summary_without_aged_runs_has_no_window_section():
    report = {"status": "complete", "new_entries": 0, "failures": [], "by_repo": {},
              "repositories": {"r": {"status": "complete", "checked_files": 0}}}
    assert "raw result retention" not in harvest.report_markdown(report)


def test_newly_public_repositories_are_sources():
    sources = json.loads((Path(harvest.__file__).parent / "harvest_sources.json").read_text(encoding="utf-8"))
    repos = sources["repos"]
    assert repos == sorted(repos, key=str.lower) and len(repos) == len(set(repos))
    for name in ("FluidFullSpeedSimulation", "OneLaunchNumerousModules", "ONTO", "OnTautoLogy", "TaxOntOy", "TopOntoLogy"):
        assert name in harvest.REPOS
