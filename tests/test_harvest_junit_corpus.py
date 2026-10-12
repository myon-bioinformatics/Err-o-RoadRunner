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
    with mock.patch.object(mod, "harvest", return_value=(entries, [], pending)):
        assert mod.main() == 0
    assert (tmp_path / "cases" / "new.xml").read_text(encoding="utf-8") == "<testsuites/>"
    assert json.loads(manifest.read_text(encoding="utf-8")) == entries


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
