"""Lightweight command wrapper with error-triggered triage escalation (stdlib only)."""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from project_version import resolve_version

if TYPE_CHECKING:
    from triage import Report

__version__ = resolve_version(package_dir=Path(__file__).resolve().parent)

SCHEMA_VERSION = "sentinel-report/v1"
DEFAULT_RING_BYTES = 64 * 1024
DEFAULT_HOOK_TIMEOUT = 30
DEFAULT_MAX_FIXES = 5
OUTPUT_LINE_MAX = 200
ENV_VALUE_MAX = 200
TIMEOUT_EXIT = 124
TERM_GRACE_SECONDS = 2.0
_USE_PROCESS_GROUP = os.name == "posix"

FORMAT_FULL = "full"
FORMAT_SHORT = "short"
FORMAT_PORCELAIN = "porcelain"
FORMAT_TABLE = "table"
OUTPUT_FORMATS = (FORMAT_FULL, FORMAT_SHORT, FORMAT_PORCELAIN, FORMAT_TABLE)

TB_SHORT = "short"
TB_LINE = "line"
TB_NO = "no"
TB_STYLES = (TB_SHORT, TB_LINE, TB_NO)

CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

FIX_KINDS = (
    "python_import",
    "missing_command",
    "permission",
    "timeout",
    "node_module",
    "assertion",
    "junit",
    "syntax",
    "actions_step",
    "generic",
)

GROUP_BY_KEYS = (
    "error_type",
    "language",
    "phase",
    "signature",
    "location",
    "kind",
)
DEFAULT_GROUP_BY: tuple[str, ...] = ("error_type",)
SORT_COUNT = "count"
SORT_FIRST_SEEN = "first_seen"
SORT_NAME = "name"
GROUP_SORT_OPTIONS = (SORT_COUNT, SORT_FIRST_SEEN, SORT_NAME)

_PATH_SIGNATURE_RE = re.compile(
    r"(?:/[\w.~$-]+)+|(?:[A-Za-z]:\\[\w.~$-\\]+)+|<repo>[\w./-]*"
)
_NUMBER_SIGNATURE_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_UUID_SIGNATURE_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_HEX_ID_SIGNATURE_RE = re.compile(r"\b0x[0-9a-f]+\b", re.I)

_PHASE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"junit collection error|collection errors", re.I), "collect"),
    (re.compile(r"collect only|error during collection", re.I), "collect"),
    (re.compile(r"ModuleNotFoundError|ImportError|cannot import", re.I), "import"),
    (re.compile(r"setup|fixture setup", re.I), "setup"),
    (re.compile(r"teardown", re.I), "teardown"),
    (re.compile(r"junit failure|AssertionError|expect\(locator\)", re.I), "test"),
    (re.compile(r"timed out|exit code: 124", re.I), "timeout"),
)

_ERROR_TYPE_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"python exception type: ModuleNotFoundError", re.I), "ModuleNotFoundError", "python"),
    (re.compile(r"python exception type: ImportError", re.I), "ImportError", "python"),
    (re.compile(r"python exception type: TypeError", re.I), "TypeError", "python"),
    (re.compile(r"python exception type: SyntaxError", re.I), "SyntaxError", "python"),
    (re.compile(r"python exception type: AssertionError", re.I), "AssertionError", "python"),
    (re.compile(r"ValidationError|pydantic.*validation", re.I), "ValidationError", "python"),
    (re.compile(r"sqlite3\.OperationalError|OperationalError.*(?:sqlite|database)", re.I), "sqlite3.OperationalError", "python"),
    (re.compile(r"psycopg(?:2|3)?\.(?:errors\.)?\w+|PsycopgError", re.I), "psycopg.Error", "python"),
    (re.compile(r"SQLAlchemyError|sqlalchemy\.exc\.\w+", re.I), "SQLAlchemyError", "python"),
    (re.compile(r"node error|Cannot find module|ERR_MODULE_NOT_FOUND", re.I), "NodeError", "node"),
    (re.compile(r"junit failure|junit error", re.I), "JUnitFailure", "junit"),
    (re.compile(r"actions log failing|actions log ::error::", re.I), "ActionsStepFailure", "actions"),
    (re.compile(r"exit code: 127|FileNotFoundError", re.I), "CommandNotFound", "shell"),
    (re.compile(r"exit code: 126|PermissionError", re.I), "PermissionDenied", "shell"),
)

_LANGUAGE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"python exception|python traceback", re.I), "python"),
    (re.compile(r"node error|Cannot find module", re.I), "node"),
    (re.compile(r"junit ", re.I), "junit"),
    (re.compile(r"actions log", re.I), "actions"),
    (re.compile(r"exit code: 12[67]", re.I), "shell"),
)

# Exit-mode: child (default) keeps the wrapped exit code; signal uses RoadRunner codes.
EXIT_MODE_CHILD = "child"
EXIT_MODE_SIGNAL = "signal"
EXIT_STOP = 0  # signal mode: investigation not run (stop)
EXIT_GO = 1  # signal mode: investigation path ran (go)
EXIT_USAGE = 2

_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization)"
)
_STDERR_TRACEBACK_MARKER = "Traceback (most recent call last):"


@dataclass
class WatchState:
    """Low-cost capture while the wrapped command runs."""

    started_at: float = field(default_factory=time.monotonic)
    stdout_tail: "RingBuffer" = field(default_factory=lambda: RingBuffer(DEFAULT_RING_BYTES))
    stderr_tail: "RingBuffer" = field(default_factory=lambda: RingBuffer(DEFAULT_RING_BYTES))
    command_argv: list[str] = field(default_factory=list)
    cwd: str | None = None


@dataclass
class RunOutcome:
    exit_code: int | None
    effective_exit_code: int | None = None
    timed_out: bool = False
    signal_name: str | None = None
    termination_signal: str | None = None
    junit_failure: bool = False
    exception_type: str | None = None
    exception_message: str | None = None


class RingBuffer:
    """Fixed-size tail buffer for streamed text (UTF-8 safe enough for logs)."""

    def __init__(self, max_bytes: int) -> None:
        self._max = max(1024, max_bytes)
        self._chunks: collections.deque[tuple[bytes, str]] = collections.deque()
        self._size = 0

    def append(self, data: bytes, stream: str) -> None:
        if not data:
            return
        self._chunks.append((data, stream))
        self._size += len(data)
        while self._size > self._max and self._chunks:
            old, _ = self._chunks.popleft()
            self._size -= len(old)

    def text(self) -> str:
        raw = b"".join(chunk for chunk, _ in self._chunks)
        return raw.decode("utf-8", errors="replace")


def shell_join(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def stderr_has_python_traceback(text: str) -> bool:
    return _STDERR_TRACEBACK_MARKER in text


def normalized_child_exit(outcome: RunOutcome) -> int:
    if outcome.timed_out:
        return TIMEOUT_EXIT
    if outcome.effective_exit_code is not None:
        return outcome.effective_exit_code
    if outcome.exit_code is not None:
        return outcome.exit_code
    return 1


def _call_triage(**kwargs: Any) -> Report:
    from triage import triage as triage_fn

    return triage_fn(**kwargs)


def _redact_text(text: str) -> str:
    from junit_redact import redact_text

    return redact_text(text)


def summarize_environment() -> dict[str, Any]:
    names = sorted(os.environ)
    redacted_values: dict[str, str] = {}
    for name in names:
        if _SENSITIVE_ENV_RE.search(name):
            redacted_values[name] = "<redacted>"
            continue
        raw = os.environ.get(name, "")
        if len(raw) > ENV_VALUE_MAX:
            raw = raw[: ENV_VALUE_MAX - 1] + "…"
        redacted_values[name] = _redact_text(raw)
    return {
        "variable_count": len(names),
        "variable_names": names[:40],
        "variable_names_truncated": len(names) > 40,
        "values_redacted": redacted_values,
    }


def junit_has_failures(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            return True
    return False


def _stream_reader(
    pipe: Any,
    label: str,
    ring: RingBuffer,
    forward: Callable[[bytes], None],
    done: threading.Event,
) -> None:
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            ring.append(chunk, label)
            forward(chunk)
    finally:
        done.set()


def _terminate_process_group(
    pgid: int,
    proc: subprocess.Popen[bytes],
    *,
    grace: float,
) -> str:
    """Send TERM to the group, then KILL after grace. Returns last signal name."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "SIGTERM"
    try:
        proc.wait(timeout=grace)
        return "SIGTERM"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return "SIGKILL"


def run_wrapped_command(
    argv: Sequence[str],
    *,
    cwd: str | None,
    timeout: float | None,
    env: dict[str, str] | None,
    ring_bytes: int = DEFAULT_RING_BYTES,
) -> tuple[WatchState, RunOutcome]:
    state = WatchState(
        command_argv=list(argv),
        cwd=cwd,
        stdout_tail=RingBuffer(ring_bytes),
        stderr_tail=RingBuffer(ring_bytes),
    )
    if not argv:
        return state, RunOutcome(
            exit_code=EXIT_USAGE,
            effective_exit_code=EXIT_USAGE,
            exception_type="ValueError",
            exception_message="empty command",
        )

    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }
    if _USE_PROCESS_GROUP:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(list(argv), **popen_kwargs)
    except FileNotFoundError as exc:
        return state, RunOutcome(
            exit_code=127,
            effective_exit_code=127,
            exception_type="FileNotFoundError",
            exception_message=str(exc),
        )
    except PermissionError as exc:
        return state, RunOutcome(
            exit_code=126,
            effective_exit_code=126,
            exception_type="PermissionError",
            exception_message=str(exc),
        )

    pgid = proc.pid if _USE_PROCESS_GROUP else None
    forwarded_handlers: dict[int, Any] = {}

    def _forward_signal(signum: int, _frame: Any) -> None:
        if pgid is not None:
            try:
                os.killpg(pgid, signum)
            except ProcessLookupError:
                pass
        else:
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                pass

    if _USE_PROCESS_GROUP and pgid is not None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            forwarded_handlers[sig] = signal.signal(sig, _forward_signal)

    out_done = threading.Event()
    err_done = threading.Event()

    def forward_out(data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def forward_err(data: bytes) -> None:
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()

    threads = [
        threading.Thread(
            target=_stream_reader,
            args=(proc.stdout, "stdout", state.stdout_tail, forward_out, out_done),
            daemon=True,
        ),
        threading.Thread(
            target=_stream_reader,
            args=(proc.stderr, "stderr", state.stderr_tail, forward_err, err_done),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    termination_signal: str | None = None
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if pgid is not None:
            termination_signal = _terminate_process_group(
                pgid, proc, grace=TERM_GRACE_SECONDS
            )
            proc.wait()
        else:
            termination_signal = "SIGKILL"
            proc.kill()
            proc.wait()
    finally:
        for sig, handler in forwarded_handlers.items():
            signal.signal(sig, handler)
        out_done.wait(timeout=1)
        err_done.wait(timeout=1)
        for thread in threads:
            thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

    code = proc.returncode
    if timed_out:
        return state, RunOutcome(
            exit_code=code,
            effective_exit_code=TIMEOUT_EXIT,
            timed_out=True,
            termination_signal=termination_signal,
        )
    if code is not None and code < 0:
        sig = signal.Signals(-code).name
        return state, RunOutcome(exit_code=code, effective_exit_code=code, signal_name=sig)
    return state, RunOutcome(exit_code=code, effective_exit_code=code)


def report_to_structured(report: Report) -> dict[str, list[str]]:
    return {
        "found": list(report.found),
        "not_found": list(report.not_found),
        "hints": list(report.hints),
    }


def _cap_output_line(text: str, *, preserve_tabs: bool = False) -> str:
    cleaned = _redact_text(text.replace("\n", " "))
    if not preserve_tabs:
        cleaned = cleaned.replace("\t", " ")
    if len(cleaned) <= OUTPUT_LINE_MAX:
        return cleaned
    return cleaned[: OUTPUT_LINE_MAX - 1] + "…"


@dataclass(frozen=True)
class _FixRule:
    kind: str
    found_re: re.Pattern[str]
    hint_re: re.Pattern[str] | None
    confidence_found: str
    suggestion: str
    loc_re: re.Pattern[str] | None = None


_FIX_RULES: tuple[_FixRule, ...] = (
    _FixRule(
        "python_import",
        re.compile(r"python exception type: ModuleNotFoundError", re.I),
        re.compile(r"ModuleNotFoundError|No module named", re.I),
        "high",
        "Add the missing dependency to your environment (pip install / lockfile) and re-run.",
        re.compile(r"No module named ['\"](?P<mod>[^'\"]+)"),
    ),
    _FixRule(
        "python_import",
        re.compile(r"python exception type: ImportError", re.I),
        re.compile(r"ImportError|cannot import name", re.I),
        "medium",
        "Resolve the import path or install the package providing the symbol.",
        None,
    ),
    _FixRule(
        "missing_command",
        re.compile(r"exit code: 127"),
        re.compile(r"FileNotFoundError|command not found|No such file", re.I),
        "high",
        "Install the missing executable or fix PATH so the command is discoverable.",
        None,
    ),
    _FixRule(
        "permission",
        re.compile(r"exit code: 126"),
        re.compile(r"PermissionError|exec format error", re.I),
        "high",
        "chmod +x the script or invoke it via an interpreter (e.g. sh script.sh).",
        None,
    ),
    _FixRule(
        "timeout",
        re.compile(r"exit code: 124"),
        re.compile(r"timed out|timeout", re.I),
        "high",
        "Re-run with a higher --timeout or profile slow steps.",
        None,
    ),
    _FixRule(
        "node_module",
        re.compile(r"Cannot find module", re.I),
        re.compile(r"ERR_MODULE_NOT_FOUND|Cannot find module", re.I),
        "high",
        "Run npm ci (or pnpm/yarn install) to restore node_modules from the lockfile.",
        re.compile(r"Cannot find module ['\"](?P<mod>[^'\"]+)"),
    ),
    _FixRule(
        "assertion",
        re.compile(r"python exception type: AssertionError", re.I),
        re.compile(r"AssertionError|assert .* failed", re.I),
        "medium",
        "Open the failing assertion site and compare expected vs actual values.",
        re.compile(r"(?P<loc>[^:]+):(?P<line>\d+)"),
    ),
    _FixRule(
        "junit",
        re.compile(r"junit failure \(first\)", re.I),
        re.compile(r"junit failure|expect\(locator\)", re.I),
        "high",
        "Open the first failing testcase in the JUnit XML and read the message/stack.",
        re.compile(
            r"(?P<loc>[\w./-]+\.(?:py|spec\.js|spec\.ts|test\.[jt]s))(?::(?P<line>\d+))?",
            re.I,
        ),
    ),
    _FixRule(
        "syntax",
        re.compile(r"python exception type: SyntaxError", re.I),
        re.compile(r"SyntaxError|Unexpected token", re.I),
        "high",
        "Fix the syntax error at the reported file and line.",
        re.compile(r"file=(?P<loc>[^,]+),line=(?P<line>\d+)|(?P<loc2>[^:]+):(?P<line2>\d+)"),
    ),
    _FixRule(
        "actions_step",
        re.compile(r"actions log failing step", re.I),
        re.compile(r"actions log ::error::", re.I),
        "medium",
        "Download the full GitHub Actions log for the failing step and reproduce locally.",
        None,
    ),
    _FixRule(
        "generic",
        re.compile(r"exit code: (?!0\b)\d+"),
        None,
        "low",
        "Re-run reproduce_command with verbose logging and inspect stderr.",
        None,
    ),
)


def _location_from_evidence(rule: _FixRule, evidence: Sequence[str]) -> dict[str, str]:
    blob = " ".join(evidence)
    if rule.loc_re:
        match = rule.loc_re.search(blob)
        if match:
            groups = match.groupdict()
            loc = groups.get("loc") or groups.get("loc2") or ""
            line = groups.get("line") or groups.get("line2") or ""
            out: dict[str, str] = {}
            if loc:
                out["loc"] = loc
            if line:
                out["line"] = line
            if out:
                return out
    for line in evidence:
        for pattern in (
            re.compile(r"frame \(first\): (?P<loc>[^:]+):(?P<line>\d+)"),
            re.compile(r"file=(?P<loc>[^,]+),line=(?P<line>\d+)"),
            re.compile(r"junit testcase classname \(first\).*?: (?P<loc>[^\s]+)"),
            re.compile(r"(?P<loc>[\w./-]+\.spec\.[jt]s):(?P<line>\d+)"),
        ):
            match = pattern.search(line)
            if match:
                return {k: v for k, v in match.groupdict().items() if v}
    return {}


def build_fix_candidates(
    structured: dict[str, list[str]],
    *,
    reproduce_command: str,
    max_fixes: int = DEFAULT_MAX_FIXES,
) -> list[dict[str, Any]]:
    found = structured.get("found") or []
    hints = structured.get("hints") or []
    raw: list[tuple[_FixRule, str, Sequence[str]]] = []

    for rule in _FIX_RULES:
        ev_found = [ln for ln in found if rule.found_re.search(ln)]
        if ev_found:
            raw.append((rule, rule.confidence_found, ev_found))
            continue
        if rule.hint_re:
            ev_hint = [ln for ln in hints if rule.hint_re.search(ln)]
            if ev_hint:
                raw.append((rule, "low", ev_hint))

    deduped: dict[tuple[str, str], tuple[_FixRule, str, list[str]]] = {}
    for rule, conf, evidence in raw:
        loc = _location_from_evidence(rule, evidence)
        loc_key = loc.get("loc", "")
        key = (rule.kind, loc_key)
        prev = deduped.get(key)
        merged = list(evidence)
        if prev:
            merged = list(dict.fromkeys([*prev[2], *evidence]))
            conf = prev[1] if CONFIDENCE_RANK[prev[1]] <= CONFIDENCE_RANK[conf] else conf
            rule = prev[0]
        deduped[key] = (rule, conf, merged)

    ordered = sorted(
        deduped.values(),
        key=lambda item: (CONFIDENCE_RANK[item[1]], -len(item[2]), item[0].kind),
    )

    candidates: list[dict[str, Any]] = []
    for idx, (rule, confidence, evidence) in enumerate(ordered[: max(0, max_fixes)], start=1):
        location = _location_from_evidence(rule, evidence)
        candidates.append(
            {
                "id": f"F{idx}",
                "kind": rule.kind,
                "confidence": confidence,
                "evidence": list(evidence),
                "location": location,
                "suggestion": rule.suggestion,
                "verify_command": reproduce_command,
            }
        )
    return candidates


def parse_group_by(value: str) -> tuple[str, ...]:
    keys = [part.strip() for part in value.split(",") if part.strip()]
    if not keys:
        return DEFAULT_GROUP_BY
    invalid = [k for k in keys if k not in GROUP_BY_KEYS]
    if invalid:
        raise ValueError(f"invalid --group-by keys: {', '.join(invalid)}")
    return tuple(keys)


def normalize_signature(message: str) -> str:
    text = message.strip()
    text = _PATH_SIGNATURE_RE.sub("<path>", text)
    text = _UUID_SIGNATURE_RE.sub("<id>", text)
    text = _HEX_ID_SIGNATURE_RE.sub("<id>", text)
    text = _NUMBER_SIGNATURE_RE.sub("<n>", text)
    text = " ".join(text.split())
    if len(text) > 160:
        text = text[:159] + "…"
    return text or "generic"


def _classify_error_type_language(text: str) -> tuple[str, str]:
    for pattern, etype, lang in _ERROR_TYPE_RULES:
        if pattern.search(text):
            return etype, lang
    match = re.search(r"python exception type: (?P<etype>[A-Za-z_][\w.]*)", text, re.I)
    if match:
        return match.group("etype"), "python"
    for pattern, lang in _LANGUAGE_HINTS:
        if pattern.search(text):
            return "generic", lang
    return "generic", "generic"


def _classify_phase(text: str, outcome: RunOutcome) -> str:
    if outcome.timed_out:
        return "timeout"
    for pattern, phase in _PHASE_RULES:
        if pattern.search(text):
            return phase
    if outcome.junit_failure and "junit" in text.lower():
        return "test"
    return "runtime"


def _classify_kind(text: str, error_type: str) -> str:
    for rule in _FIX_RULES:
        if rule.found_re.search(text):
            return rule.kind
    if error_type in ("ModuleNotFoundError", "ImportError"):
        return "python_import"
    if error_type == "ValidationError":
        return "generic"
    if error_type in ("sqlite3.OperationalError", "psycopg.Error", "SQLAlchemyError"):
        return "generic"
    return "generic"


def _parse_location_from_line(line: str) -> dict[str, str]:
    for pattern in (
        re.compile(r"frame \(first\): (?P<loc>[^:]+):(?P<line>\d+)"),
        re.compile(r"file=(?P<loc>[^,]+),line=(?P<line>\d+)"),
        re.compile(r"(?P<loc>[\w./-]+\.spec\.[jt]s):(?P<line>\d+)"),
        re.compile(r"junit testcase classname \(first\).*?: (?P<loc>[^\s]+)"),
    ):
        match = pattern.search(line)
        if match:
            return {k: v for k, v in match.groupdict().items() if v}
    return {}


def _location_key(location: dict[str, str]) -> str:
    if not location:
        return ""
    loc = location.get("loc", "")
    line = location.get("line", "")
    return f"{loc}:{line}" if line else loc


def _message_from_found_lines(found: Sequence[str], index: int) -> str:
    line = found[index]
    msg_match = re.search(r"python exception message: (.+)", line)
    if msg_match:
        return msg_match.group(1)
    if "python exception type:" in line:
        for look in found[index + 1 : index + 4]:
            follow = re.search(r"python exception message: (.+)", look)
            if follow:
                return follow.group(1)
    for pattern in (
        re.compile(r"junit failure \(first\).*?: (.+)", re.I),
        re.compile(r"node error line: (.+)", re.I),
        re.compile(r"actions log workflow error \(first\).*?: (.+)", re.I),
    ):
        match = pattern.search(line)
        if match:
            return match.group(1)
    return line


def _is_error_seed_line(line: str) -> bool:
    seeds = (
        "python exception type:",
        "junit failure (first)",
        "junit error (first)",
        "node error line:",
        "actions log failing step",
        "actions log workflow error (first)",
        "exit code: 127",
        "exit code: 126",
        "exit code: 124",
    )
    return any(token in line for token in seeds)


def extract_normalized_errors(
    structured: dict[str, list[str]],
    outcome: RunOutcome,
) -> list[dict[str, Any]]:
    found = structured.get("found") or []
    errors: list[dict[str, Any]] = []
    for idx, line in enumerate(found):
        if not _is_error_seed_line(line):
            continue
        message = _message_from_found_lines(found, idx)
        error_type, language = _classify_error_type_language(line + " " + message)
        phase = _classify_phase(line + " " + message, outcome)
        location = _parse_location_from_line(line)
        if not location and idx + 1 < len(found):
            location = _parse_location_from_line(found[idx + 1])
        kind = _classify_kind(line, error_type)
        errors.append(
            {
                "error_type": error_type,
                "language": language,
                "phase": phase,
                "signature": normalize_signature(message),
                "location": location,
                "kind": kind,
                "first_seen": idx,
                "message": message[:500],
                "source_line": line,
            }
        )

    if not errors:
        blob = "\n".join(found)
        error_type, language = _classify_error_type_language(blob)
        phase = _classify_phase(blob, outcome)
        if outcome.exception_type:
            error_type = outcome.exception_type
            language = "python"
        errors.append(
            {
                "error_type": error_type,
                "language": language,
                "phase": phase,
                "signature": normalize_signature(blob[:300] or "failure"),
                "location": _parse_location_from_line(blob),
                "kind": _classify_kind(blob, error_type),
                "first_seen": 0,
                "message": blob[:300] or "failure",
                "source_line": found[0] if found else "",
            }
        )
    return errors


def _bucket_name(key: dict[str, str], group_by: Sequence[str]) -> str:
    parts = [f"{field}={key.get(field, 'generic')}" for field in group_by]
    return ", ".join(parts)


def build_groups(
    errors: Sequence[dict[str, Any]],
    *,
    group_by: Sequence[str],
    sort: str,
) -> dict[str, Any]:
    buckets_map: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    for err in errors:
        key_dict: dict[str, str] = {}
        for field in group_by:
            if field == "location":
                key_dict[field] = _location_key(err.get("location") or {})
            else:
                key_dict[field] = str(err.get(field) or "generic")
        key_tuple = tuple(sorted(key_dict.items()))
        buckets_map.setdefault(key_tuple, []).append(err)

    buckets: list[dict[str, Any]] = []
    for key_tuple, members in buckets_map.items():
        key_dict = dict(key_tuple)
        buckets.append(
            {
                "key": key_dict,
                "count": len(members),
                "first_seen": min(m["first_seen"] for m in members),
                "name": _bucket_name(key_dict, group_by),
                "errors": list(members),
            }
        )

    if sort == SORT_FIRST_SEEN:
        buckets.sort(key=lambda b: (b["first_seen"], b["name"]))
    elif sort == SORT_NAME:
        buckets.sort(key=lambda b: b["name"])
    else:
        buckets.sort(key=lambda b: (-b["count"], b["first_seen"], b["name"]))

    return {
        "group_by": list(group_by),
        "sort": sort,
        "buckets": buckets,
        "error_count": len(errors),
    }


def _format_groups_short(groups: dict[str, Any], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for bucket in (groups.get("buckets") or [])[:limit]:
        lines.append(
            _cap_output_line(
                f"G {bucket['count']}× {bucket['name']}",
            )
        )
    return lines


def _short_outcome_code(outcome: RunOutcome) -> str | None:
    if outcome.timed_out:
        return "T"
    if outcome.signal_name:
        return "S"
    code = normalized_child_exit(outcome)
    if code in (126, 127):
        return "X"
    if outcome.exception_type and outcome.exception_type not in (None, "ValueError"):
        return "E"
    if outcome.junit_failure:
        return "F"
    if code not in (None, 0):
        return "F"
    return None


def _classify_triage_short(
    structured: dict[str, list[str]],
    outcome: RunOutcome,
) -> dict[str, int]:
    counts = {"F": 0, "E": 0, "T": 0, "S": 0, "X": 0, "H": 0, "?": 0}
    primary = _short_outcome_code(outcome)
    if primary:
        counts[primary] = 1
    found_joined = "\n".join(structured.get("found") or [])
    if "AssertionError" in found_joined and counts["F"] == 0 and counts["E"] == 0:
        counts["F"] = 1
    if any("python exception type" in ln for ln in structured.get("found") or []):
        if counts["F"] == 0 and counts["E"] == 0:
            counts["E"] = 1
    counts["H"] = len(structured.get("hints") or [])
    counts["?"] = len(structured.get("not_found") or [])
    return counts


def render_stdout_format(
    doc: dict[str, Any],
    *,
    fmt: str,
    tb: str,
    verbose: bool,
    outcome: RunOutcome | None = None,
) -> str:
    if fmt == FORMAT_FULL:
        return _render_full(doc, tb=tb)
    if fmt == FORMAT_SHORT:
        return _render_short(doc, tb=tb, verbose=verbose, outcome=outcome)
    if fmt == FORMAT_PORCELAIN:
        return _render_porcelain(doc)
    if fmt == FORMAT_TABLE:
        return _render_table(doc)
    return ""


def render_stop_stdout(
    *,
    fmt: str,
    exit_code: int,
    elapsed_seconds: float,
) -> str:
    if fmt == FORMAT_PORCELAIN:
        return ""
    if fmt == FORMAT_SHORT:
        return _cap_output_line(
            f"sentinel: STOP exit={exit_code} {elapsed_seconds:.2f}s",
        )
    return ""


def _render_full(doc: dict[str, Any], *, tb: str) -> str:
    lines: list[str] = []
    tri = doc.get("triage") or {}
    for item in tri.get("found", []):
        lines.append(_cap_output_line(f"FOUND: {item}"))
    for item in tri.get("not_found", []):
        lines.append(_cap_output_line(f"NOT FOUND: {item}"))
    for item in tri.get("hints", []):
        lines.append(_cap_output_line(f"HINT: {item}"))
    if tb != TB_NO:
        for cand in doc.get("fix_candidates") or []:
            lines.append(_cap_output_line(f"FIX: [{cand['id']}] {cand['suggestion']}"))
    return "\n".join(lines) + ("\n" if lines else "")


def _render_short(
    doc: dict[str, Any],
    *,
    tb: str,
    verbose: bool,
    outcome: RunOutcome | None,
) -> str:
    structured = doc.get("triage") or {}
    outcome = outcome or RunOutcome(
        exit_code=doc.get("outcome", {}).get("raw_exit_code"),
        effective_exit_code=doc.get("outcome", {}).get("exit_code"),
        timed_out=doc.get("outcome", {}).get("timed_out", False),
        signal_name=doc.get("outcome", {}).get("signal"),
        junit_failure=doc.get("outcome", {}).get("junit_failure", False),
    )
    counts = _classify_triage_short(structured, outcome)
    lines: list[str] = []
    code = _short_outcome_code(outcome) or "F"
    cmd = doc.get("command", {}).get("reproduce_command", "")
    lines.append(_cap_output_line(f"{code} {cmd}"))

    if tb != TB_NO:
        for item in (structured.get("found") or [])[:3 if tb == TB_LINE else 8]:
            prefix = "  "
            lines.append(_cap_output_line(f"{prefix}{item}"))

    fixes = doc.get("fix_candidates") or []
    if fixes:
        top = fixes[0]
        lines.append(_cap_output_line(f"fix: [{top['id']}] {top['suggestion']}"))

    groups = doc.get("groups")
    if groups:
        lines.extend(_format_groups_short(groups))

    if verbose:
        for item in structured.get("not_found") or []:
            lines.append(_cap_output_line(f"? NOT FOUND: {item}"))

    elapsed = doc.get("timing", {}).get("elapsed_seconds", 0.0)
    parts: list[str] = []
    if counts["F"]:
        parts.append(f"{counts['F']} failed")
    if counts["E"]:
        parts.append(f"{counts['E']} error")
    if counts["T"]:
        parts.append(f"{counts['T']} timeout")
    if counts["S"]:
        parts.append(f"{counts['S']} signal")
    if counts["X"]:
        parts.append(f"{counts['X']} exit126/127")
    if counts["H"]:
        parts.append(f"{counts['H']} hint")
    if verbose and counts["?"]:
        parts.append(f"{counts['?']} not found")
    nfix = len(fixes)
    if nfix:
        parts.append(f"{nfix} fixes")
    summary = ", ".join(parts) if parts else "0 items"
    lines.append(_cap_output_line(f"== {summary} in {elapsed:.2f}s =="))
    return "\n".join(lines) + "\n"


def _render_porcelain(doc: dict[str, Any]) -> str:
    lines: list[str] = []
    header = "\t".join(
        [
            f"# {SCHEMA_VERSION}",
            doc.get("mode", "escalated"),
            doc.get("command", {}).get("reproduce_command", ""),
            str(doc.get("outcome", {}).get("exit_code", "")),
        ]
    )
    lines.append(_cap_output_line(header, preserve_tabs=True))
    tri = doc.get("triage") or {}
    for item in tri.get("found", []):
        lines.append(_cap_output_line("\t".join(["F", "found", item]), preserve_tabs=True))
    for item in tri.get("hints", []):
        lines.append(_cap_output_line("\t".join(["H", "hint", item]), preserve_tabs=True))
    for item in tri.get("not_found", []):
        lines.append(_cap_output_line("\t".join(["?", "not_found", item]), preserve_tabs=True))
    for cand in doc.get("fix_candidates") or []:
        loc = cand.get("location") or {}
        loc_s = loc.get("loc", "")
        line_s = loc.get("line", "")
        lines.append(
            _cap_output_line(
                "\t".join(
                    [
                        "C",
                        cand["id"],
                        cand["kind"],
                        cand["confidence"],
                        loc_s,
                        line_s,
                        cand["suggestion"],
                        cand.get("verify_command", ""),
                    ]
                ),
                preserve_tabs=True,
            )
        )
    elapsed = doc.get("timing", {}).get("elapsed_seconds", 0.0)
    nfix = len(doc.get("fix_candidates") or [])
    lines.append(
        _cap_output_line(
            f"# totals\tfound={len(tri.get('found', []))}\thints={len(tri.get('hints', []))}"
            f"\tnot_found={len(tri.get('not_found', []))}\tfixes={nfix}\telapsed={elapsed:.3f}",
            preserve_tabs=True,
        )
    )
    return "\n".join(lines) + "\n"


def _render_table(doc: dict[str, Any]) -> str:
    groups = doc.get("groups") or {}
    buckets = groups.get("buckets") or []
    if buckets:
        width = 28
        lines = [
            f"{'Group':<{width}} {'Count':>6} {'First':>6}",
        ]
        for bucket in buckets:
            lines.append(
                f"{bucket['name'][:width]:<{width}} {bucket['count']:>6} {bucket['first_seen']:>6}"
            )
        total = groups.get("error_count", sum(b["count"] for b in buckets))
        lines.append(f"{'TOTAL':<{width}} {total:>6} {'-':>6}")
        return "\n".join(_cap_output_line(ln) for ln in lines) + "\n"

    tri = doc.get("triage") or {}
    n_found = len(tri.get("found", []))
    n_hint = len(tri.get("hints", []))
    n_nf = len(tri.get("not_found", []))
    n_fix = len(doc.get("fix_candidates") or [])
    width = 14
    lines = [
        f"{'Category':<{width}} {'Found':>6} {'Hint':>6} {'NotFound':>8}",
        f"{'triage':<{width}} {n_found:>6} {n_hint:>6} {n_nf:>8}",
        f"{'fix_candidates':<{width}} {n_fix:>6} {'-':>6} {'-':>8}",
        f"{'TOTAL':<{width}} {n_found + n_fix:>6} {n_hint:>6} {n_nf:>8}",
    ]
    return "\n".join(_cap_output_line(ln) for ln in lines) + "\n"


def _extract_locations(found: Sequence[str]) -> list[dict[str, str]]:
    locations: list[dict[str, str]] = []
    patterns = [
        re.compile(r"frame \(first\): (?P<loc>[^:]+):(?P<line>\d+)"),
        re.compile(r"file=(?P<loc>[^,]+),line=(?P<line>\d+)"),
        re.compile(r"junit testcase classname \(first\): (?P<loc>[^\s]+)"),
    ]
    for line in found:
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                entry = {k: v for k, v in match.groupdict().items() if v}
                if entry:
                    locations.append(entry)
                break
    return locations


def _next_investigation_steps(report: Report, outcome: RunOutcome) -> list[str]:
    steps: list[str] = []
    if outcome.timed_out:
        steps.append("Re-run with a higher timeout or profile slow steps.")
    if outcome.signal_name:
        steps.append(f"Inspect why the process received {outcome.signal_name}.")
    if outcome.junit_failure:
        steps.append("Open the JUnit XML and read the first failure or error testcase.")
    if any("actions log" in line for line in report.found):
        steps.append("Download the full GitHub Actions job log for the failing step.")
    if any("python exception" in line for line in report.found):
        steps.append("Reproduce locally with the same Python version and lock files.")
    if any("node " in line for line in report.found):
        steps.append("Reproduce with the same Node version; check package-lock or lockfile.")
    if report.hints:
        steps.append("Use HINT web search keywords before deeper code archaeology.")
    if not steps:
        steps.append("Re-run the reproduce_command with verbose logging enabled.")
    return steps


def run_diagnostic_hooks(
    hooks: Sequence[str],
    *,
    timeout: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for hook in hooks:
        entry: dict[str, Any] = {"command": hook, "exit_code": None, "stdout": "", "stderr": ""}
        try:
            completed = subprocess.run(
                hook,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            entry["exit_code"] = completed.returncode
            entry["stdout"] = _redact_text((completed.stdout or "")[:8000])
            entry["stderr"] = _redact_text((completed.stderr or "")[:8000])
        except subprocess.TimeoutExpired:
            entry["exit_code"] = None
            entry["stderr"] = f"hook timed out after {timeout}s"
        except OSError as exc:
            entry["stderr"] = _redact_text(str(exc))
        results.append(entry)
    return results


def build_escalation_report(
    state: WatchState,
    outcome: RunOutcome,
    *,
    project_root: Path | None,
    junit_paths: Sequence[str],
    actions_log_path: str | None,
    hooks: Sequence[str],
    hook_timeout: float,
    max_fixes: int = DEFAULT_MAX_FIXES,
    group_by: Sequence[str] = DEFAULT_GROUP_BY,
    sort: str = SORT_COUNT,
) -> dict[str, Any]:
    elapsed = time.monotonic() - state.started_at
    stdout_text = state.stdout_tail.text()
    stderr_text = state.stderr_tail.text()
    log_text = stdout_text if stdout_text.strip() else None
    if stderr_text.strip() and not log_text:
        log_text = None

    actions_log_text: str | None = None
    if actions_log_path:
        try:
            actions_log_text = Path(actions_log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            actions_log_text = None

    exit_code = normalized_child_exit(outcome)
    if outcome.junit_failure and exit_code == 0:
        exit_code = 1

    report = _call_triage(
        exit_code=exit_code,
        stderr_text=stderr_text if stderr_text.strip() else None,
        log_text=log_text,
        actions_log_text=actions_log_text,
        junit_paths=junit_paths,
        project_root=project_root,
    )

    reasons: list[str] = []
    if outcome.timed_out:
        reasons.append("timeout")
    elif outcome.exit_code not in (None, 0):
        reasons.append("non_zero_exit")
    if outcome.signal_name:
        reasons.append(f"signal:{outcome.signal_name}")
    if outcome.junit_failure:
        reasons.append("junit_failure")
    if outcome.exception_type:
        reasons.append(f"wrapper:{outcome.exception_type}")
    if stderr_has_python_traceback(stderr_text):
        reasons.append("stderr_traceback")

    structured = report_to_structured(report)
    reported_exit = normalized_child_exit(outcome)
    reproduce = shell_join(state.command_argv)
    fix_candidates = build_fix_candidates(
        structured,
        reproduce_command=reproduce,
        max_fixes=max_fixes,
    )
    normalized_errors = extract_normalized_errors(structured, outcome)
    groups = build_groups(
        normalized_errors,
        group_by=group_by,
        sort=sort,
    )
    doc: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "mode": "escalated",
        "escalation_reasons": reasons,
        "command": {
            "argv": state.command_argv,
            "reproduce_command": shell_join(state.command_argv),
            "cwd": state.cwd,
        },
        "timing": {
            "elapsed_seconds": round(elapsed, 3),
            "started_monotonic": state.started_at,
        },
        "outcome": {
            "exit_code": reported_exit,
            "raw_exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "signal": outcome.signal_name,
            "termination_signal": outcome.termination_signal,
            "junit_failure": outcome.junit_failure,
        },
        "capture": {
            "stdout_tail": _redact_text(stdout_text),
            "stderr_tail": _redact_text(stderr_text),
        },
        "environment": summarize_environment(),
        "triage": structured,
        "locations": _extract_locations(structured["found"]),
        "next_investigation_steps": _next_investigation_steps(report, outcome),
        "fix_candidates": fix_candidates,
        "groups": groups,
        "human_summary": "",
    }
    doc["human_summary"] = _human_summary(
        structured, reasons, state, outcome, fix_candidates=fix_candidates
    )
    if hooks:
        doc["diagnostic_hooks"] = run_diagnostic_hooks(hooks, timeout=hook_timeout)
    return doc


def _human_summary(
    structured: dict[str, list[str]],
    reasons: Sequence[str],
    state: WatchState,
    outcome: RunOutcome,
    *,
    fix_candidates: Sequence[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "## Err-o-RoadRunner sentinel (escalated)",
        "",
        f"- **Reasons:** {', '.join(reasons) or 'failure'}",
        f"- **Command:** `{shell_join(state.command_argv)}`",
        f"- **Exit code:** {normalized_child_exit(outcome)}",
    ]
    if outcome.timed_out:
        lines.append("- **Timed out:** yes")
    if outcome.signal_name:
        lines.append(f"- **Signal:** {outcome.signal_name}")
    if outcome.termination_signal:
        lines.append(f"- **Termination signal:** {outcome.termination_signal}")
    hints = structured.get("hints") or []
    if hints:
        lines.append("")
        lines.append("### Hints")
        for hint in hints[:5]:
            lines.append(f"- {hint}")
    found = structured.get("found") or []
    if found:
        lines.append("")
        lines.append("### Key findings")
        for item in found[:12]:
            lines.append(f"- {item}")
        if len(found) > 12:
            lines.append(f"- … {len(found) - 12} more FOUND lines in JSON")
    fixes = list(fix_candidates or [])
    if fixes:
        lines.append("")
        lines.append("### Fix candidates")
        for cand in fixes[:8]:
            loc = cand.get("location") or {}
            loc_bit = ""
            if loc.get("loc"):
                loc_bit = f" (`{loc['loc']}`"
                if loc.get("line"):
                    loc_bit += f":{loc['line']}"
                loc_bit += ")"
            lines.append(
                f"- **{cand['id']}** ({cand['kind']}, {cand['confidence']}){loc_bit}: "
                f"{cand['suggestion']}"
            )
    return "\n".join(lines)


def is_stop_signal(
    outcome: RunOutcome,
    *,
    junit_failure: bool,
    stderr_text: str,
    go_on_stderr_traceback: bool,
) -> bool:
    """Early-stop path: wrapped exit 0 and no go triggers (no triage, no report)."""
    if outcome.exception_type:
        return False
    if outcome.timed_out or outcome.signal_name:
        return False
    if outcome.exit_code != 0:
        return False
    if junit_failure:
        return False
    if go_on_stderr_traceback and stderr_has_python_traceback(stderr_text):
        return False
    return True


def is_go_signal(
    outcome: RunOutcome,
    *,
    junit_failure: bool,
    stderr_text: str,
    go_on_stderr_traceback: bool,
) -> bool:
    return not is_stop_signal(
        outcome,
        junit_failure=junit_failure,
        stderr_text=stderr_text,
        go_on_stderr_traceback=go_on_stderr_traceback,
    )


def resolve_sentinel_exit(
    *,
    child_exit: int,
    investigated: bool,
    exit_mode: str,
) -> int:
    if exit_mode == EXIT_MODE_SIGNAL:
        return EXIT_GO if investigated else EXIT_STOP
    return child_exit


def detect_junit_failure(junit_paths: Sequence[str]) -> bool:
    for raw in junit_paths:
        if junit_has_failures(Path(raw)):
            return True
    return False


def write_report_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(_redact_text(payload), encoding="utf-8")


def append_step_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown)
            if not markdown.endswith("\n"):
                handle.write("\n")
            handle.write("\n")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="err-o-roadrunner sentinel",
        description="Wrap a command with light monitoring; escalate to full triage on failure.",
    )
    parser.add_argument(
        "--ring-bytes",
        type=int,
        default=DEFAULT_RING_BYTES,
        help=f"Bytes retained per stdout/stderr stream (default {DEFAULT_RING_BYTES}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Kill the wrapped command after this many seconds.",
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        metavar="PATH",
        help="JUnit XML path (repeatable); failures escalate even on exit 0.",
    )
    parser.add_argument(
        "--actions-log",
        metavar="PATH",
        help="GitHub Actions job log file for triage on escalation.",
    )
    parser.add_argument(
        "--project-root",
        metavar="DIR",
        default=".",
        help="Project root for lock-file detection during escalation.",
    )
    parser.add_argument(
        "--report-json",
        metavar="PATH",
        help="Write machine-readable escalation report to this path.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Append human summary to GITHUB_STEP_SUMMARY when set.",
    )
    parser.add_argument(
        "--hook",
        action="append",
        default=[],
        metavar="SHELL",
        help="Shell command to run on escalation (repeatable).",
    )
    parser.add_argument(
        "--hook-timeout",
        type=float,
        default=DEFAULT_HOOK_TIMEOUT,
        help=f"Seconds per diagnostic hook (default {DEFAULT_HOOK_TIMEOUT}).",
    )
    parser.add_argument(
        "--print-triage",
        action="store_true",
        help="Print triage output to stdout on escalation (uses --format, default full).",
    )
    parser.add_argument(
        "--format",
        choices=OUTPUT_FORMATS,
        default=FORMAT_FULL,
        help="Stdout report style on escalation (default full = classic FOUND lines).",
    )
    parser.add_argument(
        "--tb",
        choices=TB_STYLES,
        default=TB_SHORT,
        help="Traceback/detail density for stdout formats (short, line, no).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Include NOT FOUND lines in short format (pytest -v analogue).",
    )
    parser.add_argument(
        "--max-fixes",
        type=int,
        default=DEFAULT_MAX_FIXES,
        metavar="N",
        help=f"Cap fix_candidates in JSON and stdout (default {DEFAULT_MAX_FIXES}).",
    )
    parser.add_argument(
        "--group-by",
        default="error_type",
        metavar="KEY[,KEY]",
        help=(
            "Group normalized errors for groups JSON and short/table stdout "
            f"({', '.join(GROUP_BY_KEYS)}; default error_type)."
        ),
    )
    parser.add_argument(
        "--sort",
        choices=GROUP_SORT_OPTIONS,
        default=SORT_COUNT,
        help="Sort group buckets by count, first_seen, or name (default count).",
    )
    parser.add_argument(
        "--exit-mode",
        choices=(EXIT_MODE_CHILD, EXIT_MODE_SIGNAL),
        default=EXIT_MODE_CHILD,
        help=(
            "child: return wrapped exit code (default, CI-safe). "
            "signal: 0=stop (no investigation), 1=go (investigation ran)."
        ),
    )
    parser.add_argument(
        "--go-on-stderr-traceback",
        action="store_true",
        help=(
            "Treat exit 0 with a Python traceback on stderr as go (default: stop). "
            "Owner default remains exit 0 = stop unless this flag is set."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command after `--` (e.g. sentinel -- pytest -q).",
    )
    return parser


def run_sentinel(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return 0 if code in (0, None) else 2

    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("sentinel: missing command after `--`", file=sys.stderr)
        return 2

    try:
        group_by = parse_group_by(args.group_by)
    except ValueError as exc:
        print(f"sentinel: {exc}", file=sys.stderr)
        return 2

    ring_bytes = max(1024, args.ring_bytes)

    project_root: Path | None
    try:
        project_root = Path(args.project_root).resolve()
        if not project_root.is_dir():
            project_root = None
    except OSError:
        project_root = None

    state, outcome = run_wrapped_command(
        cmd,
        cwd=os.getcwd(),
        timeout=args.timeout,
        env=None,
        ring_bytes=ring_bytes,
    )

    stderr_text = state.stderr_tail.text()
    child_exit = normalized_child_exit(outcome)

    junit_fail = detect_junit_failure(args.junit) if args.junit else False
    outcome.junit_failure = junit_fail

    emit_stdout = args.print_triage or args.format != FORMAT_FULL
    elapsed_stop = time.monotonic() - state.started_at

    if is_stop_signal(
        outcome,
        junit_failure=junit_fail,
        stderr_text=stderr_text,
        go_on_stderr_traceback=args.go_on_stderr_traceback,
    ):
        if args.format == FORMAT_SHORT:
            stop_text = render_stop_stdout(
                fmt=FORMAT_SHORT,
                exit_code=child_exit,
                elapsed_seconds=elapsed_stop,
            )
            if stop_text:
                print(stop_text, end="" if stop_text.endswith("\n") else "\n")
        return resolve_sentinel_exit(
            child_exit=child_exit,
            investigated=False,
            exit_mode=args.exit_mode,
        )

    try:
        max_fixes = max(0, args.max_fixes)
        doc = build_escalation_report(
            state,
            outcome,
            project_root=project_root,
            junit_paths=args.junit,
            actions_log_path=args.actions_log,
            hooks=args.hook,
            hook_timeout=args.hook_timeout,
            max_fixes=max_fixes,
            group_by=group_by,
            sort=args.sort,
        )
        if args.report_json:
            write_report_json(Path(args.report_json), doc)
        if args.summary:
            append_step_summary(doc.get("human_summary", ""))
        if args.print_triage or emit_stdout:
            text = render_stdout_format(
                doc,
                fmt=args.format,
                tb=args.tb,
                verbose=args.verbose > 0,
                outcome=outcome,
            )
            if text:
                print(text, end="" if text.endswith("\n") else "\n")
    except BaseException:
        pass

    return resolve_sentinel_exit(
        child_exit=child_exit,
        investigated=True,
        exit_mode=args.exit_mode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_sentinel(argv)


if __name__ == "__main__":
    raise SystemExit(main())
