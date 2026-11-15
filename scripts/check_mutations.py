#!/usr/bin/env python3
"""Mutation checks: every source mutation must make its guard tests fail (zero survivors).

  python scripts/check_mutations.py combo     # triage / actions_log, single and pairwise
  python scripts/check_mutations.py version   # errlib/project_version resolution order and name guard
"""

from __future__ import annotations

import argparse, itertools, subprocess, sys
from dataclasses import dataclass; from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_TARGET = ROOT / "errlib" / "project_version.py"


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    path: Path
    old: str
    new: str


COMBO_MUTATIONS: list[Mutation] = [
    Mutation(
        "gh_hits_ge3_to_ge4",
        ROOT / "errlib" / "actions_log.py",
        "gh_hits >= 3 and (gh_hits >= nonempty",
        "gh_hits >= 4 and (gh_hits >= nonempty",
    ),
    Mutation(
        "gh_hits_ge3_to_ge2",
        ROOT / "errlib" / "actions_log.py",
        "gh_hits >= 3 and (gh_hits >= nonempty",
        "gh_hits >= 2 and (gh_hits >= nonempty",
    ),
    Mutation(
        "disable_exception_group_prepare",
        ROOT / "triage.py",
        "text = _prepare_python_trace_text(text)",
        "text = text",
    ),
    Mutation(
        "unlink_chain_marker",
        ROOT / "triage.py",
        "        elif line in PYTHON_CHAIN_MARKERS:\n            if current:\n                blocks.append(_TraceBlock(current, linked))\n                current = []\n            next_linked = True",
        "        elif line in PYTHON_CHAIN_MARKERS:\n            if current:\n                blocks.append(_TraceBlock(current, linked))\n                current = []\n            next_linked = False",
    ),
    Mutation(
        "gh_cap_show_all",
        ROOT / "errlib" / "actions_log.py",
        'label: str,\n    items: list[str],\n    cap: int = MAX_ACTIONS_ERROR_LINES,\n) -> None:\n    if not items:\n        report.add_not_found(f"actions log {label} ({source})")\n        return\n    report.add_found(f"actions log {label} ({source}): {len(items)}")\n    shown = items[:cap]',
        'label: str,\n    items: list[str],\n    cap: int = MAX_ACTIONS_ERROR_LINES,\n) -> None:\n    if not items:\n        report.add_not_found(f"actions log {label} ({source})")\n        return\n    report.add_found(f"actions log {label} ({source}): {len(items)}")\n    shown = items[:]',
    ),
    Mutation(
        "workflow_cap_show_all",
        ROOT / "errlib" / "actions_log.py",
        "    cap = MAX_ACTIONS_ERROR_LINES\n    shown = items[:cap]\n    for i, wf in enumerate(shown, start=1):",
        "    cap = MAX_ACTIONS_ERROR_LINES\n    shown = items[:]\n    for i, wf in enumerate(shown, start=1):",
    ),
    Mutation(
        "gh_cap_omitted_off_by_one",
        ROOT / "errlib" / "actions_log.py",
        "omitted = len(items) - len(shown)",
        "omitted = len(items) - len(shown) - 1",
    ),
    Mutation(
        "workflow_omitted_count_zero",
        ROOT / "errlib" / "actions_log.py",
        "omitted = len(items) - len(shown)\n    if omitted > 0:\n        report.add_found(\n            f\"actions log workflow error omitted",
        "omitted = 0\n    if omitted > 0:\n        report.add_found(\n            f\"actions log workflow error omitted",
    ),
    Mutation(
        "skip_node_stack_when_python_present",
        ROOT / "triage.py",
        "if _should_parse_node_stack(text):\n        parse_node_stack(text, report)",
        "if _should_parse_node_stack(text):\n        pass",
    ),
    Mutation(
        "disable_node_syntax_user_location",
        ROOT / "triage.py",
        "    user_syntax = _node_syntax_user_location(text)\n    if user_syntax:",
        "    user_syntax = None\n    if user_syntax:",
    ),
    Mutation(
        "disable_node_rejection_headline",
        ROOT / "triage.py",
        "[\\w]*(?:Error|Warning|Rejection)|Unhandled\\s+rejection",
        "[\\w]*(?:Error|Warning)|Unhandled\\s+rejection",
    ),
]


VERSION_MUTATIONS: list[Mutation] = [
    Mutation(
        "drop_name_check",
        VERSION_TARGET,
        "    if not _project_name_matches(name):\n        return None",
        "    if False and not _project_name_matches(name):\n        return None",
    ),
    Mutation(
        "metadata_before_pyproject",
        VERSION_TARGET,
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
        VERSION_TARGET,
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
        VERSION_TARGET,
        "    return VERSION_UNKNOWN",
        '    return "not-unknown"',
    ),
    Mutation(
        "skip_toml_when_loader_missing",
        VERSION_TARGET,
        "    data = _loads_toml(text)\n"
        "    if data is None:\n"
        "        return None",
        "    data = _loads_toml(text)\n"
        "    if False and data is None:\n"
        "        return None",
    ),
    Mutation(
        "pep503_drop_dot_separator",
        VERSION_TARGET,
        '    return re.sub(r"[-_.]+", "-", name).lower()',
        '    return re.sub(r"[-_]+", "-", name).lower()',
    ),
    Mutation(
        "pep503_drop_plus_quantifier",
        VERSION_TARGET,
        '    return re.sub(r"[-_.]+", "-", name).lower()',
        '    return re.sub(r"[-_.]", "-", name).lower()',
    ),
]


# suite -> (mutations, guard tests, also pairwise, guard must pass unmutated first)
SUITES = {
    "combo": (COMBO_MUTATIONS, ["uv", "run", "pytest", "-q", "tests/test_triage.py", "-m", "mutation_guard"], True, True),
    "version": (VERSION_MUTATIONS, ["uv", "run", "pytest", "-q", "tests/test_version.py", "-k", "mutation_guard"], False, False),
}


def _apply_all(edits: list[Mutation]) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for mut in edits:
        if mut.path not in originals:
            originals[mut.path] = mut.path.read_text(encoding="utf-8")
    current = dict(originals)
    for mut in edits:
        text = current[mut.path]
        if mut.old not in text:
            raise RuntimeError(f"{mut.mutation_id}: pattern missing in {mut.path}")
        current[mut.path] = text.replace(mut.old, mut.new, 1)
    for path, text in current.items():
        path.write_text(text, encoding="utf-8")
    return originals


def _restore(originals: dict[Path, str]) -> None:
    for path, text in originals.items():
        path.write_text(text, encoding="utf-8")


def _guard_passes(guard: list[str]) -> bool:
    return subprocess.run(guard, cwd=ROOT, capture_output=True, text=True, check=False).returncode == 0


def _killed(guard: list[str], edits: list[Mutation]) -> bool:
    """Apply ``edits``, run the guard tests, restore the sources; True when the guard failed."""
    originals = _apply_all(edits)
    try:
        return not _guard_passes(guard)
    finally:
        _restore(originals)


def check_mutations(suite: str) -> int:
    """0 when every mutation of ``suite`` is killed, 1 with survivors, 2 when the guard fails unmutated."""
    mutations, guard, pairs, baseline = SUITES[suite]
    if baseline and not _guard_passes(guard):
        print("baseline mutation_guard tests failed before mutations", file=sys.stderr)
        return 2
    survivors: list[str] = []
    rows: list[tuple[str, str, str]] = []
    for mut in mutations:
        killed = _killed(guard, [mut])
        rows.append((mut.mutation_id, "single", "killed" if killed else "SURVIVED"))
        if not killed:
            survivors.append(mut.mutation_id)
    for a, b in itertools.combinations(mutations, 2) if pairs else ():
        label = f"{a.mutation_id}+{b.mutation_id}"
        try:
            killed = _killed(guard, [a, b])
        except RuntimeError as exc:
            rows.append((label, "pair", f"skip ({exc})"))
            continue
        rows.append((label, "pair", "killed" if killed else "SURVIVED"))
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
    print(f"All {suite} mutations killed (0 survivors).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("suite", choices=sorted(SUITES), help="combo: triage/actions_log, pairwise too; version: project_version")
    return check_mutations(ap.parse_args(sys.argv[1:] if argv is None else argv).suite)


if __name__ == "__main__":
    raise SystemExit(main())
