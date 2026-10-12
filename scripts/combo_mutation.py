#!/usr/bin/env python3
"""Single and pairwise mutation testing against triage/actions_log sources."""

from __future__ import annotations

import itertools
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "err_o_roadrunner"

GUARD_CMD = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "tests/test_multi_error_coverage.py",
    "-m",
    "mutation_guard",
]


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    path: Path
    old: str
    new: str


MUTATIONS: list[Mutation] = [
    Mutation(
        "gh_hits_ge3_to_ge4",
        SRC / "actions_log.py",
        "gh_hits >= 3 and (gh_hits >= nonempty",
        "gh_hits >= 4 and (gh_hits >= nonempty",
    ),
    Mutation(
        "gh_hits_ge3_to_ge2",
        SRC / "actions_log.py",
        "gh_hits >= 3 and (gh_hits >= nonempty",
        "gh_hits >= 2 and (gh_hits >= nonempty",
    ),
    Mutation(
        "disable_exception_group_prepare",
        SRC / "triage.py",
        "text = _prepare_python_trace_text(text)",
        "text = text",
    ),
    Mutation(
        "unlink_chain_marker",
        SRC / "triage.py",
        "        elif line in PYTHON_CHAIN_MARKERS:\n            if current:\n                blocks.append(_TraceBlock(current, linked))\n                current = []\n            next_linked = True",
        "        elif line in PYTHON_CHAIN_MARKERS:\n            if current:\n                blocks.append(_TraceBlock(current, linked))\n                current = []\n            next_linked = False",
    ),
    Mutation(
        "gh_cap_show_all",
        SRC / "actions_log.py",
        'label: str,\n    items: list[str],\n    cap: int = MAX_ACTIONS_ERROR_LINES,\n) -> None:\n    if not items:\n        report.add_not_found(f"actions log {label} ({source})")\n        return\n    report.add_found(f"actions log {label} ({source}): {len(items)}")\n    shown = items[:cap]',
        'label: str,\n    items: list[str],\n    cap: int = MAX_ACTIONS_ERROR_LINES,\n) -> None:\n    if not items:\n        report.add_not_found(f"actions log {label} ({source})")\n        return\n    report.add_found(f"actions log {label} ({source}): {len(items)}")\n    shown = items[:]',
    ),
    Mutation(
        "workflow_cap_show_all",
        SRC / "actions_log.py",
        "    cap = MAX_ACTIONS_ERROR_LINES\n    shown = items[:cap]\n    for i, wf in enumerate(shown, start=1):",
        "    cap = MAX_ACTIONS_ERROR_LINES\n    shown = items[:]\n    for i, wf in enumerate(shown, start=1):",
    ),
    Mutation(
        "gh_cap_omitted_off_by_one",
        SRC / "actions_log.py",
        "omitted = len(items) - len(shown)",
        "omitted = len(items) - len(shown) - 1",
    ),
    Mutation(
        "workflow_omitted_count_zero",
        SRC / "actions_log.py",
        "omitted = len(items) - len(shown)\n    if omitted > 0:\n        report.add_found(\n            f\"actions log workflow error omitted",
        "omitted = 0\n    if omitted > 0:\n        report.add_found(\n            f\"actions log workflow error omitted",
    ),
    Mutation(
        "skip_node_stack_when_python_present",
        SRC / "triage.py",
        "if _should_parse_node_stack(text):\n        parse_node_stack(text, report)",
        "if _should_parse_node_stack(text):\n        pass",
    ),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _apply_all(edits: list[Mutation]) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for mut in edits:
        if mut.path not in originals:
            originals[mut.path] = _read(mut.path)
    current = {p: originals[p] for p in originals}
    for mut in edits:
        text = current[mut.path]
        if mut.old not in text:
            raise RuntimeError(f"{mut.mutation_id}: pattern missing in {mut.path}")
        current[mut.path] = text.replace(mut.old, mut.new, 1)
    for path, text in current.items():
        _write(path, text)
    return originals


def _restore(originals: dict[Path, str]) -> None:
    for path, text in originals.items():
        _write(path, text)


def _run_guards() -> bool:
    proc = subprocess.run(GUARD_CMD, cwd=ROOT, capture_output=True, text=True, check=False)
    return proc.returncode == 0


def main() -> int:
    baseline_ok = _run_guards()
    if not baseline_ok:
        print("baseline mutation_guard tests failed before mutations", file=sys.stderr)
        return 2

    survivors: list[str] = []
    rows: list[tuple[str, str, str]] = []

    for mut in MUTATIONS:
        originals = _apply_all([mut])
        try:
            killed = not _run_guards()
        finally:
            _restore(originals)
        status = "killed" if killed else "SURVIVED"
        rows.append((mut.mutation_id, "single", status))
        if not killed:
            survivors.append(mut.mutation_id)

    for a, b in itertools.combinations(MUTATIONS, 2):
        label = f"{a.mutation_id}+{b.mutation_id}"
        try:
            originals = _apply_all([a, b])
        except RuntimeError as exc:
            rows.append((label, "pair", f"skip ({exc})"))
            continue
        try:
            killed = not _run_guards()
        finally:
            _restore(originals)
        status = "killed" if killed else "SURVIVED"
        rows.append((label, "pair", status))
        if not killed:
            survivors.append(label)

    print("mutation_id | kind | result")
    print("-" * 60)
    for mid, kind, status in rows:
        print(f"{mid} | {kind} | {status}")
    print("-" * 60)
    print(f"Survivors: {len(survivors)}")
    if survivors:
        for name in survivors:
            print(f"  - {name}")
        return 1
    print("All mutations killed (0 survivors).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
