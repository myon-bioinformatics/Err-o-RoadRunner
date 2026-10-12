#!/usr/bin/env python3
"""Mutation checks for project_version resolution order and name guard."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "project_version.py"

GUARD_CMD = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "tests/test_version_matrix.py",
    "-k",
    "mutation_guard",
]


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    old: str
    new: str


MUTATIONS: list[Mutation] = [
    Mutation(
        "drop_name_check",
        "    if not _project_name_matches(name):\n        return None",
        "    if False and not _project_name_matches(name):\n        return None",
    ),
    Mutation(
        "metadata_before_pyproject",
        "    checkout = read_pyproject_version(root / \"pyproject.toml\")\n"
        "    if checkout is not None:\n"
        "        return checkout\n"
        "    installed = _installed_version()\n"
        "    if installed is not None:\n"
        "        return installed\n"
        "    return VERSION_UNKNOWN",
        "    installed = _installed_version()\n"
        "    if installed is not None:\n"
        "        return installed\n"
        "    checkout = read_pyproject_version(root / \"pyproject.toml\")\n"
        "    if checkout is not None:\n"
        "        return checkout\n"
        "    return VERSION_UNKNOWN",
    ),
    Mutation(
        "drop_oserror_catch",
        "    try:\n"
        "        text = pyproject.read_text(encoding=\"utf-8\")\n"
        "    except (OSError, UnicodeDecodeError):\n"
        "        return None\n"
        "    return parse_pyproject_version(text)",
        "    text = pyproject.read_text(encoding=\"utf-8\")\n"
        "    return parse_pyproject_version(text)",
    ),
    Mutation(
        "drop_unknown_fallback",
        "    return VERSION_UNKNOWN",
        '    return "not-unknown"',
    ),
    Mutation(
        "skip_toml_when_loader_missing",
        "    data = _loads_toml(text)\n"
        "    if data is None:\n"
        "        return None",
        "    data = _loads_toml(text)\n"
        "    if False and data is None:\n"
        "        return None",
    ),
    Mutation(
        "pep503_drop_dot_separator",
        '    return re.sub(r"[-_.]+", "-", name).lower()',
        '    return re.sub(r"[-_]+", "-", name).lower()',
    ),
    Mutation(
        "pep503_drop_plus_quantifier",
        '    return re.sub(r"[-_.]+", "-", name).lower()',
        '    return re.sub(r"[-_.]", "-", name).lower()',
    ),
]


def apply_mutation(mutation: Mutation) -> str:
    text = TARGET.read_text(encoding="utf-8")
    if mutation.old not in text:
        raise SystemExit(f"mutation anchor missing: {mutation.mutation_id}")
    return text.replace(mutation.old, mutation.new, 1)


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    survivors: list[str] = []
    for mutation in MUTATIONS:
        TARGET.write_text(original, encoding="utf-8")
        TARGET.write_text(apply_mutation(mutation), encoding="utf-8")
        proc = subprocess.run(GUARD_CMD, cwd=ROOT, check=False, capture_output=True)
        if proc.returncode == 0:
            survivors.append(mutation.mutation_id)
    TARGET.write_text(original, encoding="utf-8")
    print("-" * 60)
    print(f"Survivors: {len(survivors)}")
    if survivors:
        for name in survivors:
            print(f"  {name}")
        return 1
    print("All version mutations killed (0 survivors).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
