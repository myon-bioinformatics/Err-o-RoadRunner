#!/usr/bin/env python3
"""Mutation checks: every source mutation must make its guard tests fail (zero survivors).

  python scripts/check_mutations.py combo     # triage / actions_log, single and pairwise
  python scripts/check_mutations.py version   # errlib/project_version resolution order and name guard
  python scripts/check_mutations.py partial   # rules that classify the former partial JUnit corpus cases
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


# Each rule that moved a harvested "partial" JUnit corpus case to "well_handled", reverted.
PARTIAL_MUTATIONS: list[Mutation] = [
    Mutation(
        "drop_builtin_e_line_fallback",
        ROOT / "triage.py",
        "        m = PYTEST_E_NAME_LINE_RE.match(line)\n        if m and",
        "        m = None\n        if m and",
    ),
    Mutation(
        "builtin_fallback_accepts_any_name",
        ROOT / "triage.py",
        'if m and m.group("exc").rsplit(".", 1)[-1] in PYTHON_BUILTIN_EXCEPTIONS:',
        "if m:",
    ),
    Mutation(
        "drop_type_attr_fallback",
        ROOT / "triage.py",
        "    if JUNIT_BARE_EXCEPTION_NAME_RE.match(attr) and is_python_exception_type_name(attr):\n        return attr",
        "    if False:\n        return attr",
    ),
    Mutation(
        "type_attr_unchecked",
        ROOT / "triage.py",
        "    if JUNIT_BARE_EXCEPTION_NAME_RE.match(attr) and is_python_exception_type_name(attr):",
        "    if attr:",
    ),
    Mutation(
        "type_attr_from_last_failure",
        ROOT / "triage.py",
        "                        if not failure_blobs:\n                            primary_type_attr = child.attrib.get(\"type\") or \"\"\n                        failure_blobs.append(blob)\n                        failures.append(",
        "                        if True:\n                            primary_type_attr = child.attrib.get(\"type\") or \"\"\n                        failure_blobs.append(blob)\n                        failures.append(",
    ),
    Mutation(
        "type_attr_from_last_error",
        ROOT / "triage.py",
        "                        if not failure_blobs:\n                            primary_type_attr = child.attrib.get(\"type\") or \"\"\n                        failure_blobs.append(blob)\n                        errors.append(",
        "                        if True:\n                            primary_type_attr = child.attrib.get(\"type\") or \"\"\n                        failure_blobs.append(blob)\n                        errors.append(",
    ),
    Mutation(
        "type_attr_not_passed",
        ROOT / "triage.py",
        "_junit_pytest_exception_type(primary_blob, primary_message, primary_type_attr)",
        "_junit_pytest_exception_type(primary_blob, primary_message)",
    ),
    Mutation(
        "playwright_project_required",
        ROOT / "triage.py",
        'r"^(?:\\[(?P<project>[^\\]\\s]+)\\]\\s+›\\s+)?(?P<location>',
        'r"^\\[(?P<project>[^\\]\\s]+)\\]\\s+›\\s+(?P<location>',
    ),
    Mutation(
        "playwright_keep_trailing_rule",
        ROOT / "triage.py",
        'title = m.group("title").rstrip("─ ").strip()',
        'title = m.group("title").strip()',
    ),
    Mutation(
        "playwright_always_bracket_project",
        ROOT / "triage.py",
        "project = f\"[{m.group('project')}] \" if m.group(\"project\") else \"\"",
        "project = f\"[{m.group('project')}] \"",
    ),
    Mutation(
        "drop_import_mismatch_found",
        ROOT / "triage.py",
        "        if mismatch_found:\n            report.add_found(",
        "        if False:\n            report.add_found(",
    ),
    Mutation(
        "harvest_key_drop_playwright_error",
        ROOT / "scripts" / "harvest_corpus.py",
        '    "junit playwright error",\n',
        "",
    ),
    Mutation(
        "harvest_key_drop_import_mismatch",
        ROOT / "scripts" / "harvest_corpus.py",
        '    "junit collection import mismatch",\n',
        "",
    ),
    Mutation(
        "usage_graph_key_drop_playwright_error",
        ROOT / "scripts" / "usage_graph.py",
        '    "junit playwright error",\n',
        "",
    ),
    Mutation(
        "usage_graph_key_drop_import_mismatch",
        ROOT / "scripts" / "usage_graph.py",
        '    "junit collection import mismatch",\n',
        "",
    ),
    Mutation(
        "recapture_ignores_committed_runtime",
        ROOT / "scripts" / "capture_goldens.py",
        "        if prefix in kept:\n",
        "        if False:\n",
    ),
    Mutation(
        "recapture_last_committed_runtime_wins",
        ROOT / "scripts" / "capture_goldens.py",
        "        if prefix is not None and prefix not in kept:\n",
        "        if prefix is not None:\n",
    ),
    Mutation(
        "recapture_drops_line_ending",
        ROOT / "scripts" / "capture_goldens.py",
        "            line = kept[prefix] + line[len(body):]\n",
        "            line = kept[prefix] + \"\\n\"\n",
    ),
    Mutation(
        "recapture_skips_existing_golden",
        ROOT / "scripts" / "capture_goldens.py",
        "        if golden.exists():\n            text = keep_runtime_lines(",
        "        if False:\n            text = keep_runtime_lines(",
    ),
]


# suite -> (mutations, guard tests, also pairwise, guard must pass unmutated first)
# ⚡ this interpreter (already the project environment) instead of `uv run` per mutant; -x: the first failure kills it
_PYTEST = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
SUITES = {
    "combo": (COMBO_MUTATIONS, [*_PYTEST, "tests/test_triage.py", "-m", "mutation_guard"], True, True),
    "partial": (PARTIAL_MUTATIONS, [*_PYTEST, "tests/test_corpus_partial.py", "-m", "mutation_guard"], False, True),
    "version": (VERSION_MUTATIONS, [*_PYTEST, "tests/test_version.py", "-k", "mutation_guard"], False, False),
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
    ap.add_argument("suite", choices=sorted(SUITES), help="combo: triage/actions_log, pairwise too; version: project_version; partial: JUnit corpus partial-case rules")
    return check_mutations(ap.parse_args(sys.argv[1:] if argv is None else argv).suite)


if __name__ == "__main__":
    raise SystemExit(main())
