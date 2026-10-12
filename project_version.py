"""Resolve err-o-roadrunner version from checkout pyproject or installed metadata."""

from __future__ import annotations

import re
from pathlib import Path

DISTRIBUTION_NAME = "err-o-roadrunner"
VERSION_UNKNOWN = "unknown"

_PROJECT_SECTION_RE = re.compile(r"^\[project\]\s*$")
_PROJECT_TABLE_END_RE = re.compile(r"^\[")
_NAME_VALUE_RE = re.compile(
    r"""^name\s*=\s*(?:"([^"]+)"|'([^']+)')\s*(?:#.*)?$"""
)
_VERSION_LINE_RE = re.compile(
    r"""^version\s*=\s*(?:"([^"]+)"|'([^']+)')\s*(?:#.*)?$"""
)


def _normalize_project_version(name: str | None, raw_version: str | None) -> str | None:
    if name != DISTRIBUTION_NAME:
        return None
    if raw_version is None:
        return None
    stripped = raw_version.strip()
    if not stripped:
        return None
    return stripped


def _extract_project_fields_from_lines(text: str) -> tuple[str | None, str | None]:
    """Read name and first version= from [project] only (duplicate keys: first wins)."""
    in_project = False
    found_name: str | None = None
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
        name_match = _NAME_VALUE_RE.match(stripped)
        if name_match:
            found_name = name_match.group(1) or name_match.group(2)
        version_match = _VERSION_LINE_RE.match(stripped)
        if version_match and found_version is None:
            raw = version_match.group(1) or version_match.group(2)
            found_version = raw
    return found_name, found_version


def parse_pyproject_version_from_lines(text: str) -> str | None:
    """Line-based [project] extraction (canonical on all Python versions)."""
    name, raw_version = _extract_project_fields_from_lines(text)
    return _normalize_project_version(name, raw_version)


def parse_pyproject_version_from_tomllib(text: str) -> str | None:
    """[project] name/version via tomllib, then shared normalization (tests / parity)."""
    try:
        import tomllib
    except ImportError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return _normalize_project_version(None, None)
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str):
        name = None
    if not isinstance(version, str):
        version = None
    return _normalize_project_version(name, version)


def read_pyproject_version(pyproject: Path) -> str | None:
    """Return [project].version when the project name matches, else None."""
    if not pyproject.is_file():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_pyproject_version_from_lines(text)


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
