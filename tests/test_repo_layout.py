"""Repository layout: triage modules live at the repo root (no src/ package)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ROOT_MODULES = (
    "triage.py",
    "actions_log.py",
    "junit_redact.py",
    "project_version.py",
    "sentinel.py",
)


def test_main_modules_at_repository_root() -> None:
    for name in ROOT_MODULES:
        path = ROOT / name
        assert path.is_file(), f"expected module at repo root: {name}"


def test_no_src_package_layout() -> None:
    src = ROOT / "src"
    assert not src.exists(), "src/ must be removed after flattening modules to the repo root"


def test_no_stray_root_module_copies_under_tests_tree() -> None:
    allowed_names = set(ROOT_MODULES)
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name in allowed_names:
            pytest.fail(
                f"stray copy of root module {path.name} under tests/: {path.relative_to(ROOT)}"
            )
