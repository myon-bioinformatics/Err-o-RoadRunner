"""Run sentinel benchmarks for golden regression (stdlib subprocess only)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

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

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


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
    proc = subprocess.run(
        [
            *SENTINEL_CLI,
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
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
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
