"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): locks, pins, benchmark, CI lane, layout.

- fleet: Fleet contracts through the parent's shared checks (vendor/fleet_check.py): locks, pins, benchmark.
- ci_python_provider: Offline guards for the moving-latest CI interpreter lane.
- repo_layout: Repository layout: entry points at the repo root, shared helpers in errlib/ (no src/ package).
"""

from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
import unittest

import re

import pytest


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fleet = load_vendored("err_o_roadrunner_fleet_check", "vendor/fleet_check.py")


# ──── fleet ───────────────────────────────────────────────────────

ROOT = fleet.repo_root(__file__)
PARENT_WORKFLOW_PIN = "main"  # 🔁 own sources follow main (2026-11-28)


# Node sources and the shared checks stay on accepted upstream commits with exact bytes.
ACCEPTED = {
    "vendor/xprobe.py",
    "vendor/cli_args.py",
    "vendor/fleet_check.py",
}  # 🔁 locked destinations; they follow main


def test_vendor_lock_matches_checked_in_bytes_and_accepted_commits():
    fleet.check_lock(ROOT)


def test_parent_workflow_references_use_reviewed_rollout_commit():
    assert set(fleet.parent_refs(ROOT, skip=(".vendor-sync-tools",))) == {PARENT_WORKFLOW_PIN}


@pytest.mark.parametrize("optimized", [False, True], ids=["normal", "optimized"])
@pytest.mark.parametrize("mismatch", [False, True], ids=["same", "mismatch"])
def test_benchmark_parity_in_both_interpreter_modes(optimized, mismatch):
    patch = "mock.patch.object(g['subprocess'], 'check_output', return_value=open(%r, 'rb').read()).start()\n" % str(ROOT / "errlib" / "actions_log.py")
    if mismatch:
        patch += "mock.patch.object(g['actions_log'], '_analyze_step', return_value=g['actions_log']._StepMeta(name='different', body=[])).start()\n"
    result = fleet.run_main(ROOT, "scripts/benchmark_step_scan.py", patch, optimized=optimized)
    if mismatch:
        assert (result.returncode, result.stdout) == (1, ""), result.stderr
        assert "result mismatch" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("{"), result.stdout


# ──── ci_python_provider ──────────────────────────────────────────

def test_latest_python_policy_is_applied_during_setup_only():
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    job = fleet.workflow_job(ROOT, "test")
    steps = re.split(r"^      - ", job, flags=re.M)[1:]
    setup = [step for step in steps if "uses: astral-sh/setup-uv@" in step]
    assert len(setup) == 1
    step = setup[0]
    assert "env:\n          UV_PYTHON_PREFERENCE: ${{ matrix.python-version == '3.x' && 'only-managed' || 'managed' }}" in step
    assert 'python-version: "${{ matrix.python-version == \'3.x\' && \'3\' || matrix.python-version }}"' in step
    assert "activate-environment: true" in step
    assert text.count("UV_PYTHON_PREFERENCE") == 1
    assert "UV_PYTHON_PREFERENCE" not in job.split("steps:", 1)[0]
    assert """python-version: ${{ github.event_name == 'pull_request' && fromJSON('["3.15"]') || fromJSON('["3.11", "3.12", "3.13", "3.14", "3.15", "3.x"]') }}""" in job
    assert "junit-py3.x/junit.xml" in text


# ──── repo_layout ─────────────────────────────────────────────────

ROOT_MODULES = (
    "triage.py",
    "sentinel.py",
    "squad.py",
)
ERRLIB_MODULES = (
    "actions_log.py",
    "junit_redact.py",
    "project_version.py",
)


def test_main_modules_at_repository_root() -> None:
    for name in ROOT_MODULES:
        path = ROOT / name
        assert path.is_file(), f"expected module at repo root: {name}"


def test_helper_modules_live_in_errlib_package() -> None:
    assert (ROOT / "errlib" / "__init__.py").is_file()
    for name in ERRLIB_MODULES:
        assert (ROOT / "errlib" / name).is_file(), f"expected helper in errlib/: {name}"
        assert not (ROOT / name).exists(), f"helper must not also live at the repo root: {name}"


def test_no_src_package_layout() -> None:
    src = ROOT / "src"
    assert not src.exists(), "src/ must be removed after flattening modules to the repo root"


def test_no_stray_root_module_copies_under_tests_tree() -> None:
    allowed_names = set(ROOT_MODULES) | set(ERRLIB_MODULES)
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name in allowed_names:
            pytest.fail(
                f"stray copy of root module {path.name} under tests/: {path.relative_to(ROOT)}"
            )


# 🧰 Scripts: 📝 each one describes itself and ⌨️ one that reads sys.argv declares its arguments (argparse /
# cli_args). Read with ast, never run; the portfolio's script catalog reads the same facts.

class ScriptsDescribeThemselves(unittest.TestCase):
    def test_every_script_has_a_description_and_declared_arguments(self):
        fleet.check_scripts(fleet.repo_root(__file__))

    def test_no_top_level_vendor_package_imports(self):
        # 📦 vendored modules load by path under a repo-unique name (cli_args README: Loading a vendored copy)
        self.assertEqual(fleet.check_no_vendor_package_imports(ROOT), [])

    def test_pull_request_ci_stays_quick(self):
        # ⏱ push limited to branches, superseded PR runs cancelled, every job timed (fleet_check.check_ci)
        fleet.check_ci(fleet.repo_root(__file__))

    def test_root_scripts_import_only_the_standard_library(self):
        root = fleet.repo_root(__file__)
        for path in sorted(root.glob("*.py")):
            if path.name != "conftest.py":
                fleet.check_imports(root, path.name)
