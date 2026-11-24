"""scripts/harvest_corpus.py and .github/workflows/harvest-corpus.yml: fail-closed collection and offline preparation steps.

- harvest_junit_corpus: Harvest script must fail closed on gh/download errors without touching manifest.
- harvest_preparation: Exercise the workflow's preparation and validation commands without network access.
"""

from __future__ import annotations

import hashlib, importlib.util, json, os, shlex, shutil, subprocess, sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

from errlib.junit_redact import find_corpus_leaks
from scripts import harvest_corpus as harvest


# ──── harvest_junit_corpus ────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
HARVEST = ROOT / "scripts" / "harvest_corpus.py"


def _load_harvest_module() -> object:
    name = "harvest_corpus_under_test"
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
        code = mod.main(["harvest"])
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
        assert mod.main(["harvest"]) == 1
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
        assert mod.main(["harvest"]) == 0
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

    def fail_download(repo, run_id, name, dest, failures, *, artifact_id):  # noqa: ANN001
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

    def download(repo, run_id, name, dest, failures, *, artifact_id):
        if scenario != "empty_artifact":
            (dest / "result.xml").write_text("<broken" if scenario == "invalid_xml" else
                '<testsuite><testcase name="bad"><failure>boom</failure></testcase></testsuite>')
        return True

    monkeypatch.setattr(mod, "list_failed_runs", runs)
    monkeypatch.setattr(mod, "list_artifacts", artifacts)
    monkeypatch.setattr(mod, "download_artifact", download)
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    assert mod.main(["harvest", "--allow-partial", "--report", str(report), "--summary", str(summary)]) == expected_code
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
    def download(repo, run_id, name, dest, failures, *, artifact_id):
        failures.append(mod.HarvestFailure(repo, run_id, name, "download_artifact", "network error"))
        return False
    monkeypatch.setattr(mod, "list_failed_runs", runs)
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: [{"name": "junit", "id": 1, "expired": False}])
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
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: [{"name": "junit", "id": 1, "expired": True}])
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
        {"name": "junit-old", "id": 1, "expired": True},
        {"name": "junit-new", "id": 2, "expired": False},
    ])
    def download(repo, run_id, name, dest, failures, *, artifact_id):
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
        assert mod.download_artifact("r", 1, "junit", tmp_path / "d", failures, artifact_id=7) is False
        assert failures[-1].as_dict()["artifact_id"] == 7
    assert [f.stage for f in failures] == ["list_runs", "download_artifact"]
    assert all("timed out after" in f.detail for f in failures)
    assert [k["timeout"] for k in seen] == [mod.GH_TIMEOUT_SECONDS, mod.ARTIFACT_TIMEOUT_SECONDS]
    assert all(k["env"]["GH_PROMPT_DISABLED"] == "1" and k["stdin"] is subprocess.DEVNULL for k in seen)


# ──── harvest_preparation ─────────────────────────────────────────

WORKFLOW = (ROOT / ".github/workflows/harvest-corpus.yml").read_text()


def _step_script(name: str) -> str:
    step = WORKFLOW.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]
    run = step.split("        run: ", 1)[1]
    if run.startswith("|\n"):
        lines = []
        for line in run[2:].splitlines():
            if not line.startswith("          "):
                break
            lines.append(line[10:])
        run = "\n".join(lines)
    root = shlex.quote(str(ROOT))
    return run.replace("uv run --locked python", shlex.quote(sys.executable)).replace(
        "uv run pytest", f"uv run --project {root} --offline --no-sync pytest")


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    for name in ("triage.py", "errlib/__init__.py", "errlib/junit_redact.py", "errlib/error_input.py",
                 "errlib/project_version.py", "pyproject.toml",
                 "scripts/harvest_corpus.py", "scripts/harvest_sources.json", "scripts/capture_goldens.py",
                 "tests/support.py", "tests/test_corpus.py"):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    # The job metadata API is irrelevant to offline corpus preparation.
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text('#!/bin/sh\nprintf \'{"jobs": []}\\n\'\n')
    gh.chmod(0o755)
    corpus = tmp_path / "tests/fixtures/junit_corpus"
    (corpus / "cases").mkdir(parents=True)
    (corpus / "golden").mkdir()
    (tmp_path / "tests/fixtures/failure_suite/empty_root").mkdir(parents=True)
    return tmp_path


def _run_step(sandbox: Path, name: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-e", "-c", _step_script(name)], cwd=sandbox,
                          env={**os.environ, "PATH": str(sandbox / "bin") + os.pathsep + os.environ["PATH"]},
                          text=True, capture_output=True, timeout=60)


def test_prepare_redacts_existing_xml_refreshes_goldens_and_hashes(sandbox: Path) -> None:
    corpus = sandbox / "tests/fixtures/junit_corpus"
    entries = []
    for run in (38908321131, 38908363336):
        raw = '<testsuite><testcase name="bad"><failure>AssertionError: paths = [PosixPath(\'/home/runner/work/markdown...\')]</failure></testcase></testsuite>'
        source = sandbox / f"{run}.xml"
        source.write_text(raw)
        # The collector must still keep failing cases, omit passing cases, and use shared redaction.
        tree = ET.fromstring(raw)
        ET.SubElement(tree, "testcase", {"name": "passing"})
        assert len(harvest.extract_failing_xml(ET.ElementTree(tree)).getroot().findall(".//testcase")) == 1
        entry, write = harvest._harvest_file("markdown", {"databaseId": run}, "junit-py3.9", source, "pytest-3.9.xml", [])
        assert find_corpus_leaks(write.content) == []
        entries.append(entry)
        # Simulate a corpus written by the old collector and an already-existing leaky golden.
        (corpus / entry["xml"]).write_text(raw)
        (corpus / entry["golden"]).write_text("stale /home/runner/work/markdown...\n")
        entry["sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    (corpus / "manifest.json").write_text(json.dumps(entries))
    outside = sandbox / "tests/fixtures/unrelated.xml"
    outside.write_text('<testsuite name="untouched"/>')
    redacted = _run_step(sandbox, "Redact corpus (Silver)")
    assert redacted.returncode == 0, redacted.stdout + redacted.stderr
    prepared = _run_step(sandbox, "Prepare corpus goldens")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    for entry in json.loads((corpus / "manifest.json").read_text()):
        xml = corpus / entry["xml"]
        assert entry["sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()
        for path in (xml, corpus / entry["golden"]):
            assert find_corpus_leaks(path.read_text()) == []
        assert "FOUND: junit failures" in (corpus / entry["golden"]).read_text()
    assert outside.read_text() == '<testsuite name="untouched"/>'
    checked = _run_step(sandbox, "Validate corpus")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    # A later leak in a generated golden must block reaching PR creation.
    (corpus / entries[0]["golden"]).write_text("/home/runner/work/markdown...\n")
    checked = _run_step(sandbox, "Validate corpus")
    assert checked.returncode != 0
    assert "runner_work_path" in checked.stdout
    assert WORKFLOW.index("- name: Validate corpus") < WORKFLOW.index("- name: Open or refresh the corpus PR")
    pr_step = WORKFLOW.split("- name: Open or refresh the corpus PR", 1)[1]
    assert "if: success() && steps.validate.outcome == 'success'" in pr_step
    assert "if: always()" not in pr_step
    assert "continue-on-error" not in WORKFLOW


@pytest.mark.parametrize("raw", ["<broken", '<testsuite hostname="private"/>'])
def test_prepare_fails_closed_on_invalid_xml_or_remaining_leak(sandbox: Path, raw: str) -> None:
    corpus = sandbox / "tests/fixtures/junit_corpus"
    (corpus / "manifest.json").write_text("[]")
    (corpus / "cases/bad.xml").write_text(raw)
    if "hostname" in raw:
        # Simulate a redactor regression: the independent scanner must still reject it.
        module = sandbox / "errlib" / "junit_redact.py"
        text = module.read_text().replace("    redact_xml_tree(root)", "    # redaction disabled")
        module.write_text(text)
    result = _run_step(sandbox, "Redact corpus (Silver)")
    assert result.returncode != 0
    assert "Patched" not in result.stdout


@pytest.mark.parametrize(("artifact", "expected"), [
    ("junit-setup-py3.x", "pytest (Python 3.x)"),
    ("junit-setup-py3.11", "pytest (Python 3.11)"),
    ("junit-setup-vendor", "Vendor locked baseline"),
    ("junit-setup-test", "test"),
    ("junit-setup-clipboard-webkit", "studio-clipboard (webkit)"),
    ("junit-setup-unknown", None),
    ("junit-py3.11", "pytest (Python 3.11)"),
    ("setup-identity", "setup-identity / collect"),
    ("failure-identity", "failure-identity / collect"),
])
def test_harvest_resolves_setup_artifact_to_source_job(artifact, expected):
    jobs = [{"name": name} for name in (
        "pytest (Python 3.x)", "pytest (Python 3.11)", "Vendor locked baseline",
        "test", "test other", "studio-clipboard (webkit)", "studio-clipboard (firefox)",
        "setup-identity / collect", "failure-identity / collect",
    )]
    assert harvest.resolve_job(artifact, jobs) == expected


@pytest.mark.parametrize("mode", ["new", "hashed-legacy", "single-newer", "unhashed-legacy", "same-content"])
def test_harvest_downloads_listed_ids_without_reresolving_names(tmp_path, monkeypatch, mode):
    import io
    import zipfile

    mod = _load_harvest_module()
    repo = "Ironmate"
    run_id = 12345
    name = "junit-" + "same-name-" * 9
    source_path = "nested/results.xml"
    old_id, new_id = 14524043122, 14527477725
    old_case = '<testsuite><testcase name="same"><failure>old failure</failure></testcase></testsuite>'
    new_case = '<testsuite><testcase name="same"><failure>new failure</failure></testcase></testsuite>'
    archives = {old_id: old_case, new_id: old_case if mode == "same-content" else new_case}
    artifact_ids = [new_id] if mode in {"single-newer", "unhashed-legacy"} else [old_id, new_id]
    listed = [{"name": name, "id": aid, "expired": False} for aid in artifact_ids]
    listing_calls = []
    download_ids = []

    def list_gh(args, *rest, **kwargs):
        assert args == ["api", f"repos/{mod.ORG}/{repo}/actions/runs/{run_id}/artifacts",
                        "--paginate", "--jq", ".artifacts[] | {name,id,expired}"]
        listing_calls.append(args)
        return "\n".join(json.dumps(item) for item in listed)

    def download_gh(argv, **kwargs):
        assert len(argv) == 3 and argv[:2] == ["gh", "api"]
        aid = int(argv[2].split("/")[-2])
        assert argv[2] == f"repos/{mod.ORG}/{repo}/actions/artifacts/{aid}/zip"
        download_ids.append(aid)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr(source_path, archives[aid])
        return subprocess.CompletedProcess(argv, 0, out.getvalue(), b"")

    previous = []
    if mode in {"hashed-legacy", "single-newer", "unhashed-legacy"}:
        raw = tmp_path / "raw.xml"
        raw.write_text(old_case)
        legacy, pending = mod._harvest_file(repo, {"databaseId": run_id}, name, raw, source_path, [])
        if mode == "unhashed-legacy":
            legacy.pop("sha256")
            monkeypatch.setattr(mod, "CORPUS", tmp_path / "nonexistent-corpus")
        previous = [legacy]
        old_id_path = (legacy["id"], legacy["xml"], legacy["golden"])
    monkeypatch.setattr(mod, "REPOS", [repo])
    monkeypatch.setattr(mod, "list_failed_runs", lambda *args: [{"databaseId": run_id}])
    monkeypatch.setattr(mod, "run_gh", list_gh)
    monkeypatch.setattr(mod.subprocess, "run", download_gh)
    entries, failures, pending = mod.harvest(existing=previous)
    assert not failures
    assert len(listing_calls) == 1
    assert download_ids == artifact_ids
    expected = {"new": [old_id, new_id], "hashed-legacy": [new_id], "single-newer": [new_id],
                "unhashed-legacy": [], "same-content": [old_id]}[mode]
    assert [e["artifact_id"] for e in entries] == expected
    assert all(e["artifact"] == name and e["source_file"] == source_path for e in entries)
    assert len({e["id"] for e in entries}) == len({p.rel_path for p in pending}) == len(expected)
    assert all(str(e["artifact_id"]) in e["id"] and str(e["artifact_id"]) in e["xml"] for e in entries)
    if previous:
        assert "artifact_id" not in previous[0]
        assert tuple(previous[0][key] for key in ("id", "xml", "golden")) == old_id_path
        assert all(e["xml"] != previous[0]["xml"] for e in entries)


@pytest.mark.parametrize("value", [True, 0, -1, "7", None])
def test_invalid_listed_artifact_ids_fail_without_download(value, tmp_path, monkeypatch):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "run_gh", lambda *a, **k: json.dumps({"name": "junit", "id": value, "expired": False}))
    failures = []
    assert mod.list_artifacts("r", 1, failures) == []
    assert failures[-1].stage == "list_artifacts"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("invalid ID reached download"))
    assert not mod.download_artifact("r", 1, "junit", tmp_path, failures, artifact_id=value)
    assert failures[-1].detail == "invalid listed artifact id"


@pytest.mark.parametrize("scenario,expected_status,expected_stages", [
    ("valid", "complete", []),
    ("receipt_only", "unavailable", ["missing_result"]),
    ("incomplete", "partial", ["incomplete_evidence"]),
    ("malformed", "partial", ["invalid_evidence"]),
    ("expired", "partial", ["artifact_expired"]),
])
def test_shared_identity_receipt_is_not_raw_result(monkeypatch, scenario, expected_status, expected_stages):
    mod = _load_harvest_module()
    monkeypatch.setattr(mod, "REPOS", ["repo"])
    monkeypatch.setattr(mod, "list_failed_runs", lambda *args: [{"databaseId": 1}])
    artifacts = [{"name": "python-failure-identity", "id": 1, "expired": scenario == "expired"}]
    if scenario != "receipt_only":
        artifacts.append({"name": "junit-py3.15", "id": 2, "expired": False})
    monkeypatch.setattr(mod, "list_artifacts", lambda *args: artifacts)

    def download(repo, run_id, name, dest, failures, *, artifact_id):
        if artifact_id == 1:
            summary = {"complete": scenario != "incomplete", "missing_reports": [],
                       "unexpected_reports": [], "reports": [
                           {"report_id": "junit-1", "measured": True, "truncated": False, "failures": 0}]}
            (dest / "collection.json").write_text("{" if scenario == "malformed" else json.dumps(summary), encoding="utf-8")
            (dest / "failure-identity.jsonl").write_text("", encoding="utf-8")
        else:
            (dest / "junit.xml").write_text('<testsuite><testcase name="passing"/></testsuite>', encoding="utf-8")
        return True

    monkeypatch.setattr(mod, "download_artifact", download)
    coverage = {}
    entries, failures, pending = mod.harvest(coverage=coverage)
    assert not entries and not pending
    assert coverage["repo"]["status"] == expected_status
    assert [f.stage for f in failures] == expected_stages
    assert coverage["repo"]["checked_files"] == (0 if scenario == "receipt_only" else 1)


def test_identity_receipt_requires_both_files(tmp_path):
    (tmp_path / "collection.json").write_text(json.dumps({
        "complete": True, "missing_reports": [], "unexpected_reports": [],
        "reports": [{"report_id": "junit-1", "measured": True, "truncated": False, "failures": 0}]
    }), encoding="utf-8")
    assert harvest.inspect_identity_evidence(tmp_path) == "invalid_evidence"
