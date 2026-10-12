"""Partition tests for project_version resolution (pyproject × metadata × run mode).

Matrix size: 12 pyproject states × 3 metadata states × 4 run modes = 144 cells.

Unreachable (30 cells, see tests/version_matrix_support.UNREACHABLE):
- wheel_console × metadata absent (3): wheels always ship dist-info metadata.
- wheel_console × non-buildable pyproject (27): hatchling cannot build a wheel from
  missing/invalid/malformed pyproject fixtures.

Covered: 114 cells exercised by test_version_resolution_matrix_cell.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import triage
from project_version import VERSION_UNKNOWN, read_pyproject_version, resolve_version
from tests.version_matrix_support import (
    CHECKOUT_VERSION,
    META_VERSION,
    METADATA_STATES,
    PYPROJECT_STATES,
    RUN_MODES,
    UNREACHABLE,
    build_wheel,
    expected_version,
    install_fake_metadata,
    matrix_totals,
    metadata_version,
    populate_sandbox,
    pyproject_bytes,
    run_version_cli,
    wheel_pyproject_version,
    write_buildable_pyproject,
)

ROOT = Path(__file__).resolve().parents[1]


MATRIX_CASES = list(
    itertools.product(PYPROJECT_STATES, METADATA_STATES, RUN_MODES)
)


@pytest.mark.parametrize(
    ("pyproject_state", "metadata_state", "run_mode"),
    MATRIX_CASES,
    ids=[f"{a}|{b}|{c}" for a, b, c in MATRIX_CASES],
)
def test_version_resolution_matrix_cell(
    pyproject_state: str,
    metadata_state: str,
    run_mode: str,
    tmp_path: Path,
) -> None:
    key = (pyproject_state, metadata_state, run_mode)
    if key in UNREACHABLE:
        pytest.skip(UNREACHABLE[key])

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    install_fake_metadata(meta_dir, metadata_version(metadata_state))

    env = {"PYTHONPATH": os.pathsep.join([str(sandbox), str(meta_dir)])}
    wheel = None
    if run_mode == "wheel_console":
        for name in ("triage.py", "actions_log.py", "junit_redact.py", "project_version.py"):
            (sandbox / name).write_bytes((ROOT / name).read_bytes())
        write_buildable_pyproject(
            sandbox,
            pyproject_state,
            wheel_pyproject_version(metadata_state),
        )
        full = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        full = re.sub(
            r'^version\s*=\s*"[^"]+"',
            f'version = "{wheel_pyproject_version(metadata_state)}"',
            full,
            count=1,
            flags=re.MULTILINE,
        )
        (sandbox / "pyproject.toml").write_text(full, encoding="utf-8")
        shutil.copy2(ROOT / "README.md", sandbox / "README.md")
        wheel = build_wheel(sandbox, tmp_path / "wheels")
    else:
        populate_sandbox(sandbox, pyproject_state)
        if pyproject_state == "unreadable_oserror":
            pyproject = sandbox / "pyproject.toml"
            if os.access(pyproject, os.R_OK):
                pytest.skip("cannot simulate unreadable pyproject.toml on this runner")

    proc = run_version_cli(
        sandbox=sandbox,
        run_mode=run_mode,
        env=env,
        wheel=wheel,
    )
    assert proc.returncode == 0
    want = expected_version(pyproject_state, metadata_state, run_mode)
    assert proc.stdout.strip() == f"err-o-roadrunner {want}"

    if run_mode != "wheel_console":
        merged = os.environ.copy()
        merged.update(env)
        merged["PYTHONPATH"] = os.pathsep.join([str(sandbox), str(meta_dir)])
        runner = ROOT / "tests" / "isolated_triage_runner.py"
        if run_mode == "other_cwd":
            cmd = [sys.executable, str(runner), str(sandbox), "--exit-code", "0"]
            cwd = sandbox.parent
        elif run_mode == "module":
            cmd = [
                sys.executable,
                str(runner),
                str(sandbox),
                "--as-module",
                "--exit-code",
                "0",
            ]
            cwd = sandbox
        else:
            cmd = [sys.executable, str(runner), str(sandbox), "--exit-code", "0"]
            cwd = sandbox
        proc2 = subprocess.run(
            cmd,
            cwd=cwd,
            env=merged,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc2.returncode == 0
        assert "FOUND: exit code: 0" in proc2.stdout


def test_version_matrix_coverage_summary() -> None:
    total, unreachable, covered = matrix_totals()
    assert total == 144
    assert unreachable == 30
    assert covered == 114


def test_resolve_version_direct_matches_matrix_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("project_version._installed_version", lambda: None)
    for pyproject_state in PYPROJECT_STATES:
        sandbox = tmp_path / pyproject_state
        sandbox.mkdir()
        populate_sandbox(sandbox, pyproject_state)
        if pyproject_state == "unreadable_oserror":
            with mock.patch.object(
                Path,
                "read_text",
                side_effect=OSError("permission denied"),
            ):
                got = resolve_version(package_dir=sandbox)
        else:
            got = resolve_version(package_dir=sandbox)
        want = expected_version(pyproject_state, "absent", "checkout_script")
        assert got == want


def test_import_succeeds_with_unknown_version(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "iso"
    sandbox.mkdir()
    for name in ("triage.py", "actions_log.py", "junit_redact.py", "project_version.py"):
        (sandbox / name).write_bytes((ROOT / name).read_bytes())
    env = os.environ.copy()
    env["PYTHONPATH"] = str(sandbox)
    runner = ROOT / "tests" / "isolated_triage_runner.py"
    proc = subprocess.run(
        [sys.executable, str(runner), str(sandbox), "--version"],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "err-o-roadrunner unknown"
    proc2 = subprocess.run(
        [sys.executable, str(runner), str(sandbox), "--exit-code", "0"],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr


@pytest.mark.mutation_guard
def test_mutation_guard_pyproject_name_check_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "wrong_name")
    monkeypatch.setattr("project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pyproject_before_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "valid")
    monkeypatch.setattr("project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


def test_repo_checkout_version_matches_pyproject() -> None:
    expected = read_pyproject_version(ROOT / "pyproject.toml")
    assert expected is not None
    assert triage.__version__ == expected
