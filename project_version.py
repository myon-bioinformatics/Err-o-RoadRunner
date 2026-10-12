"""Resolve err-o-roadrunner version from checkout pyproject or installed metadata."""

from __future__ import annotations

import re
from pathlib import Path

DISTRIBUTION_NAME = "err-o-roadrunner"

_PROJECT_NAME_RE = re.compile(rf'^name\s*=\s*"{DISTRIBUTION_NAME}"\s*$')
_PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$')


def read_pyproject_version(pyproject: Path) -> str | None:
    """Return [project].version when the project name matches, else None."""
    if not pyproject.is_file():
        return None
    in_project = False
    saw_name = False
    found_version: str | None = None
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if not in_project:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if _PROJECT_NAME_RE.match(stripped):
            saw_name = True
        match = _PROJECT_VERSION_RE.match(stripped)
        if match:
            found_version = match.group(1)
    if saw_name and found_version is not None:
        return found_version
    return None


def resolve_version(*, package_dir: Path | None = None) -> str:
    """Prefer checkout pyproject.toml beside the package; else installed metadata."""
    root = package_dir if package_dir is not None else Path(__file__).resolve().parent
    checkout = read_pyproject_version(root / "pyproject.toml")
    if checkout is not None:
        return checkout
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError as exc:
        raise RuntimeError(f"cannot determine version for {DISTRIBUTION_NAME}") from exc
