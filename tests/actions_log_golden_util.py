"""Shared helpers for GitHub Actions log golden tests."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from err_o_roadrunner.triage import triage
from tests.report_assert import assert_report_has_exact_line

ROOT = Path(__file__).resolve().parents[1]

__all__ = [
    "assert_report_has_exact_line",
    "normalize_report",
    "triage_actions_log",
    "triage_actions_log_cli",
]


def normalize_report(text: str) -> str:
    text = text.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")
    text = re.sub(r"/usr/lib/python3\.\d+/", "<pylib>/", text)
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("FOUND: python version:"):
            lines.append("FOUND: python version: <runtime>")
        elif line.startswith("FOUND: node version:"):
            lines.append("FOUND: node version: <runtime>")
        elif line.startswith("FOUND: gh version:"):
            lines.append("FOUND: gh version: <runtime>")
        elif line.startswith("FOUND: environment variable count:"):
            lines.append("FOUND: environment variable count: <runtime>")
        elif line.startswith("FOUND: environment variable names:"):
            lines.append("FOUND: environment variable names: <runtime>")
        elif "node stack frame" in line or line.startswith("FOUND: node error"):
            line = re.sub(r"node:internal/[^:\s]+:\d+", "<node-internal>", line)
            lines.append(line)
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def triage_actions_log(log_path: Path) -> str:
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
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "err_o_roadrunner",
            "--exit-code",
            "1",
            "--actions-log",
            str(log_path),
            "--project-root",
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


