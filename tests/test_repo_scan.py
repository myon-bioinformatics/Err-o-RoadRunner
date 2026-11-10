import contextlib
import io
from unittest.mock import patch

import repo_scan


def test_issue_detection_ignores_pull_requests():
    items = [
        {"number": 1, "title": "Bug: failure", "html_url": "https://example/1", "labels": []},
        {"number": 2, "title": "bug", "html_url": "https://example/2", "labels": [], "pull_request": {}},
        {"number": 3, "title": "Feature", "html_url": "https://example/3", "labels": []},
    ]
    with patch.object(repo_scan, "load", return_value=items):
        result = repo_scan.issues("example/repo", 30)
    assert len(result) == 1
    assert "#1" in result[0]


def test_run_metadata_and_historical_note():
    item = {"id": 42, "name": "CI", "head_branch": "main", "head_sha": "a" * 40,
            "updated_at": "2026-11-10", "html_url": "https://example/42"}
    def fake_load(path):
        return {"workflow_runs": [item]} if "/actions/" in path else []
    output = io.StringIO()
    with patch.object(repo_scan, "load", side_effect=fake_load), contextlib.redirect_stdout(output):
        assert repo_scan.main(["example/repo"]) == 0
    assert "branch=main" in output.getvalue()
    assert "historical" in output.getvalue()


def test_partial_failure_returns_error_without_hiding_other_results():
    def fake_load(path):
        if "/actions/" in path:
            raise RuntimeError("access denied")
        return []
    output = io.StringIO()
    with patch.object(repo_scan, "load", side_effect=fake_load), contextlib.redirect_stdout(output):
        assert repo_scan.main(["example/repo"]) == 2
    assert "ERROR: workflow runs" in output.getvalue()
    assert "DONE: issues" in output.getvalue()
