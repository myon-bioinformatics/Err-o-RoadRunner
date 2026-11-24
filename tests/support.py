"""Shared test helpers (not collected): run triage / sentinel the way the goldens are captured and checked.

``scripts/capture_goldens.py`` and ``scripts/harvest_corpus.py`` import the same runner and masks, so a
captured golden and the test that compares it see the same command. Standard library plus the vendored,
stdlib-only ``vendor/cli_args.py`` at import time.
"""

from __future__ import annotations

import json, os, re, sys, tempfile
from copy import deepcopy; from pathlib import Path; from typing import Any, Mapping, Sequence

from vendor.cli_args import Command, run_cli, run_command

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
EMPTY = FIXTURES / "failure_suite" / "empty_root"
CORPUS_CASES = FIXTURES / "junit_corpus" / "cases"

RUNTIME_LINE_PREFIXES = (
    "FOUND: python version:",
    "FOUND: node version:",
    "FOUND: gh version:",
    "FOUND: environment variable count:",
    "FOUND: environment variable names:",
)


# ──── triage CLI and report text ────────────────────────────────────────


def triage_cli(*args: object, project_root: Path = EMPTY, exit_code: int = 1, check: bool = True) -> str:
    """stdout of ``python -m triage --exit-code N --project-root DIR ARGS`` run from the checkout."""
    cmd = [sys.executable, "-m", "triage", "--exit-code", str(exit_code), "--project-root", str(project_root)]
    proc = run_command(Command((*cmd, *map(str, args))), cwd=ROOT)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout


def mask_repo(text: str) -> str:
    """Checkout paths as ``<repo>`` (what captured goldens store)."""
    return text.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")


def normalize_report(text: str) -> str:
    """Golden comparison form: ``<repo>`` / ``<pylib>`` paths, runtime lines and Node internals masked."""
    text = re.sub(r"/usr/lib/python3\.\d+/", "<pylib>/", mask_repo(text))
    lines: list[str] = []
    for line in text.splitlines():
        prefix = next((p for p in RUNTIME_LINE_PREFIXES if line.startswith(p)), None)
        if prefix:
            line = f"{prefix} <runtime>"
        elif "node stack frame" in line or line.startswith("FOUND: node error"):
            line = re.sub(r"node:internal/[^:\s]+:\d+", "<node-internal>", line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def assert_report_has_exact_line(lines: list[str], expected: str) -> str:
    """Require an exact full-line match; partial lines and NOT FOUND must not satisfy."""
    if expected.startswith("FOUND:"):
        not_found_line = f"NOT FOUND: {expected.removeprefix('FOUND: ')}"
        if not_found_line in lines:
            raise AssertionError(
                f"ambiguous: NOT FOUND line present for expected FOUND line {expected!r}"
            )
    matches = [ln for ln in lines if ln == expected]
    assert len(matches) == 1, (
        f"expected exactly one line equal to {expected!r}, "
        f"got {len(matches)} matches among {len(lines)} lines"
    )
    return matches[0]


def triage_actions_log(log_path: Path) -> str:
    from triage import triage

    text = log_path.read_text(encoding="utf-8", errors="replace")
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        actions_log_text=text,
        junit_paths=[],
        project_root=ROOT,
    )
    return "\n".join(report.lines()) + "\n"


def triage_actions_log_cli(log_path: Path) -> str:
    return triage_cli("--actions-log", log_path, project_root=ROOT)


# ──── sentinel benchmarks ───────────────────────────────────────────────

_STABLE_ENV_KEYS = ("variable_count", "variable_names", "variable_names_truncated")

_SHORT_ELAPSED_RE = re.compile(r"( in )\d+\.\d+(s ==)")
_SHORT_SQUAD_MS_RE = re.compile(r"^S (\S+ \S+ )\d+ms")
_JUNIT_PAREN_RUNNER_RE = re.compile(r"\(<runner-work>/")
_SHORT_ENV_LINE_RES = (
    re.compile(r"^  python version: .*$"),
    re.compile(r"^  node version: .*$"),
    re.compile(r"^  gh version: .*$"),
)
_SUMMARY_MASK_PREFIXES = (
    "python version:",
    "node version:",
    "gh version:",
    "environment variable names:",
)


def corpus_cases(pattern: str) -> list[str]:
    """Sorted JUnit corpus case paths matching ``pattern``."""
    return [str(p) for p in sorted(CORPUS_CASES.glob(pattern))]


def records_without(doc: dict, *keys: str) -> list:
    """Report ``records`` with the given keys dropped (fields that vary by runtime)."""
    return [{k: v for k, v in rec.items() if k not in keys} for rec in doc.get("records") or []]


def _mask_triage_line(line: str) -> str:
    for prefix in _SUMMARY_MASK_PREFIXES:
        if line.startswith(prefix):
            return f"{prefix} <masked>"
    return line


def _mask_human_summary(text: str) -> str:
    masked = text
    for prefix in _SUMMARY_MASK_PREFIXES:
        masked = re.sub(
            rf"{re.escape(prefix)}[^\n]*",
            f"{prefix} <masked>",
            masked,
        )
    return masked


def normalize_report_benchmark(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Drop volatile environment values and mask env/tool lines for benchmark goldens."""
    out = deepcopy(dict(doc))
    env = out.get("environment")
    if isinstance(env, dict):
        out["environment"] = {
            key: env[key] for key in _STABLE_ENV_KEYS if key in env
        }
        out["environment"]["values_redacted"] = {}
        if "variable_names" in out["environment"]:
            out["environment"]["variable_names"] = ["<masked>"]
    command = out.get("command")
    if isinstance(command, dict) and command.get("cwd"):
        command["cwd"] = "<repo>"
    triage = out.get("triage")
    if isinstance(triage, dict):
        for field in ("found", "hints", "not_found"):
            lines = triage.get(field)
            if not isinstance(lines, list):
                continue
            triage[field] = [_mask_triage_line(str(line)) for line in lines]
    summary = out.get("human_summary")
    if isinstance(summary, str):
        out["human_summary"] = _mask_human_summary(summary)
    return out


def normalize_short_timing(short_text: str) -> str:
    """Strip wall-clock footer variance for golden comparison."""
    return _SHORT_ELAPSED_RE.sub(r"\1<t>s ==", short_text)


def normalize_short_benchmark(short_text: str, root: Path | None = None) -> str:
    """Normalize short stdout for golden comparison (timing, env, squad ms, paths)."""
    text = normalize_short_timing(short_text)
    if root is not None:
        root_text = str(root.resolve())
        text = text.replace(root_text + os.sep, "")
        text = text.replace(root_text + "/", "")
    text = _JUNIT_PAREN_RUNNER_RE.sub("(", text)
    lines: list[str] = []
    for line in text.splitlines():
        if _SHORT_ENV_LINE_RES[0].match(line):
            lines.append("  python version: <masked>")
        elif _SHORT_ENV_LINE_RES[1].match(line):
            lines.append("  node version: <masked>")
        elif _SHORT_ENV_LINE_RES[2].match(line):
            lines.append("  gh version: <masked>")
        else:
            match = _SHORT_SQUAD_MS_RE.match(line)
            if match:
                lines.append(f"S {match.group(1)}<ms>")
            else:
                lines.append(line)
    if text.endswith("\n"):
        return "\n".join(lines) + "\n"
    return "\n".join(lines)


def run_benchmark(
    case_paths: Sequence[str],
    *,
    squads: str = "none",
    max_records: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run sentinel on JUnit case paths; return (short stdout, report JSON dict)."""
    junit_args: list[str] = []
    root = ROOT.resolve()
    for path in case_paths:
        candidate = Path(path)
        try:
            junit_args.extend(["--junit", str(candidate.resolve().relative_to(root))])
        except ValueError:
            junit_args.extend(["--junit", path])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report_path = handle.name
    extra: list[str] = []
    if max_records is not None:
        extra.extend(["--max-records", str(max_records)])
    proc = run_cli(
        ROOT / "sentinel.py",
        "--squads",
        squads,
        "--offline",
        "--format",
        "short",
        "--report-json",
        report_path,
        *extra,
        *junit_args,
        "--",
        "sh",
        "-c",
        "exit 0",
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    Path(report_path).unlink(missing_ok=True)
    return proc.stdout, normalize_report_benchmark(report)


def median_merge_records_seconds(path: str, *, rounds: int = 6) -> float:
    """Median wall time to parse and merge one JUnit XML in-process."""
    import statistics
    import time

    import squad

    xml_path = Path(path)
    squad.merge_failure_records([xml_path])
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        squad.merge_failure_records([xml_path])
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)
