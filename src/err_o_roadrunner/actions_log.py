"""Parse downloaded GitHub Actions job logs (read-only, stdlib only)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
WORKFLOW_PARAM_FILE = re.compile(r"\bfile=(?P<file>[^,;\s]+)")
WORKFLOW_PARAM_LINE = re.compile(r"\bline=(?P<line>\d+)")
PROCESS_EXIT_RE = re.compile(
    r"Process completed with exit code (?P<code>-?\d+)",
    re.IGNORECASE,
)
PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(\S+)\s+-\s+(.*)$")
FAKE_ERROR_IN_OUTPUT = re.compile(r"##\[error\]")
GH_UNKNOWN_STEP = "UNKNOWN STEP"
CONTINUE_ON_ERROR_RE = re.compile(r"continue-on-error:\s*true\b", re.IGNORECASE)
POST_STEP_NAME_RE = re.compile(r"^Post\s+", re.IGNORECASE)
CANCEL_TIMEOUT_RE = re.compile(
    r"(?:operation was )?cancell?ed|timed out|maximum execution time|timeout expired",
    re.IGNORECASE,
)

MAX_ACTIONS_ERROR_LINES = 40


@dataclass(frozen=True)
class _ParsedLine:
    text: str
    gh_job: str | None = None
    gh_step: str | None = None


@dataclass
class _StepMeta:
    name: str
    body: list[str]
    job: str = ""
    is_post: bool = False
    continue_on_error: bool = False
    exit_code: int | None = None
    gh_errors: list[str] = field(default_factory=list)
    workflow_errors: list[_WorkflowError] = field(default_factory=list)
    pytest_failed: list[str] = field(default_factory=list)
    cancel_or_timeout: bool = False

    def is_failure_evidence(self) -> bool:
        if self.cancel_or_timeout:
            return True
        if self.exit_code is not None and self.exit_code != 0:
            return True
        if self.gh_errors and self.exit_code is None:
            for msg in self.gh_errors:
                if not PROCESS_EXIT_RE.search(msg):
                    return True
        return False


@dataclass(frozen=True)
class _WorkflowError:
    message: str
    file: str = ""
    line: int | None = None


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
    raw_lines = text.split("\n")
    if raw_lines and text.endswith("\n") and raw_lines[-1] == "":
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


def split_gh_run_view_steps(
    records: list[_ParsedLine],
) -> list[tuple[str, str, list[str]]]:
    steps: list[tuple[str, str, list[str]]] = []
    current_key: tuple[str, str] | None = None
    current_job = ""
    current_name = ""
    current_body: list[str] = []

    for rec in records:
        step = (rec.gh_step or "").strip()
        job = (rec.gh_job or "").strip()
        if not step or step == GH_UNKNOWN_STEP:
            if current_key is not None:
                steps.append((current_job, current_name, current_body))
                current_key = None
                current_job = ""
                current_name = ""
                current_body = []
            continue
        key = (job, step)
        if current_key != key:
            if current_key is not None:
                steps.append((current_job, current_name, current_body))
            current_key = key
            current_job = job
            current_name = step
            current_body = [rec.text]
        else:
            current_body.append(rec.text)

    if current_key is not None:
        steps.append((current_job, current_name, current_body))
    return steps


def _display_step_name(job: str, name: str) -> str:
    if job and name:
        return f"{job} / {name}"
    return name or job


def _resolve_steps(
    records: list[_ParsedLine], is_gh_run_view: bool
) -> list[tuple[str, str, list[str]]]:
    line_texts = [rec.text for rec in records]
    run_steps = split_run_steps(line_texts)
    if run_steps:
        return [("", name, body) for name, body in run_steps]
    if is_gh_run_view:
        gh_steps = split_gh_run_view_steps(records)
        if gh_steps:
            return gh_steps
    return []


def _parse_workflow_error(line: str) -> _WorkflowError | None:
    m = WORKFLOW_CMD.match(line)
    if not m or m.group("kind") != "error":
        return None
    params = m.group("params") or ""
    msg = (m.group("msg") or "").strip()
    file_m = WORKFLOW_PARAM_FILE.search(params)
    line_m = WORKFLOW_PARAM_LINE.search(params)
    file_val = file_m.group("file") if file_m else ""
    line_val = int(line_m.group("line")) if line_m else None
    return _WorkflowError(message=msg, file=file_val, line=line_val)


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


def _collect_workflow_errors(body: list[str]) -> list[_WorkflowError]:
    out: list[_WorkflowError] = []
    for line in body:
        wf = _parse_workflow_error(line)
        if wf:
            out.append(wf)
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


def _step_has_continue_on_error(body: list[str]) -> bool:
    return any(CONTINUE_ON_ERROR_RE.search(line) for line in body)


def _analyze_step(job: str, name: str, body: list[str]) -> _StepMeta:
    display = _display_step_name(job, name)
    meta = _StepMeta(
        name=display,
        body=body,
        job=job,
        is_post=bool(POST_STEP_NAME_RE.match(name.strip())),
        continue_on_error=_step_has_continue_on_error(body),
        exit_code=_step_process_exit_code(body),
        gh_errors=_collect_official_error_messages(body),
        workflow_errors=_collect_workflow_errors(body),
        pytest_failed=_collect_pytest_failed(body),
    )
    for msg in meta.gh_errors:
        if CANCEL_TIMEOUT_RE.search(msg) and not PROCESS_EXIT_RE.search(msg):
            meta.cancel_or_timeout = True
            break
    if not meta.cancel_or_timeout:
        for line in body:
            m = GH_ERROR_LINE.match(line)
            if not m:
                continue
            msg = m.group("msg").strip()
            if msg and not PROCESS_EXIT_RE.search(msg):
                if CANCEL_TIMEOUT_RE.search(msg):
                    meta.cancel_or_timeout = True
                    break
    return meta


def _collect_log_wide(
    lines: list[str],
) -> tuple[list[str], list[_WorkflowError], list[str]]:
    gh_errors: list[str] = []
    workflow: list[_WorkflowError] = []
    pytest_failed: list[str] = []
    for line in lines:
        m = GH_ERROR_LINE.match(line)
        if m:
            msg = m.group("msg").strip()
            if msg:
                gh_errors.append(msg)
        wf = _parse_workflow_error(line)
        if wf:
            workflow.append(wf)
        stripped = line.strip()
        pm = PYTEST_FAILED_RE.match(stripped)
        if pm:
            pytest_failed.append(f"FAILED {pm.group(1)} - {pm.group(2)}")
        elif stripped.startswith("FAILED "):
            pytest_failed.append(stripped)
    return gh_errors, workflow, pytest_failed


def _emit_capped_lines(
    report: Report,
    source: str,
    label: str,
    items: list[str],
    cap: int = MAX_ACTIONS_ERROR_LINES,
) -> None:
    if not items:
        report.add_not_found(f"actions log {label} ({source})")
        return
    report.add_found(f"actions log {label} ({source}): {len(items)}")
    shown = items[:cap]
    for i, item in enumerate(shown, start=1):
        report.add_found(f"actions log {label} {i}/{len(items)} ({source}): {item[:400]}")
    omitted = len(items) - len(shown)
    if omitted > 0:
        report.add_found(
            f"actions log {label} omitted ({source}): +{omitted} omitted "
            f"(total {label} {len(items)})"
        )


def _emit_workflow_errors(
    report: Report, source: str, items: list[_WorkflowError]
) -> None:
    if not items:
        report.add_not_found(f"actions log ::error:: commands ({source})")
        return
    report.add_found(f"actions log ::error:: commands ({source}): {len(items)}")
    cap = MAX_ACTIONS_ERROR_LINES
    for i, wf in enumerate(items[:cap], start=1):
        loc = ""
        if wf.file:
            loc = f" file={wf.file}"
            if wf.line is not None:
                loc += f",line={wf.line}"
        report.add_found(
            f"actions log workflow error {i}/{len(items)} ({source}):{loc} {wf.message[:360]}"
        )
    omitted = len(items) - min(len(items), cap)
    if omitted > 0:
        report.add_found(
            f"actions log workflow error omitted ({source}): +{omitted} omitted "
            f"(total ::error {len(items)})"
        )
    first = items[0]
    first_msg = first.message[:400]
    if first.file:
        first_msg = f"{first.file}:{first.line or '?'} {first.message}"[:400]
    report.add_found(f"actions log workflow error (first) ({source}): {first_msg}")


def _hint_user_printed_error_markers(body: list[str], report: Report, source: str) -> None:
    for line in body:
        if GH_ERROR_LINE.match(line) or GH_WARNING_LINE.match(line):
            continue
        if FAKE_ERROR_IN_OUTPUT.search(line) and not line.startswith("##[error]"):
            report.add_hint(
                f"actions log inline error marker (not a workflow line) ({source}): {line.strip()[:200]}"
            )


def _step_embedded_output(body: list[str], skip_failed_lines: bool) -> str:
    output_lines: list[str] = []
    past_endgroup = False
    for line in body:
        if GH_GROUP_END.match(line):
            past_endgroup = True
            continue
        if not past_endgroup:
            continue
        if skip_failed_lines:
            stripped = line.strip()
            if stripped.startswith("FAILED ") or PYTEST_FAILED_RE.match(stripped):
                continue
        output_lines.append(line)
    return "\n".join(output_lines)


def _pick_primary_failure(metas: list[_StepMeta]) -> _StepMeta | None:
    if not metas:
        return None
    for meta in metas:
        if meta.is_failure_evidence() and not meta.continue_on_error:
            return meta
    for meta in metas:
        if meta.is_failure_evidence():
            return meta
    return None


def parse_actions_log(text: str, source: str, report: Report) -> None:
    records, is_gh_run_view = preprocess_actions_log(text)
    lines = [rec.text for rec in records]
    if not any(ln.strip() for ln in lines):
        report.add_not_found(f"actions log content ({source})")
        return

    report.add_found(f"actions log lines ({source}): {len(lines)}")
    if is_gh_run_view:
        report.add_found(f"actions log format ({source}): gh run view")

    raw_steps = _resolve_steps(records, is_gh_run_view)
    if raw_steps:
        report.add_found(f"actions log run steps ({source}): {len(raw_steps)}")
        last_job, last_name, _ = raw_steps[-1]
        report.add_found(
            f"actions log last run step ({source}): {_display_step_name(last_job, last_name)}"
        )
    else:
        report.add_not_found(f"actions log run steps ({source})")

    step_metas = [_analyze_step(job, name, body) for job, name, body in raw_steps]
    failing_metas = [m for m in step_metas if m.is_failure_evidence()]

    if failing_metas:
        report.add_found(f"actions log failing steps ({source}): {len(failing_metas)}")
        for i, meta in enumerate(failing_metas, start=1):
            suffix = ""
            if meta.is_post:
                suffix = " [post step]"
            if meta.continue_on_error:
                suffix += " [continue-on-error]"
            report.add_found(
                f"actions log failing step {i}/{len(failing_metas)} ({source}): {meta.name}{suffix}"
            )
    else:
        report.add_not_found(f"actions log failing steps ({source})")

    primary = _pick_primary_failure(failing_metas)
    if primary:
        report.add_found(f"actions log failing step ({source}): {primary.name}")
    else:
        report.add_not_found(f"actions log failing step ({source})")

    if primary and primary.exit_code is not None:
        report.add_found(f"actions log process exit code ({source}): {primary.exit_code}")
    elif primary and primary.cancel_or_timeout:
        report.add_not_found(f"actions log process exit code ({source})")
        report.add_hint(
            f"actions log runner cancel or timeout ({source}): {primary.gh_errors[0][:300]}"
        )
    else:
        report.add_not_found(f"actions log process exit code ({source})")

    jobs_failing: dict[str, int] = {}
    for meta in failing_metas:
        if meta.job:
            jobs_failing[meta.job] = jobs_failing.get(meta.job, 0) + 1
    if jobs_failing:
        parts = [f"{job}×{count}" for job, count in sorted(jobs_failing.items())]
        report.add_found(f"actions log failing jobs ({source}): {', '.join(parts)}")

    log_gh, log_wf, log_failed = _collect_log_wide(lines)
    _emit_capped_lines(report, source, "##[error] markers", log_gh)
    _emit_workflow_errors(report, source, log_wf)
    _emit_capped_lines(report, source, "pytest failed lines", log_failed)

    if primary:
        _hint_user_printed_error_markers(primary.body, report, source)

    if primary:
        error_msgs = primary.gh_errors
        workflow_errors = primary.workflow_errors
        pytest_failed = primary.pytest_failed
        if error_msgs:
            report.add_found(
                f"actions log error marker (first) ({source}): {error_msgs[0][:400]}"
            )
        else:
            report.add_not_found(f"actions log ##[error] markers ({source})")

        if workflow_errors and not log_wf:
            wf0 = workflow_errors[0]
            wf_msg = wf0.message[:400]
            if wf0.file:
                wf_msg = f"{wf0.file}:{wf0.line or '?'} {wf0.message}"[:400]
            report.add_found(f"actions log workflow error (first) ({source}): {wf_msg}")
        elif not log_wf and not workflow_errors:
            report.add_not_found(f"actions log ::error:: commands ({source})")

        if pytest_failed:
            report.add_found(
                f"actions log pytest failed (first) ({source}): {pytest_failed[0][:400]}"
            )
            if len(pytest_failed) > 1:
                report.add_found(
                    f"actions log pytest failed (last) ({source}): {pytest_failed[-1][:400]}"
                )
        else:
            report.add_not_found(f"actions log pytest failed lines ({source})")

    multi_fail = len(failing_metas) > 1
    for meta in failing_metas:
        embedded = _step_embedded_output(meta.body, skip_failed_lines=True)
        if embedded.strip():
            from err_o_roadrunner.triage import detect_and_parse_log, embedded_failure_signals

            if embedded_failure_signals(embedded):
                step_label = f"step:{meta.name}" if multi_fail else ""
                detect_and_parse_log(embedded, report, label_suffix=step_label)

    if primary and primary.cancel_or_timeout:
        report.add_found(f"actions log cancel or timeout signal ({source}): yes")

    sample = ""
    if log_failed:
        sample = log_failed[0]
    elif log_gh:
        sample = log_gh[0]
    elif log_wf:
        sample = log_wf[0].message
    if sample:
        report.add_hint(f"actions log search keywords ({source}): {sample[:400]}")
    elif primary and primary.exit_code is not None and primary.exit_code != 0:
        report.add_hint(
            f"actions log search keywords ({source}): GitHub Actions exit code {primary.exit_code}"
        )
    elif primary and primary.cancel_or_timeout and primary.gh_errors:
        report.add_hint(
            f"actions log search keywords ({source}): {primary.gh_errors[0][:400]}"
        )
