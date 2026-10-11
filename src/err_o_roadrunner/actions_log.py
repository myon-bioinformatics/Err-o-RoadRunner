"""Parse downloaded GitHub Actions job logs (read-only, stdlib only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from err_o_roadrunner.triage import strip_terminal_controls

if TYPE_CHECKING:
    from err_o_roadrunner.triage import Report

GH_LOG_TS_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z "
)
GH_RUN_VIEW_REST_TS = re.compile(
    r"^(?:\ufeff)?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z "
)
RUN_STEP_START = re.compile(r"^##\[group\]Run (?P<name>.+)$")
GH_GROUP_END = re.compile(r"^##\[endgroup\]\s*$")
GH_ERROR_LINE = re.compile(r"^##\[error\](?P<msg>.*)$")
GH_WARNING_LINE = re.compile(r"^##\[warning\](?P<msg>.*)$")
WORKFLOW_CMD = re.compile(
    r"^::(?P<kind>error|warning|notice|debug)(?:\s+(?P<params>[^:]*))?::(?P<msg>.*)$"
)
PROCESS_EXIT_RE = re.compile(
    r"Process completed with exit code (?P<code>-?\d+)",
    re.IGNORECASE,
)
PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(\S+)\s+-\s+(.*)$")
FAKE_ERROR_IN_OUTPUT = re.compile(r"##\[error\]")
GH_UNKNOWN_STEP = "UNKNOWN STEP"


@dataclass(frozen=True)
class _ParsedLine:
    text: str
    gh_job: str | None = None
    gh_step: str | None = None


def strip_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _strip_log_timestamp_prefix(line: str) -> str:
    line = strip_bom(line)
    return GH_LOG_TS_PREFIX.sub("", line, count=1)


def normalize_actions_log_line(line: str) -> str:
    line = _strip_log_timestamp_prefix(line)
    return strip_terminal_controls(line)


def _looks_like_gh_run_view_rest(rest: str) -> bool:
    return bool(GH_RUN_VIEW_REST_TS.match(rest))


def _parse_gh_run_view_raw_line(line: str) -> _ParsedLine | None:
    parts = line.split("\t", 2)
    if len(parts) != 3:
        return None
    job, step, rest = parts
    if not _looks_like_gh_run_view_rest(rest):
        return None
    return _ParsedLine(
        text=normalize_actions_log_line(rest),
        gh_job=job,
        gh_step=step,
    )


def preprocess_actions_log(text: str) -> tuple[list[_ParsedLine], bool]:
    text = strip_bom(strip_terminal_controls(text))
    # splitlines() drops trailing \\r; split on \\n so CRLF lines keep \\r for rstrip in normalize.
    raw_lines = text.split("\n")
    if raw_lines and not text.endswith("\n"):
        pass
    elif raw_lines and text.endswith("\n") and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    parsed: list[_ParsedLine] = []
    gh_hits = 0
    for raw in raw_lines:
        raw = raw.rstrip("\r")
        gh = _parse_gh_run_view_raw_line(raw)
        if gh is not None:
            gh_hits += 1
            parsed.append(gh)
        else:
            parsed.append(_ParsedLine(text=normalize_actions_log_line(raw)))
    nonempty = sum(1 for ln in parsed if ln.text.strip())
    is_gh_run_view = gh_hits >= 3 and (gh_hits >= nonempty or gh_hits / max(nonempty, 1) >= 0.5)
    return parsed, is_gh_run_view


def split_run_steps(lines: list[str]) -> list[tuple[str, list[str]]]:
    """A Run step owns its group block and all following lines until the next Run group."""
    steps: list[tuple[str, list[str]]] = []
    current_name = ""
    current_body: list[str] = []

    for line in lines:
        m_run = RUN_STEP_START.match(line)
        if m_run:
            if current_name:
                steps.append((current_name, current_body))
            current_name = m_run.group("name").strip()
            current_body = [line]
            continue
        if current_name:
            current_body.append(line)

    if current_name:
        steps.append((current_name, current_body))
    return steps


def split_gh_run_view_steps(records: list[_ParsedLine]) -> list[tuple[str, list[str]]]:
    """Group by gh step column when the step name is present (not UNKNOWN STEP)."""
    steps: list[tuple[str, list[str]]] = []
    current_key: tuple[str, str] | None = None
    current_name = ""
    current_body: list[str] = []

    for rec in records:
        step = (rec.gh_step or "").strip()
        job = (rec.gh_job or "").strip()
        if not step or step == GH_UNKNOWN_STEP:
            if current_key is not None:
                steps.append((current_name, current_body))
                current_key = None
                current_name = ""
                current_body = []
            continue
        key = (job, step)
        if current_key != key:
            if current_key is not None:
                steps.append((current_name, current_body))
            current_key = key
            current_name = step if not job else f"{job} / {step}"
            current_body = [rec.text]
        else:
            current_body.append(rec.text)

    if current_key is not None:
        steps.append((current_name, current_body))
    return steps


def _resolve_steps(records: list[_ParsedLine], is_gh_run_view: bool) -> list[tuple[str, list[str]]]:
    line_texts = [rec.text for rec in records]
    run_steps = split_run_steps(line_texts)
    if run_steps:
        return run_steps
    if is_gh_run_view:
        gh_steps = split_gh_run_view_steps(records)
        if gh_steps:
            return gh_steps
    return []


def _step_process_exit_code(body: list[str]) -> int | None:
    for line in body:
        m = GH_ERROR_LINE.match(line)
        if not m:
            continue
        msg = m.group("msg")
        m_exit = PROCESS_EXIT_RE.search(msg)
        if m_exit:
            return int(m_exit.group("code"))
    for line in body:
        m_exit = PROCESS_EXIT_RE.search(line)
        if m_exit:
            return int(m_exit.group("code"))
    return None


def _collect_official_error_messages(body: list[str]) -> list[str]:
    msgs: list[str] = []
    for line in body:
        m = GH_ERROR_LINE.match(line)
        if m:
            msg = m.group("msg").strip()
            if msg:
                msgs.append(msg)
    return msgs


def _collect_workflow_errors(body: list[str]) -> list[str]:
    out: list[str] = []
    for line in body:
        m = WORKFLOW_CMD.match(line)
        if m and m.group("kind") == "error":
            out.append(m.group("msg").strip())
    return out


def _collect_pytest_failed(body: list[str]) -> list[str]:
    failed: list[str] = []
    for line in body:
        stripped = line.strip()
        m = PYTEST_FAILED_RE.match(stripped)
        if m:
            failed.append(f"FAILED {m.group(1)} - {m.group(2)}")
        elif stripped.startswith("FAILED "):
            failed.append(stripped)
    return failed


def _hint_user_printed_error_markers(body: list[str], report: Report, source: str) -> None:
    for line in body:
        if GH_ERROR_LINE.match(line) or GH_WARNING_LINE.match(line):
            continue
        if FAKE_ERROR_IN_OUTPUT.search(line) and not line.startswith("##[error]"):
            report.add_hint(
                f"actions log inline error marker (not a workflow line) ({source}): {line.strip()[:200]}"
            )


def parse_actions_log(text: str, source: str, report: Report) -> None:
    records, is_gh_run_view = preprocess_actions_log(text)
    lines = [rec.text for rec in records]
    if not any(ln.strip() for ln in lines):
        report.add_not_found(f"actions log content ({source})")
        return

    report.add_found(f"actions log lines ({source}): {len(lines)}")
    if is_gh_run_view:
        report.add_found(f"actions log format ({source}): gh run view")

    steps = _resolve_steps(records, is_gh_run_view)
    if steps:
        report.add_found(f"actions log run steps ({source}): {len(steps)}")
        report.add_found(f"actions log last run step ({source}): {steps[-1][0]}")
    else:
        report.add_not_found(f"actions log run steps ({source})")

    failing_step: tuple[str, list[str]] | None = None
    failing_code: int | None = None
    for name, body in steps:
        code = _step_process_exit_code(body)
        if code is not None and code != 0:
            failing_step = (name, body)
            failing_code = code
            break

    if failing_step:
        report.add_found(f"actions log failing step ({source}): {failing_step[0]}")
    else:
        report.add_not_found(f"actions log failing step ({source})")

    if failing_code is not None:
        report.add_found(f"actions log process exit code ({source}): {failing_code}")
    else:
        report.add_not_found(f"actions log process exit code ({source})")

    error_msgs: list[str] = []
    workflow_errors: list[str] = []
    pytest_failed: list[str] = []
    if failing_step:
        error_msgs = _collect_official_error_messages(failing_step[1])
        workflow_errors = _collect_workflow_errors(failing_step[1])
        pytest_failed = _collect_pytest_failed(failing_step[1])
        _hint_user_printed_error_markers(failing_step[1], report, source)

    if error_msgs:
        report.add_found(f"actions log error marker (first) ({source}): {error_msgs[0][:400]}")
    else:
        report.add_not_found(f"actions log ##[error] markers ({source})")

    if workflow_errors:
        report.add_found(
            f"actions log workflow error (first) ({source}): {workflow_errors[0][:400]}"
        )
    else:
        report.add_not_found(f"actions log ::error:: commands ({source})")

    if pytest_failed:
        report.add_found(f"actions log pytest failed (first) ({source}): {pytest_failed[0][:400]}")
        if len(pytest_failed) > 1:
            report.add_found(
                f"actions log pytest failed (last) ({source}): {pytest_failed[-1][:400]}"
            )
    else:
        report.add_not_found(f"actions log pytest failed lines ({source})")

    if failing_step:
        # Output lives after ##[endgroup] in the step body.
        output_lines: list[str] = []
        past_endgroup = False
        for line in failing_step[1]:
            if GH_GROUP_END.match(line):
                past_endgroup = True
                continue
            if past_endgroup:
                output_lines.append(line)
        embedded = "\n".join(output_lines)
        if embedded.strip():
            from err_o_roadrunner.triage import detect_and_parse_log

            detect_and_parse_log(embedded, report)

    sample = (
        pytest_failed[0]
        if pytest_failed
        else (error_msgs[0] if error_msgs else (workflow_errors[0] if workflow_errors else ""))
    )
    if sample:
        report.add_hint(f"actions log search keywords ({source}): {sample[:400]}")
    elif failing_code is not None and failing_code != 0:
        report.add_hint(
            f"actions log search keywords ({source}): GitHub Actions exit code {failing_code}"
        )
