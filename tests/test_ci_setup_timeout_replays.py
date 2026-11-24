"""Replay selected public setup failures using synthetic, local-only evidence."""

import json
import os
import sys
from xml.etree import ElementTree as ET

import pytest

from errlib.junit_redact import find_corpus_leaks
from scripts import harvest_corpus
from tests.support import FIXTURES, ROOT, triage_actions_log_cli
from vendor.cli_args import Command, run_cli, run_command

CASES = json.loads(
    (FIXTURES / "actions_logs/synthetic/apt-setup-timeouts.json").read_text(encoding="utf-8")
)["cases"]


@pytest.fixture(params=CASES, ids=lambda case: case["id"])
def observed(request):
    return request.param


def test_setup_timeout_is_primary_not_the_missing_artifact(tmp_path, observed):
    """Imitate named gh run-view lines; none of these are copied log bodies."""
    setup = observed["setup_step"]
    minutes = observed["budget_minutes"]
    statements = (
        (setup, f"Need to get {observed['apt_download']} of archives."),
        (setup, f"##[error]The action '{setup}' has timed out after {minutes} minutes."),
        (observed["report_step"], f"##[{observed['artifact_log_level']}]No files were found to upload."),
    )
    log = tmp_path / "synthetic-gh-run-view.log"
    log.write_text("".join(
        f"{observed['job']}\t{step}\t2000-01-01T00:00:0{i}.0000000Z {message}\n"
        for i, (step, message) in enumerate(statements)
    ), encoding="utf-8")
    report = triage_actions_log_cli(log)
    assert f"FOUND: actions log failing step (actions-log): {observed['job']} / {setup}" in report
    assert "FOUND: actions log cancel or timeout signal (actions-log): yes" in report
    assert "NOT FOUND: actions log process exit code (actions-log)" in report
    assert f"after {minutes} minutes" in report
    assert f"actions log failing step (actions-log): {observed['job']} / {observed['report_step']}" not in report


def test_fake_setup_failure_survives_junit_harvest_and_digest(tmp_path, observed):
    """Exit in a disposable local process; do not run the upstream workflow."""
    process = run_command(Command((sys.executable, "-S", "-c", "raise SystemExit(17)")), cwd=tmp_path)
    assert process.returncode == 17
    step = observed["setup_step"]
    outcome = "failure" if process.returncode else "success"
    raw_setup = tmp_path / "setup.xml"
    emitted = run_cli(
        ROOT / "scripts/ci_setup_junit.py", observed["id"], str(raw_setup),
        python_options=("-S",),
        env={**os.environ, "SETUP_OUTCOMES": f"checkout=success\n{step}={outcome}\nproduct tests=skipped\n"},
    )
    assert emitted.returncode == 0, emitted.stderr
    assert not (tmp_path / "junit.xml").exists(), "must not pretend product tests ran"
    failures = []
    entry, pending = harvest_corpus._harvest_file(
        observed["repo"].split("/", 1)[1],
        {"databaseId": observed["run_id"], "workflowName": observed["workflow"]},
        "junit-setup-synthetic", raw_setup, "setup.xml", failures,
    )
    assert not failures
    assert (entry["repo"], entry["run_id"], entry["format"]) == (
        observed["repo"], observed["run_id"], "junit-xml",
    )
    trimmed = ET.fromstring(pending.content)
    cases = trimmed.findall(".//testcase")
    assert [case.get("name") for case in cases] == [step]
    assert cases[0].find("error").get("type") == "SetupError"
    redacted = tmp_path / "redacted.xml"
    redacted.write_text(pending.content, encoding="utf-8")
    assert not find_corpus_leaks(pending.content)
    digest = run_cli(ROOT / "triage.py", "digest", str(redacted), python_options=("-S",))
    assert digest.returncode == 1, digest.stderr
    assert f"ci.setup.{observed['id']}::{step}: {step} failed" in digest.stdout
