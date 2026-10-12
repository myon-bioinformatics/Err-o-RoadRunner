"""Project version is defined once in pyproject.toml and exposed by triage."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import triage
from project_version import DISTRIBUTION_NAME, read_pyproject_version, resolve_version

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def test_pyproject_name_is_err_o_roadrunner_distribution() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert f'name = "{DISTRIBUTION_NAME}"' in text


def test_triage_version_matches_pyproject() -> None:
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert triage.__version__ == expected


def test_cli_version_flag_matches_pyproject() -> None:
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


def test_cli_version_prefers_checkout_pyproject_over_installed_metadata(
    tmp_path: Path,
) -> None:
    dist_info = tmp_path / "err_o_roadrunner-0.0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        f"Name: {DISTRIBUTION_NAME}\n"
        "Version: 0.0.1\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "triage.py"), "--version"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    checkout_version = read_pyproject_version(PYPROJECT)
    assert checkout_version is not None
    assert checkout_version != "0.0.1"
    assert proc.stdout.strip() == f"err-o-roadrunner {checkout_version}"


def test_resolve_version_matches_pyproject_in_checkout() -> None:
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert resolve_version(package_dir=ROOT) == expected
