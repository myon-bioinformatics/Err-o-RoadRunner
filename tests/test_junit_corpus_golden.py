"""Golden tests for real failed-run JUnit XML corpus."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"


def normalize(text: str) -> str:
    text = text.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")
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


def run_cli(xml_path: Path) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "err_o_roadrunner",
            "--exit-code",
            "1",
            "--project-root",
            str(EMPTY),
            "--junit",
            str(xml_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.junit_corpus
@pytest.mark.parametrize(
    "entry",
    json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else [],
    ids=lambda e: e["id"],
)
def test_junit_corpus_golden(entry: dict) -> None:
    xml_path = CORPUS / entry["xml"]
    golden_path = CORPUS / entry["golden"]
    expected = normalize(golden_path.read_text(encoding="utf-8"))
    actual = normalize(run_cli(xml_path))
    assert actual == expected
    assert re.search(r"FOUND: junit failures|FOUND: junit errors", actual)
