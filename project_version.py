"""Resolve err-o-roadrunner version from checkout pyproject or installed metadata."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DISTRIBUTION_NAME = "err-o-roadrunner"
VERSION_UNKNOWN = "unknown"

_PROJECT_SECTION_RE = re.compile(r"^\[project\]\s*$")
_PROJECT_TABLE_END_RE = re.compile(r"^\[")
_NAME_LINE_RE = re.compile(
    r"""^name\s*=\s*(?:"err-o-roadrunner"|'err-o-roadrunner')\s*(?:#.*)?$"""
)
_VERSION_LINE_RE = re.compile(
    r"""^version\s*=\s*(?:"([^"]+)"|'([^']+)')\s*(?:#.*)?$"""
)


def _version_from_project_table(project: object) -> str | None:
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    version = project.get("version")
    if name != DISTRIBUTION_NAME:
        return None
    if not isinstance(version, str):
        return None
    stripped = version.strip()
    if not stripped:
        return None
    return stripped


def _parse_pyproject_tomllib(text: str) -> str | None:
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return _version_from_project_table(data.get("project"))


def _parse_pyproject_fallback(text: str) -> str | None:
    in_project = False
    saw_name = False
    found_version: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if _PROJECT_SECTION_RE.match(stripped):
            in_project = True
            continue
        if not in_project:
            continue
        if _PROJECT_TABLE_END_RE.match(stripped) and stripped != "[project]":
            break
        if _NAME_LINE_RE.match(stripped):
            saw_name = True
        match = _VERSION_LINE_RE.match(stripped)
        if match and found_version is None:
            raw = match.group(1) or match.group(2)
            stripped_version = raw.strip()
            if stripped_version:
                found_version = stripped_version
    if saw_name and found_version is not None:
        return found_version
    return None


def _parse_pyproject_text(text: str) -> str | None:
    if sys.version_info >= (3, 11):
        return _parse_pyproject_tomllib(text)
    return _parse_pyproject_fallback(text)


def read_pyproject_version(pyproject: Path) -> str | None:
    """Return [project].version when the project name matches, else None."""
    if not pyproject.is_file():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _parse_pyproject_text(text)


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
