"""Parse downloaded GitHub Actions job logs (read-only, stdlib only)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from err_o_roadrunner.triage import strip_terminal_controls

if TYPE_CHECKING:
    from err_o_roadrunner.triage import Report

GH_LOG_TS_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+"
)
GH_GROUP_START = re.compile(r"^##\[group\](?P<name>.*)$")
GH_GROUP_END = re.compile(r"^##\[endgroup\]\s*$")
GH_ERROR_MARKER = re.compile(r"^##\[error\](?P<msg>.*)$")
GH_WARNING_MARKER = re.compile(r"^##\[warning\](?P<msg>.*)$")
WORKFLOW_CMD = re.compile(
    r"^::(?P<kind>error|warning|notice|debug)(?:\s+(?P<params>[^:]*))?::(?P<msg>.*)$"
)
PROCESS_EXIT_RE = re.compile(
    r"Process completed with exit code (?P<code>-?\d+)",
    re.IGNORECASE,
)
STEP_RUN_GROUP = re.compile(r"^Run (?P<name>.+)$")


def strip_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def normalize_actions_log_line(line: str) -> str:
    line = line.rstrip("\r")
    line = GH_LOG_TS_PREFIX.sub("", line)
    return strip_terminal_controls(line)


def preprocess_actions_log(text: str) -> list[str]:
    text = strip_bom(strip_terminal_controls(text))
    return [normalize_actions_log_line(ln) for ln in text.splitlines()]


def parse_actions_log(text: str, source: str, report: Report) -> None:
    lines = preprocess_actions_log(text)
    if not any(ln.strip() for ln in lines):
        report.add_not_found(f"actions log content ({source})")
        return

    report.add_found(f"actions log lines ({source}): {len(lines)}")

    groups: list[tuple[str, list[str]]] = []
    current_name = ""
    current_lines: list[str] = []
    depth = 0

    for line in lines:
        m_start = GH_GROUP_START.match(line)
        if m_start:
            if depth == 0 and current_name:
                groups.append((current_name, current_lines))
                current_lines = []
            depth += 1
            if depth == 1:
                current_name = m_start.group("name").strip()
                current_lines = []
            else:
                current_lines.append(line)
            continue
        if GH_GROUP_END.match(line):
            if depth == 1:
                groups.append((current_name, current_lines))
                current_name = ""
                current_lines = []
            elif depth > 1:
                current_lines.append(line)
            depth = max(0, depth - 1)
            continue
        if depth >= 1:
            current_lines.append(line)

    error_lines: list[str] = []
    workflow_errors: list[str] = []
    exit_codes: list[int] = []
    failing_steps: list[str] = []

    for line in lines:
        m_err = GH_ERROR_MARKER.match(line)
        if m_err:
            msg = m_err.group("msg").strip()
            if msg:
                error_lines.append(msg)
            continue
        m_wf = WORKFLOW_CMD.match(line)
        if m_wf and m_wf.group("kind") == "error":
            workflow_errors.append(m_wf.group("msg").strip())
        m_exit = PROCESS_EXIT_RE.search(line)
        if m_exit:
            exit_codes.append(int(m_exit.group("code")))

    for name, body in groups:
        joined = "\n".join(body)
        if any(GH_ERROR_MARKER.match(ln) for ln in body):
            failing_steps.append(name)
        elif PROCESS_EXIT_RE.search(joined) and "exit code 0" not in joined.lower():
            if re.search(r"exit code (?!0)\d+", joined, re.I):
                failing_steps.append(name)
        for ln in body:
            if GH_ERROR_MARKER.match(ln) or WORKFLOW_CMD.match(ln):
                if name not in failing_steps:
                    failing_steps.append(name)

    if groups:
        report.add_found(f"actions log step groups ({source}): {len(groups)}")
        report.add_found(f"actions log last step name ({source}): {groups[-1][0]}")
    else:
        report.add_not_found(f"actions log step groups ({source})")

    if failing_steps:
        report.add_found(f"actions log failing step ({source}): {failing_steps[-1]}")
    else:
        report.add_not_found(f"actions log failing step ({source})")

    if exit_codes:
        report.add_found(f"actions log process exit code ({source}): {exit_codes[-1]}")
    else:
        report.add_not_found(f"actions log process exit code ({source})")

    if error_lines:
        report.add_found(f"actions log error marker (first) ({source}): {error_lines[0][:400]}")
        if len(error_lines) > 1:
            report.add_found(f"actions log error marker (last) ({source}): {error_lines[-1][:400]}")
    else:
        report.add_not_found(f"actions log ##[error] markers ({source})")

    if workflow_errors:
        report.add_found(
            f"actions log workflow error (first) ({source}): {workflow_errors[0][:400]}"
        )
    else:
        report.add_not_found(f"actions log ::error:: commands ({source})")

    # Re-use embedded failure text (Python/Node) from the failing step body when present.
    if failing_steps:
        for name, body in reversed(groups):
            if name == failing_steps[-1]:
                embedded = "\n".join(body)
                if embedded.strip():
                    from err_o_roadrunner.triage import detect_and_parse_log

                    detect_and_parse_log(embedded, report)
                break

    sample = error_lines[0] if error_lines else (workflow_errors[0] if workflow_errors else "")
    if sample:
        report.add_hint(f"actions log search keywords ({source}): {sample[:400]}")
    elif exit_codes and exit_codes[-1] != 0:
        report.add_hint(
            f"actions log search keywords ({source}): GitHub Actions exit code {exit_codes[-1]}"
        )
