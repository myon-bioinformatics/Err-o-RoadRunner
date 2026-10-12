"""Resolve err-o-roadrunner version from checkout pyproject or installed metadata."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

DISTRIBUTION_NAME = "err-o-roadrunner"
VERSION_UNKNOWN = "unknown"


def _pep503_normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project_name_matches(name: str | None) -> bool:
    if not isinstance(name, str):
        return False
    return _pep503_normalize_name(name) == _pep503_normalize_name(DISTRIBUTION_NAME)


def _normalize_project_version(name: str | None, raw_version: str | None) -> str | None:
    if not _project_name_matches(name):
        return None
    if raw_version is None:
        return None
    if not isinstance(raw_version, str):
        return None
    stripped = raw_version.strip()
    if not stripped:
        return None
    return stripped


def _version_from_toml_data(data: dict[str, Any]) -> str | None:
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str):
        name = None
    if not isinstance(version, str):
        version = None
    return _normalize_project_version(name, version)


def _loads_toml(text: str) -> dict[str, Any] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def parse_pyproject_version(text: str) -> str | None:
    """Return normalized [project].version when TOML parses and name matches."""
    data = _loads_toml(text)
    if data is None:
        return None
    return _version_from_toml_data(data)


def read_pyproject_version(pyproject: Path) -> str | None:
    """Return [project].version when the project name matches, else None."""
    if not pyproject.is_file():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_pyproject_version(text)


def _installed_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def resolve_version(*, package_dir: Path | None = None) -> str:
    """Prefer checkout pyproject.toml beside the package; else installed metadata; else unknown."""
    root = package_dir if package_dir is not None else Path(__file__).resolve().parent
    checkout = read_pyproject_version(root / "pyproject.toml")
    if checkout is not None:
        return checkout
    installed = _installed_version()
    if installed is not None:
        return installed
    return VERSION_UNKNOWN
