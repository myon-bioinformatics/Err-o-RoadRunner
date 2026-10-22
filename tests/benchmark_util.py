"""Run sentinel benchmarks for golden regression (stdlib subprocess only)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

_SHORT_ELAPSED_RE = re.compile(r"( in )\d+\.\d+(s ==)")
_SHORT_SQUAD_MS_RE = re.compile(r"^S (\S+ \S+ )\d+ms")
_JUNIT_PAREN_RUNNER_RE = re.compile(r"\(<runner-work>/")
_SHORT_ENV_LINE_RES = (
    re.compile(r"^  python version: .*$"),
    re.compile(r"^  node version: .*$"),
    re.compile(r"^  gh version: .*$"),
)

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


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
    return proc.stdout, report


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
