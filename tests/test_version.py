"""Smoke tests for project version (partition matrix lives in test_version_matrix.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import triage
from project_version import DISTRIBUTION_NAME, VERSION_UNKNOWN, read_pyproject_version

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def test_pyproject_name_is_err_o_roadrunner_distribution() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert f'name = "{DISTRIBUTION_NAME}"' in text


def test_triage_version_matches_pyproject_in_checkout() -> None:
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert triage.__version__ == expected


def test_cli_version_flag_matches_pyproject_in_checkout() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "triage.py"), "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert proc.stdout.strip() == f"err-o-roadrunner {expected}"


def test_unknown_version_string_constant() -> None:
    assert VERSION_UNKNOWN == "unknown"
