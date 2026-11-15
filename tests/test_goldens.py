"""Golden CLI output: deliberate failure suite and README examples (scripts/capture_goldens.py writes them).

- failure_suite_golden: Deliberate-failure suite: real failures vs checked-in goldens.
- readme_golden: Golden CLI output examples (also documented in README).
"""

from __future__ import annotations

import json, os, subprocess

import pytest

from tests.support import EMPTY, FIXTURES, normalize_report, ROOT, triage_cli


# ──── failure_suite_golden ────────────────────────────────────────

SUITE = FIXTURES / "failure_suite"
SUITE_MANIFEST = SUITE / "manifest.json"


def run_entry(entry: dict) -> str:
    cmd: list[object] = []
    if "stderr" in entry:
        cmd.extend(["--stderr", str(ROOT / entry["stderr"])])
    if "log" in entry:
        cmd.extend(["--log", str(ROOT / entry["log"])])
    if "actions_log" in entry:
        cmd.extend(["--actions-log", str(ROOT / entry["actions_log"])])
    for path in entry.get("junit", []):
        cmd.extend(["--junit", str(ROOT / path)])
    return triage_cli(*cmd, project_root=EMPTY)


@pytest.mark.failure_suite
@pytest.mark.parametrize(
    "entry",
    json.loads(SUITE_MANIFEST.read_text(encoding="utf-8")) if SUITE_MANIFEST.exists() else [],
    ids=lambda e: e["id"],
)
def test_failure_suite_golden(entry: dict) -> None:
    if entry.get("category") == "node" and subprocess.run(["which", "node"], capture_output=True).returncode != 0:
        pytest.skip("node not available")
    expected = normalize_report((ROOT / entry["golden"]).read_text(encoding="utf-8"))
    actual = normalize_report(run_entry(entry))
    assert actual == expected


# ──── readme_golden ───────────────────────────────────────────────

README_FIX = FIXTURES / "readme"
README_MANIFEST = README_FIX / "manifest.json"
EMPTY_ROOT = README_FIX / "empty_root"


def run_example(entry: dict) -> str:
    example_id = entry["id"]
    cmd: list[object] = []
    input_path = ROOT / entry["input"]
    if example_id == "hostile_symlink":
        link = README_FIX / "inputs" / "secret_link.stderr"
        target = README_FIX / "inputs" / "python_key_error.stderr"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.unlink()
        os.symlink(target, link)
        cmd.extend(["--stderr", str(link)])
    elif entry["input"].endswith((".stderr", ".log")):
        flag = "--log" if entry["input"].endswith(".log") else "--stderr"
        cmd.extend([flag, str(input_path)])
    elif entry["input"].endswith(".xml"):
        cmd.extend(["--junit", str(input_path)])
    else:
        raise AssertionError(f"unknown input for {example_id}: {entry['input']}")

    return triage_cli(*cmd, project_root=EMPTY_ROOT)


@pytest.mark.parametrize("entry", json.loads(README_MANIFEST.read_text(encoding="utf-8")), ids=lambda e: e["id"])
def test_readme_golden_example(entry: dict) -> None:
    if entry["id"].startswith("node_"):
        if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
            pytest.skip("node not available")
    golden_path = ROOT / entry["golden"]
    expected = normalize_report(golden_path.read_text(encoding="utf-8"))
    actual = normalize_report(run_example(entry))
    assert actual == expected
