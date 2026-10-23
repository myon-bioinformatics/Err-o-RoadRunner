"""Exercise the workflow's preparation and validation commands without network access."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from junit_redact import find_corpus_leaks
from scripts import harvest_junit_corpus as harvest

ROOT = Path(__file__).resolve().parents[1]
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
    for name in ("triage.py", "junit_redact.py", "project_version.py", "pyproject.toml",
                 "scripts/reredact_junit_corpus.py", "scripts/patch_junit_manifest_jobs.py",
                 "scripts/capture_junit_corpus_goldens.py", "tests/test_junit_corpus_golden.py",
                 "tests/test_results_corpus.py", "tests/test_fixture_redaction_scan.py"):
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
    assert WORKFLOW.index("- name: Validate corpus") < WORKFLOW.index("- name: Open PR with new entries")
    pr_step = WORKFLOW.split("- name: Open PR with new entries", 1)[1]
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
        module = sandbox / "junit_redact.py"
        text = module.read_text().replace("    redact_xml_tree(root)", "    # redaction disabled")
        module.write_text(text)
    result = _run_step(sandbox, "Redact corpus (Silver)")
    assert result.returncode != 0
    assert "Patched" not in result.stdout
