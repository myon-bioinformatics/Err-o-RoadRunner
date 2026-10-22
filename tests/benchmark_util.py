"""Run sentinel benchmarks for golden regression (stdlib subprocess only)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


def run_benchmark(case_paths: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """Run sentinel on JUnit case paths; return (short stdout, report JSON dict)."""
    junit_args: list[str] = []
    for path in case_paths:
        junit_args.extend(["--junit", path])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report_path = handle.name
    subprocess.run(
        [
            *SENTINEL_CLI,
            "--squads",
            "none",
            "--format",
            "short",
            "--report-json",
            report_path,
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
    short_proc = subprocess.run(
        [
            *SENTINEL_CLI,
            "--squads",
            "none",
            "--format",
            "short",
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
    return short_proc.stdout, report
