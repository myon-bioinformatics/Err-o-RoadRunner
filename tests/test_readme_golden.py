"""Golden CLI output examples (also documented in README)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README_FIX = ROOT / "tests" / "fixtures" / "readme"
MANIFEST = README_FIX / "manifest.json"
EMPTY_ROOT = README_FIX / "empty_root"


def normalize_report(text: str) -> str:
    root = str(ROOT.resolve())
    lines: list[str] = []
    for line in text.splitlines():
        line = line.replace(root, "<repo>")
        line = line.replace("/workspace", "<repo>")
        line = re.sub(r"/usr/lib/python3\.\d+/", "<pylib>/", line)
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


def run_example(entry: dict) -> str:
    example_id = entry["id"]
    cmd = [
        sys.executable,
        "-m",
        "triage",
        "--exit-code",
        "1",
        "--project-root",
        str(EMPTY_ROOT),
    ]
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

    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", json.loads(MANIFEST.read_text(encoding="utf-8")), ids=lambda e: e["id"])
def test_readme_golden_example(entry: dict) -> None:
    if entry["id"].startswith("node_"):
        if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
            pytest.skip("node not available")
    golden_path = ROOT / entry["golden"]
    expected = normalize_report(golden_path.read_text(encoding="utf-8"))
    actual = normalize_report(run_example(entry))
    assert actual == expected
