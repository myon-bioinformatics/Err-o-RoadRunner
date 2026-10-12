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
        "drop_chain_marker_hint",
        SRC / "triage.py",
        "python exception chain marker without traceback body",
        "python exception chain marker DISABLED",
    ),
    Mutation(
        "drop_frame_hint",
        SRC / "triage.py",
        "python traceback without exception summary",
        "python traceback without exception DISABLED",
    ),
    Mutation(
        "drop_multi_failing_steps",
        SRC / "actions_log.py",
        'report.add_found(f"actions log failing steps ({source}): {len(failing_metas)}")',
        'report.add_found(f"actions log failing steps DISABLED ({source}): {len(failing_metas)}")',
    ),
    Mutation(
        "drop_log_wide_gh_errors",
        SRC / "actions_log.py",
        '_emit_capped_lines(report, source, "##[error] markers", log_gh)\n    _emit_workflow_errors',
        "pass  # dropped gh errors\n    _emit_workflow_errors",
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
