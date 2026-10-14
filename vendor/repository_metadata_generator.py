#!/usr/bin/env python3
"""Generate canonical repository metadata JSON and JSONL from a Git checkout.

Uses gh_identity.repository_metadata_v1 for the stable v1 contract.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from importlib import metadata as importlib_metadata
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess

import gh_identity


_TOOL_COMMANDS = {
    "git": ("--version",),
    "gh": ("--version",),
    "node": ("--version",),
    "npm": ("--version",),
    "npx": ("--version",),
}
_CLI_VERSION = r"[0-9]+(?:\.[0-9]+)+(?:[-+._][0-9A-Za-z][0-9A-Za-z.-]*)?"
_PACKAGE_VERSION = r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+(?:[0-9A-Za-z][0-9A-Za-z.-]*)?)?"
_TOOL_PATTERNS = {
    "git": re.compile(r"^git version (?P<version>" + _CLI_VERSION + r")(?:\s.*)?$"),
    "gh": re.compile(r"^gh version (?P<version>" + _CLI_VERSION + r")(?:\s.*)?$"),
    "node": re.compile(r"^v(?P<version>" + _CLI_VERSION + r")$"),
    "npm": re.compile(r"^(?P<version>" + _CLI_VERSION + r")$"),
    "npx": re.compile(r"^(?P<version>" + _CLI_VERSION + r")$"),
    "python": re.compile(r"^(?P<version>" + _PACKAGE_VERSION + r")$"),
}
_PLAIN_VERSION_RE = re.compile(r"^(?P<version>" + _PACKAGE_VERSION + r")$")
_DEFAULT_TOOL_TIMEOUT = 5.0


def _normalize_version_label(pattern: re.Pattern[str], text: str) -> str | None:
    if not isinstance(text, str):
        return None
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first or any(ord(char) < 32 for char in first):
        return None
    match = pattern.fullmatch(first)
    if not match:
        return None
    version = match.group("version")
    return version if len(version) <= 32 else None


def normalize_version_output(tool: str, text: str) -> str | None:
    """Return one short public CLI/runtime version label, or None."""
    return _normalize_version_label(
        _TOOL_PATTERNS.get(tool, _PLAIN_VERSION_RE),
        text,
    )


def observe_command_version(command: str, *, timeout: float = _DEFAULT_TOOL_TIMEOUT) -> str | None:
    """Observe one allowlisted CLI version without leaking execution details."""
    args = _TOOL_COMMANDS.get(command)
    if args is None:
        raise ValueError("unsupported tooling command: " + command)
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    candidate = result.stdout.strip() or result.stderr.strip()
    return normalize_version_output(command, candidate)


def observe_package_version(distribution: str) -> str | None:
    """Observe one explicitly requested Python distribution version."""
    try:
        value = importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return None
    return _normalize_version_label(_PLAIN_VERSION_RE, value)


def _requested_tooling_keys(
    *,
    include_python: bool,
    commands: Sequence[str],
    distributions: Sequence[tuple[str, str]],
) -> tuple[str, ...]:
    if isinstance(commands, (str, bytes)):
        raise TypeError("commands must be a sequence of command names")
    requested: list[str] = ["python"] if include_python else []
    for command in commands:
        if command not in _TOOL_COMMANDS:
            raise ValueError("unsupported tooling command: " + str(command))
        requested.append(command)
    for key, distribution in distributions:
        if not isinstance(key, str) or not key:
            raise ValueError("distribution tooling key must be non-empty")
        if not isinstance(distribution, str) or not distribution:
            raise ValueError("distribution name must be non-empty")
        requested.append(key)
    if len(requested) != len(set(requested)):
        raise ValueError("tooling keys must have exactly one canonical source")
    return tuple(requested)


def collect_portable_tooling(
    *,
    include_python: bool = True,
    commands: Sequence[str] = (),
    distributions: Sequence[tuple[str, str]] = (),
) -> dict[str, str]:
    """Collect canonical portable tooling with explicit, collision-safe ownership."""
    _requested_tooling_keys(
        include_python=include_python,
        commands=commands,
        distributions=distributions,
    )
    observed: dict[str, str] = {}
    if include_python:
        value = normalize_version_output("python", platform.python_version())
        if value is not None:
            observed["python"] = value
    for command in commands:
        value = observe_command_version(command)
        if value is not None:
            observed[command] = value
    for key, distribution in distributions:
        value = observe_package_version(distribution)
        if value is not None:
            observed[key] = value
    return observed


def record_from_checkout(
    root: Path,
    full_name: str,
    *,
    env: Mapping[str, str] | None = None,
    github_reported_size_bytes: int | None = None,
    working_tree_bytes: int | None = None,
    release_artifact_bytes: int | None = None,
    tooling: Mapping[str, str] | None = None,
    include_python_tooling: bool = False,
    tooling_commands: Sequence[str] = (),
    tooling_distributions: Sequence[tuple[str, str]] = (),
) -> dict:
    env = os.environ if env is None else env
    supplied = dict(tooling or {})
    requested = set(_requested_tooling_keys(
        include_python=include_python_tooling,
        commands=tooling_commands,
        distributions=tooling_distributions,
    ))
    overlap = set(supplied).intersection(requested)
    if overlap:
        raise ValueError("caller tooling overlaps canonical tooling: " + ", ".join(sorted(overlap)))

    canonical = collect_portable_tooling(
        include_python=include_python_tooling,
        commands=tooling_commands,
        distributions=tooling_distributions,
    ) if (include_python_tooling or tooling_commands or tooling_distributions) else {}
    supplied.update(canonical)

    measurements = {
        key: value
        for key, value in (
            ("github_reported_size_bytes", github_reported_size_bytes),
            ("working_tree_bytes", working_tree_bytes),
            ("release_artifact_bytes", release_artifact_bytes),
        )
        if value is not None
    }

    return gh_identity.repository_metadata_v1(
        root,
        repository=full_name,
        env=env,
        tooling=supplied,
        measurements=measurements or None,
    )


def write_metadata(record: dict, output_dir: Path) -> tuple[Path, Path]:
    gh_identity.write_repository_metadata(record, output_dir)
    json_path = output_dir / "repository-metadata.json"
    jsonl_path = output_dir / "repository-metadata.jsonl"
    return json_path, jsonl_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/name")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    write_metadata(record_from_checkout(args.root.resolve(), args.repository), args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
