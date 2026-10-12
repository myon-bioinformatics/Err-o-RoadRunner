"""Repository layout: triage modules live at the repo root (no src/ package)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_MODULES = ("triage.py", "actions_log.py", "junit_redact.py")


def test_main_modules_at_repository_root() -> None:
    for name in ROOT_MODULES:
        path = ROOT / name
        assert path.is_file(), f"expected module at repo root: {name}"


def test_no_src_package_layout() -> None:
    src = ROOT / "src"
    assert not src.exists(), "src/ must be removed after flattening modules to the repo root"
