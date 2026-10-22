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
from typing import Any, Callable, Sequence

from junit_redact import find_corpus_leaks, redact_text
from project_version import resolve_version
from triage import Report, triage

__version__ = resolve_version(package_dir=Path(__file__).resolve().parent)

SCHEMA_VERSION = "sentinel-report/v1"
DEFAULT_RING_BYTES = 64 * 1024
DEFAULT_HOOK_TIMEOUT = 30
ENV_VALUE_MAX = 200

# Exit-mode: child (default) keeps the wrapped exit code; signal uses RoadRunner codes.
EXIT_MODE_CHILD = "child"
EXIT_MODE_SIGNAL = "signal"
EXIT_STOP = 0  # signal mode: investigation not run (stop)
EXIT_GO = 1  # signal mode: investigation path ran (go)
EXIT_USAGE = 2

_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|authorization)"
)


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
    timed_out: bool = False
    signal_name: str | None = None
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
        redacted_values[name] = redact_text(raw)
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
            exit_code=2,
            exception_type="ValueError",
            exception_message="empty command",
        )

    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
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
    signal_name: str | None = None
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    finally:
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
        return state, RunOutcome(exit_code=code, timed_out=True)
    if code is not None and code < 0:
        sig = signal.Signals(-code).name
        return state, RunOutcome(exit_code=code, signal_name=sig)
    return state, RunOutcome(exit_code=code)


def report_to_structured(report: Report) -> dict[str, list[str]]:
    return {
        "found": list(report.found),
        "not_found": list(report.not_found),
        "hints": list(report.hints),
    }


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
            entry["stdout"] = redact_text((completed.stdout or "")[:8000])
            entry["stderr"] = redact_text((completed.stderr or "")[:8000])
        except subprocess.TimeoutExpired:
            entry["exit_code"] = None
            entry["stderr"] = f"hook timed out after {timeout}s"
        except OSError as exc:
            entry["stderr"] = redact_text(str(exc))
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

    exit_code = outcome.exit_code if outcome.exit_code is not None else 1
    if outcome.timed_out:
        exit_code = exit_code if exit_code not in (None, 0) else 124
    if outcome.junit_failure and exit_code == 0:
        exit_code = 1

    report = triage(
        exit_code=exit_code,
        stderr_text=stderr_text if stderr_text.strip() else None,
        log_text=log_text,
        actions_log_text=actions_log_text,
        junit_paths=junit_paths,
        project_root=project_root,
    )

    reasons: list[str] = []
    if outcome.exit_code not in (None, 0):
        reasons.append("non_zero_exit")
    if outcome.timed_out:
        reasons.append("timeout")
    if outcome.signal_name:
        reasons.append(f"signal:{outcome.signal_name}")
    if outcome.junit_failure:
        reasons.append("junit_failure")
    if outcome.exception_type:
        reasons.append(f"wrapper:{outcome.exception_type}")

    structured = report_to_structured(report)
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
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "signal": outcome.signal_name,
            "junit_failure": outcome.junit_failure,
        },
        "capture": {
            "stdout_tail": redact_text(stdout_text),
            "stderr_tail": redact_text(stderr_text),
        },
        "environment": summarize_environment(),
        "triage": structured,
        "locations": _extract_locations(structured["found"]),
        "next_investigation_steps": _next_investigation_steps(report, outcome),
        "human_summary": _human_summary(structured, reasons, state, outcome),
    }
    if hooks:
        doc["diagnostic_hooks"] = run_diagnostic_hooks(hooks, timeout=hook_timeout)
    return doc


def _human_summary(
    structured: dict[str, list[str]],
    reasons: Sequence[str],
    state: WatchState,
    outcome: RunOutcome,
) -> str:
    lines = [
        "## Err-o-RoadRunner sentinel (escalated)",
        "",
        f"- **Reasons:** {', '.join(reasons) or 'failure'}",
        f"- **Command:** `{shell_join(state.command_argv)}`",
        f"- **Exit code:** {outcome.exit_code}",
    ]
    if outcome.timed_out:
        lines.append("- **Timed out:** yes")
    if outcome.signal_name:
        lines.append(f"- **Signal:** {outcome.signal_name}")
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
    return "\n".join(lines)


def is_stop_signal(outcome: RunOutcome, *, junit_failure: bool) -> bool:
    """Early-stop path: wrapped exit 0 and no go triggers (no triage, no report)."""
    if outcome.exception_type:
        return False
    if outcome.timed_out or outcome.signal_name:
        return False
    if outcome.exit_code != 0:
        return False
    if junit_failure:
        return False
    return True


def is_go_signal(outcome: RunOutcome, *, junit_failure: bool) -> bool:
    return not is_stop_signal(outcome, junit_failure=junit_failure)


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
    path.write_text(redact_text(payload), encoding="utf-8")


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
        help="Print FOUND/NOT FOUND/HINT lines to stdout on escalation.",
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

    child_exit = outcome.exit_code if outcome.exit_code is not None else 1
    if outcome.timed_out and child_exit == 0:
        child_exit = 124

    # JUnit is consulted only when needed for the stop/go decision (exit 0 may still be go).
    junit_fail = detect_junit_failure(args.junit) if args.junit else False
    outcome.junit_failure = junit_fail

    if is_stop_signal(outcome, junit_failure=junit_fail):
        return resolve_sentinel_exit(
            child_exit=child_exit,
            investigated=False,
            exit_mode=args.exit_mode,
        )

    try:
        doc = build_escalation_report(
            state,
            outcome,
            project_root=project_root,
            junit_paths=args.junit,
            actions_log_path=args.actions_log,
            hooks=args.hook,
            hook_timeout=args.hook_timeout,
        )
        if args.report_json:
            write_report_json(Path(args.report_json), doc)
        if args.summary:
            append_step_summary(doc.get("human_summary", ""))
        if args.print_triage:
            tri = doc.get("triage") or {}
            for item in tri.get("found", []):
                print(f"FOUND: {item}")
            for item in tri.get("not_found", []):
                print(f"NOT FOUND: {item}")
            for item in tri.get("hints", []):
                print(f"HINT: {item}")
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
