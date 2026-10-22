"""Run sentinel benchmarks for golden regression (stdlib subprocess only)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

_SHORT_ELAPSED_RE = re.compile(r"( in )\d+\.\d+(s ==)")
_SHORT_PYTHON_VERSION_RE = re.compile(r"^  python version: .+$", re.M)
_SHORT_NODE_VERSION_RE = re.compile(r"^  node version: .+$", re.M)

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_CLI = [sys.executable, str(ROOT / "sentinel.py")]


def normalize_short_timing(short_text: str) -> str:
    """Strip wall-clock and runtime version variance for golden comparison."""
    text = _SHORT_ELAPSED_RE.sub(r"\1<t>s ==", short_text)
    text = _SHORT_PYTHON_VERSION_RE.sub("  python version: <runtime>", text)
    text = _SHORT_NODE_VERSION_RE.sub("  node version: <runtime>", text)
    return text


def run_benchmark(case_paths: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """Run sentinel on JUnit case paths; return (short stdout, report JSON dict)."""
    junit_args: list[str] = []
    for path in case_paths:
        junit_args.extend(["--junit", path])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report_path = handle.name
    proc = subprocess.run(
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
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    Path(report_path).unlink(missing_ok=True)
    return proc.stdout, report
