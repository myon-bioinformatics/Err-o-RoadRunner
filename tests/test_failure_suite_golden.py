"""Deliberate-failure suite: real failures vs checked-in goldens."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "fixtures" / "failure_suite"
MANIFEST = SUITE / "manifest.json"
EMPTY = SUITE / "empty_root"


def normalize(text: str) -> str:
    text = text.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")
    text = re.sub(r"/usr/lib/python3\.\d+/", "<pylib>/", text)
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("FOUND: python version:"):
            lines.append("FOUND: python version: <runtime>")
        elif line.startswith("FOUND: node version:"):
            lines.append("FOUND: node version: <runtime>")
        elif line.startswith("FOUND: gh version:"):
            lines.append("FOUND: gh version: <runtime>")
        elif line.startswith("FOUND: environment variable count:"):
            lines.append("FOUND: environment variable count: <runtime>")
        elif line.startswith("FOUND: environment variable names:"):
            lines.append("FOUND: environment variable names: <runtime>")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def run_entry(entry: dict) -> str:
    cmd = [sys.executable, "-m", "err_o_roadrunner", "--exit-code", "1", "--project-root", str(EMPTY)]
    if "stderr" in entry:
        cmd.extend(["--stderr", str(ROOT / entry["stderr"])])
    if "log" in entry:
        cmd.extend(["--log", str(ROOT / entry["log"])])
    if "actions_log" in entry:
        cmd.extend(["--actions-log", str(ROOT / entry["actions_log"])])
    for path in entry.get("junit", []):
        cmd.extend(["--junit", str(ROOT / path)])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.failure_suite
@pytest.mark.parametrize(
    "entry",
    json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else [],
    ids=lambda e: e["id"],
)
def test_failure_suite_golden(entry: dict) -> None:
    if entry.get("category") == "node" and subprocess.run(["which", "node"], capture_output=True).returncode != 0:
        pytest.skip("node not available")
    expected = normalize((ROOT / entry["golden"]).read_text(encoding="utf-8"))
    actual = normalize(run_entry(entry))
    assert actual == expected
