"""Project version is defined once in pyproject.toml and exposed by triage."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import triage

ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$')


def _pyproject_project_version() -> str:
    in_project = False
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project:
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            match = _PYPROJECT_VERSION_RE.match(stripped)
            if match:
                return match.group(1)
    raise AssertionError("version not found in pyproject.toml [project]")


def test_pyproject_name_is_err_o_roadrunner_distribution() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "err-o-roadrunner"' in text


def test_triage_version_matches_pyproject() -> None:
    assert triage.__version__ == _pyproject_project_version()


def test_cli_version_flag_matches_pyproject() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "triage.py"), "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    expected = _pyproject_project_version()
    assert proc.stdout.strip() == f"err-o-roadrunner {expected}"
