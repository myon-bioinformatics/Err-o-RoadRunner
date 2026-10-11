"""Harvest script must fail closed on gh/download errors."""

from __future__ import annotations

import importlib.util
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


@pytest.mark.gap
def test_harvest_main_exits_nonzero_when_failures_recorded(tmp_path: Path) -> None:
    mod = _load_harvest_module()
    mod.CORPUS = tmp_path
    mod.MANIFEST = tmp_path / "manifest.json"
    failure = mod.HarvestFailure(
        repo="gh_identity",
        run_id=None,
        artifact=None,
        stage="list_failed_runs",
        detail="gh: authentication required",
    )
    with mock.patch.object(mod, "harvest", return_value=([], [failure])):
        code = mod.main()
    assert code == 1


@pytest.mark.gap
def test_harvest_records_expired_artifact_as_failure() -> None:
    mod = _load_harvest_module()

    def fake_list_runs(repo: str, failures: list) -> list[dict]:
        return [{"databaseId": 1, "createdAt": "2026-01-01T00:00:00Z"}]

    def fake_list_art(repo: str, run_id: int, failures: list) -> list[dict]:
        return [{"name": "junit-py3.12", "id": 9, "expired": True}]

    with (
        mock.patch.object(mod, "list_failed_runs", fake_list_runs),
        mock.patch.object(mod, "list_artifacts", fake_list_art),
        mock.patch.object(mod, "download_artifact", lambda *a, **k: False),
        mock.patch.object(mod, "REPOS", ["gh_identity"]),
    ):
        entries, failures = mod.harvest(entries_limit_per_repo=1)

    assert entries == []
    assert any(f.stage == "artifact_expired" for f in failures)
