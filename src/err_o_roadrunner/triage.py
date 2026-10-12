"""Read-only failure triage CLI (stdlib only)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

# Stable exit codes (documented in README).
EXIT_OK = 0
EXIT_USAGE = 2

MAX_READ_BYTES = 10 * 1024 * 1024
MAX_XML_TAGS = 100_000
MAX_FRAMES = 2_000
MAX_ENV_NAMES_LISTED = 40

# Terminal sequences stripped before removing remaining C0/C1 controls.
_CSI_BODY = r"[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
TERMINAL_SEQUENCE_PATTERNS = (
    re.compile(r"\x1b\][^\x07\x1b\n\u009c]*(?:\x07|\x1b\\|\u009c)?"),
    re.compile(r"\x1bP[^\x1b\n\u009c]*(?:\x1b\\|\u009c)?"),
    re.compile(r"\x1b\[" + _CSI_BODY),
    re.compile(r"\x9b" + _CSI_BODY),
    re.compile(r"\u009d[^\u009c\n]*\u009c?"),
    re.compile(r"\u0090[^\u009c\n]*\u009c?"),
    re.compile(r"\x1b_[^\n\u009c]*(?:\x1b\\|\u009c)?"),
    re.compile(r"\x1b\^[^\n\u009c]*(?:\x1b\\|\u009c)?"),
    re.compile(r"\x1bX[^\n\u009c]*(?:\x1b\\|\u009c)?"),
    re.compile(r"\x1bc"),
)
PYTHON_BUILTIN_EXCEPTIONS = frozenset(
    {
        "BaseException",
        "Exception",
        "GeneratorExit",
        "KeyboardInterrupt",
        "SystemExit",
        "StopIteration",
        "StopAsyncIteration",
    }
)

PYTHON_FRAME_RE = re.compile(
    r'^  File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.*))?$'
)
PYTHON_EXCEPTION_SUMMARY_RE = re.compile(
    r"^(?P<exc>[A-Za-z_][\w.]*)(?::\s*(?P<msg>.*))?$"
)
PYTHON_CHAIN_MARKERS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
SYNTAX_ERROR_LINE_RE = re.compile(
    r"^(?P<exc>SyntaxError)(?::\s*(?P<msg>.*))?$"
)
NODE_ERROR_HEADLINE_RE = re.compile(
    r"^(?P<head>(?:[A-Za-z_][\w]*(?:Error|Warning|Rejection)|Unhandled\s+rejection(?:\s+\([^)]+\))?))"
    r"\s*:\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
NODE_SCRIPT_LOC_LINE_RE = re.compile(
    r"^(?P<file>(?!node:)(?:/[^\s:]+|\[[^\]]+\]|[^\s:]+)):(?P<line>\d+)\s*$"
)
NODE_CORE_ERROR_RE = re.compile(
    r"^(?P<head>(?:TypeError|ReferenceError|SyntaxError|RangeError|EvalError|URIError|AggregateError))\s*:\s*(?P<rest>.*)$",
)
NODE_CAPITAL_ERROR_RE = re.compile(r"^Error:\s*(?P<rest>.*)$")
NODE_ERROR_BRACKET_CODE_RE = re.compile(
    r"^Error \[(?P<code>ERR_[A-Z0-9_]+)\]:\s*(?P<rest>.*)$"
)
NODE_CAUSE_RE = re.compile(r"^\[cause\]:\s*(?P<rest>.+)$", re.IGNORECASE)
NPM_ERR_RE = re.compile(r"^npm ERR!\s+(?P<rest>.+)$", re.IGNORECASE)
JEST_FAILURE_BULLET_RE = re.compile(r"^●\s+(?P<rest>.+)$")
COMPILER_LOWERCASE_ERROR_RE = re.compile(r"^error:\s+")
EXCEPTION_GROUP_SUMMARY_RE = re.compile(
    r"^(?P<exc>(?:Base)?ExceptionGroup)(?::\s*(?P<msg>.*))?$"
)
EXCEPTION_GROUP_TB_START_RE = re.compile(
    r"^\s*\+ Exception Group Traceback \(most recent call last\):"
)
EXCEPTION_GROUP_BORDER_RE = re.compile(r"^\s*\+[-+].*$")
NODE_ERR_ASSERTION_RE = re.compile(
    r"^AssertionError\s+\[ERR_ASSERTION\]:\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
DENO_WRAPPED_ERROR_RE = re.compile(
    r"^error:\s*(?:Uncaught\b.*?\s+)?(?P<head>[A-Za-z_][\w]*Error)\s*:\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
RUSTC_ERROR_CODE_RE = re.compile(r"^error\[E\d+\]:\s*(?P<rest>.+)$")
RUSTC_ARROW_RE = re.compile(r"^\s*-->\s+(?P<file>\S+):(?P<line>\d+):(?P<col>\d+)")
GCC_ERROR_RE = re.compile(
    r"^(?P<file>[^:\s]+):\d+:\d+:\s+error:\s*(?P<rest>.+)$"
)
DART_COMPILER_ERROR_RE = re.compile(
    r"^(?P<file>\S+\.dart):(?P<line>\d+):(?P<col>\d+):\s*Error:\s*(?P<rest>.+)$"
)
PYTHON_EXCEPTIONS_WITHOUT_MESSAGE = frozenset(
    {
        "KeyboardInterrupt",
        "MemoryError",
        "SystemExit",
        "GeneratorExit",
        "CancelledError",
        "TimeoutError",
    }
)
NODE_BRACKET_REJECTION_RE = re.compile(
    r"^\[UnhandledPromiseRejection:\s*(?P<rest>.*)\]\s*$",
    re.IGNORECASE,
)
NODE_ERR_CODE_RE = re.compile(
    r"\bcode:\s*['\"]?(ERR_[A-Z0-9_]+|MODULE_NOT_FOUND)['\"]?",
    re.IGNORECASE,
)
NODE_AT_FRAME_RE = re.compile(
    r"^\s*at (?:async )?(?:(?P<func>[^()]+?) \()?(?P<loc>.+):(?P<line>\d+):(?P<col>\d+)\)?(?: \{)?\s*$"
)
NODE_AT_LOOSE_RE = re.compile(
    r"^\s*at (?:async )?(?P<loc>.+):(?P<line>\d+)\s*$"
)
NODE_SYNTAX_PATH_RE = re.compile(
    r"\.(?:ts|tsx|js|mjs|cjs|jsx)(?:\b|:)",
    re.IGNORECASE,
)

LOCK_FILES = (
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "Cargo.lock",
    "go.sum",
)


@dataclass
class Report:
    found: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    def add_found(self, line: str) -> None:
        self.found.append(sanitize_report_text(line))

    def add_not_found(self, line: str) -> None:
        self.not_found.append(sanitize_report_text(line))

    def add_hint(self, line: str) -> None:
        self.hints.append(sanitize_report_text(line))

    def extend(self, other: Report) -> None:
        self.found.extend(other.found)
        self.not_found.extend(other.not_found)
        self.hints.extend(other.hints)

    def lines(self) -> list[str]:
        out: list[str] = []
        for item in self.found:
            out.append(f"FOUND: {item}")
        for item in self.not_found:
            out.append(f"NOT FOUND: {item}")
        for item in self.hints:
            out.append(f"HINT: {item}")
        return out


def sanitize_report_text(text: str) -> str:
    return strip_terminal_controls(text)


def strip_terminal_controls(text: str) -> str:
    out = text
    for pattern in TERMINAL_SEQUENCE_PATTERNS:
        out = pattern.sub("", out)
    cleaned: list[str] = []
    for ch in out:
        code = ord(ch)
        if ch in "\t\n":
            cleaned.append(ch)
        elif code < 32 or code == 0x7F or 0x80 <= code <= 0x9F:
            continue
        else:
            cleaned.append(ch)
    return "".join(cleaned)


def is_python_exception_type_name(name: str) -> bool:
    short = name.rsplit(".", 1)[-1]
    if short in PYTHON_BUILTIN_EXCEPTIONS or name in PYTHON_BUILTIN_EXCEPTIONS:
        return True
    if short in ("ExceptionGroup", "BaseExceptionGroup"):
        return True
    return bool(
        re.fullmatch(
            r"[A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit|Group)",
            short,
        )
    )


def parse_exception_summary_line(line: str) -> tuple[str, str] | None:
    match = PYTHON_EXCEPTION_SUMMARY_RE.match(line.strip())
    if not match:
        return None
    exc = match.group("exc")
    if not is_python_exception_type_name(exc):
        return None
    return exc, (match.group("msg") or "").strip()


def strip_ansi(text: str) -> str:
    return strip_terminal_controls(text)


def safe_read_text(path: Path, report: Report) -> str | None:
    """Read a user path safely; never follow symlinks."""
    try:
        raw = path.expanduser()
        if ".." in path.parts:
            report.add_not_found(f"unsafe path (path traversal rejected): {path}")
            return None
        try:
            st = raw.lstat()
        except OSError as exc:
            report.add_not_found(f"file not readable: {path} ({type(exc).__name__})")
            return None
        if stat.S_ISLNK(st.st_mode):
            report.add_not_found(f"symlink not followed: {path}")
            return None
        if stat.S_ISDIR(st.st_mode):
            report.add_not_found(f"not a regular file (directory): {path}")
            return None
        if not stat.S_ISREG(st.st_mode):
            report.add_not_found(f"not a regular file: {path}")
            return None
        size = st.st_size
        if size > MAX_READ_BYTES:
            report.add_found(f"input truncated (size {size} > {MAX_READ_BYTES}): {path}")
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(raw, flags)
        try:
            fst = os.fstat(fd)
        except OSError:
            os.close(fd)
            raise
        if fst.st_dev != st.st_dev or fst.st_ino != st.st_ino:
            os.close(fd)
            report.add_not_found(f"file identity changed after open: {path}")
            return None
        if not stat.S_ISREG(fst.st_mode):
            os.close(fd)
            report.add_not_found(f"not a regular file after open: {path}")
            return None
        try:
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_READ_BYTES)
        except Exception:
            os.close(fd)
            raise
    except OSError as exc:
        report.add_not_found(f"file not readable: {path} ({type(exc).__name__})")
        return None
    text = data.decode("utf-8", errors="replace")
    if b"\x00" in data:
        report.add_found(f"binary or nul bytes present in: {path}")
    return strip_terminal_controls(text)


def read_stream_arg(value: str | None, report: Report, label: str) -> str | None:
    if value is None:
        return None
    if value == "-":
        try:
            data = sys.stdin.buffer.read(MAX_READ_BYTES + 1)
        except OSError as exc:
            report.add_not_found(f"{label} stdin ({type(exc).__name__})")
            return None
        if len(data) > MAX_READ_BYTES:
            report.add_found(f"input truncated (size > {MAX_READ_BYTES}): {label} stdin")
            data = data[:MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
        if b"\x00" in data:
            report.add_found(f"binary or nul bytes present in: {label} stdin")
        return strip_terminal_controls(text)
    return safe_read_text(Path(value), report)


@dataclass(frozen=True)
class StackFrame:
    file: str
    line: int
    func: str = ""

    def format(self) -> str:
        if self.func:
            return f"{self.file}:{self.line} in {self.func}"
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class _TraceBlock:
    lines: list[str]
    linked_to_previous: bool


def _normalize_python_trace_line(line: str) -> str:
    if EXCEPTION_GROUP_TB_START_RE.match(line):
        return "Traceback (most recent call last):"
    pipe = re.match(r"^(\s*)\| (.*)$", line)
    if pipe:
        line = f"{pipe.group(1)}{pipe.group(2)}"
    if re.match(r"^\s+File ", line):
        return "  " + line.lstrip()
    return line


def _prepare_python_trace_text(text: str) -> str:
    out: list[str] = []
    for raw in text.splitlines():
        if EXCEPTION_GROUP_BORDER_RE.match(raw):
            continue
        if re.match(r"^\s*\+-+\s*\d+\s*-+", raw):
            continue
        out.append(_normalize_python_trace_line(raw))
    return "\n".join(out)


def _split_python_trace_blocks(text: str) -> tuple[list[_TraceBlock], int]:
    text = _prepare_python_trace_text(text)
    lines = text.splitlines()
    blocks: list[_TraceBlock] = []
    current: list[str] = []
    next_linked = False
    linked = False
    for line in lines:
        if line.startswith("Traceback (most recent call last):"):
            if current:
                blocks.append(_TraceBlock(current, linked))
                current = []
            linked = next_linked
            next_linked = False
            current = [line]
        elif line in PYTHON_CHAIN_MARKERS:
            if current:
                blocks.append(_TraceBlock(current, linked))
                current = []
            next_linked = True
        elif current:
            current.append(line)
        elif SYNTAX_ERROR_LINE_RE.match(line.strip()):
            stripped_syntax = line.strip()
            if _is_node_syntax_error_line(stripped_syntax) or _text_has_node_stack_frames(
                text
            ):
                continue
            if blocks and not blocks[-1].lines[0].startswith("Traceback"):
                blocks[-1].lines.append(line)
            elif current:
                current.append(line)
            else:
                blocks.append(_TraceBlock([line], linked))
                linked = False
                next_linked = False
        elif line.startswith("  File "):
            if current:
                blocks.append(_TraceBlock(current, linked))
            linked = next_linked
            next_linked = False
            current = [line]
    if current:
        blocks.append(_TraceBlock(current, linked))
    unrelated = sum(1 for i, block in enumerate(blocks) if i > 0 and not block.linked_to_previous)
    return blocks, unrelated


def _report_key(label: str, phrase: str) -> str:
    return f"{phrase} ({label})" if label else phrase


def _parse_one_python_block(tb: list[str], report: Report, label: str) -> tuple[str, str]:
    frames: list[StackFrame] = []
    exc_type = ""
    exc_msg = ""
    frame_cap_reported = False
    tail_lines: list[str] = []
    start = 1 if tb and tb[0].startswith("Traceback") else 0
    for line in tb[start:]:
        m = PYTHON_FRAME_RE.match(line)
        if m:
            if len(frames) < MAX_FRAMES:
                frames.append(
                    StackFrame(
                        file=m.group("file"),
                        line=int(m.group("line")),
                        func=(m.group("func") or "").strip(),
                    )
                )
            elif not frame_cap_reported:
                report.add_found(_report_key(label, f"python traceback frame cap reached ({MAX_FRAMES})"))
                frame_cap_reported = True
            continue
        if re.match(r"^    \S", line):
            probe = line.strip()
            if not parse_exception_summary_line(probe) and not EXCEPTION_GROUP_SUMMARY_RE.match(
                probe
            ):
                continue
        stripped = line.strip()
        if not stripped or stripped.startswith("^"):
            continue
        tail_lines.append(stripped)

    for stripped in tail_lines:
        parsed = parse_exception_summary_line(stripped)
        if parsed:
            name, msg = parsed
            if name.endswith("Warning"):
                if not exc_type:
                    exc_type, exc_msg = name, msg
                else:
                    report.add_hint(
                        f"{_report_key(label, 'warning line after traceback')}: {stripped}"
                    )
                continue
            if not exc_type:
                exc_type, exc_msg = name, msg
            else:
                report.add_hint(f"{_report_key(label, 'additional exception line')}: {stripped}")
            continue
        eg = EXCEPTION_GROUP_SUMMARY_RE.match(stripped)
        if eg and not exc_type:
            exc_type = eg.group("exc")
            exc_msg = (eg.group("msg") or "").strip()
            continue
        if re.match(r"^[A-Za-z_][\w.]*:", stripped):
            report.add_hint(f"{_report_key(label, 'trailing colon line')}: {stripped}")
        elif re.match(r"^[A-Za-z_][\w.]*$", stripped):
            report.add_hint(f"{_report_key(label, 'uncertain traceback line')}: {stripped}")

    if exc_type in ("ExceptionGroup", "BaseExceptionGroup"):
        loc = frames[0].format() if frames else ""
        for stripped in tail_lines:
            sub = parse_exception_summary_line(stripped)
            if sub and sub[0] not in ("ExceptionGroup", "BaseExceptionGroup"):
                suffix = f" ({loc})" if loc else ""
                report.add_hint(
                    f"{_report_key(label, 'exception group sub-exception')}: "
                    f"{stripped[:360]}{suffix}"
                )

    if exc_type:
        report.add_found(f"{_report_key(label, 'python exception type')}: {exc_type}")
        if exc_msg:
            report.add_found(f"{_report_key(label, 'python exception message')}: {exc_msg}")
        elif exc_type.rsplit(".", 1)[-1] not in PYTHON_EXCEPTIONS_WITHOUT_MESSAGE:
            report.add_not_found(_report_key(label, "python exception message"))
    else:
        report.add_not_found(_report_key(label, "python exception type"))
        if frames:
            report.add_hint(
                f"{_report_key(label, 'python traceback without exception summary')}: "
                f"{frames[-1].format()}"
            )

    if frames:
        report.add_found(f"{_report_key(label, 'python traceback frame (first)')}: {frames[0].format()}")
        report.add_found(f"{_report_key(label, 'python traceback frame (last)')}: {frames[-1].format()}")
        report.add_found(f"{_report_key(label, 'python traceback frame count')}: {len(frames)}")
    else:
        report.add_not_found(_report_key(label, "python stack frames"))

    return exc_type, exc_msg


def parse_python_traceback(text: str, report: Report, label: str = "") -> None:
    blocks, unrelated_count = _split_python_trace_blocks(text)
    if not blocks:
        if any(m in text for m in PYTHON_CHAIN_MARKERS):
            report.add_hint(
                "python exception chain marker without traceback body: "
                + next(m for m in PYTHON_CHAIN_MARKERS if m in text)[:120]
            )
            return
        if EXCEPTION_GROUP_TB_START_RE.search(text) or "ExceptionGroup" in text:
            report.add_hint("python exception group traceback present but unparsed")
            return
        report.add_not_found("python traceback")
        return

    prefix = label
    block_labels: list[str] = []
    chained_n = 0
    unrelated_n = 0
    for i, block in enumerate(blocks):
        if i == 0:
            block_labels.append(prefix)
        elif block.linked_to_previous:
            chained_n += 1
            block_labels.append(
                f"{prefix}chained-{chained_n}" if prefix else f"chained-{chained_n}"
            )
        else:
            unrelated_n += 1
            block_labels.append(
                f"{prefix}unrelated-{unrelated_n}" if prefix else f"unrelated-{unrelated_n}"
            )

    if chained_n:
        report.add_found(f"python chained traceback blocks: {chained_n}")
    if unrelated_n:
        report.add_found(f"python unrelated traceback blocks: {unrelated_n}")

    last_type = ""
    last_msg = ""
    for block, block_label in zip(blocks, block_labels):
        exc_type, exc_msg = _parse_one_python_block(block.lines, report, block_label)
        if exc_type:
            last_type, last_msg = exc_type, exc_msg

    if last_type or last_msg:
        keywords = " ".join(part for part in (last_type, last_msg) if part)
        report.add_hint(f"web search keywords: {keywords[:500]}")


def parse_syntax_error(text: str, report: Report) -> None:
    for line in text.splitlines():
        m = SYNTAX_ERROR_LINE_RE.match(line.strip())
        if m:
            report.add_found(f"python exception type: {m.group('exc')}")
            msg = (m.group("msg") or "").strip()
            if msg:
                report.add_found(f"python exception message: {msg}")
            else:
                report.add_not_found("python exception message")
            report.add_hint(f"web search keywords: SyntaxError {msg[:400]}")
            return
    report.add_not_found("python syntax error")


def _node_syntax_user_location(text: str) -> StackFrame | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = NODE_SCRIPT_LOC_LINE_RE.match(stripped)
        if not m:
            continue
        file = m.group("file")
        line_no = int(m.group("line"))
        for j in range(i + 1, min(i + 10, len(lines))):
            probe = lines[j]
            stripped_probe = probe.strip()
            if not stripped_probe:
                continue
            if stripped_probe.startswith("at ") or _parse_node_at_line(probe):
                break
            m_core = NODE_CORE_ERROR_RE.match(stripped_probe)
            if m_core:
                if m_core.group("head") == "SyntaxError":
                    return StackFrame(file=file, line=line_no, func="")
                break
            if SYNTAX_ERROR_LINE_RE.match(stripped_probe):
                return StackFrame(file=file, line=line_no, func="")
        for j in range(i + 1, min(i + 10, len(lines))):
            if "SyntaxError:" in lines[j]:
                return StackFrame(file=file, line=line_no, func="")
    return None


def _parse_node_at_line(line: str) -> StackFrame | None:
    m = NODE_AT_FRAME_RE.match(line)
    if m:
        func = (m.group("func") or "").strip()
        return StackFrame(file=m.group("loc"), line=int(m.group("line")), func=func)
    m = NODE_AT_LOOSE_RE.match(line)
    if m:
        return StackFrame(file=m.group("loc"), line=int(m.group("line")), func="")
    return None



def _text_has_node_stack_frames(text: str) -> bool:
    return bool(re.search(r"(?:^|\n)\s*at .+:\d+", text))


def _node_error_follows_line(lines: list[str], index: int) -> bool:
    for j in range(index + 1, min(index + 4, len(lines))):
        probe = lines[j].strip()
        if not probe:
            continue
        if NODE_AT_FRAME_RE.match(lines[j]) or NODE_AT_LOOSE_RE.match(lines[j]):
            return True
        if probe.startswith("at "):
            return True
        break
    return False


def _skip_node_error_line_in_python_context(
    text: str, line_index: int, stripped: str
) -> bool:
    if not _has_python_failure_signal(text):
        return False
    if not parse_exception_summary_line(stripped):
        return False
    if NODE_ERROR_BRACKET_CODE_RE.match(stripped):
        return False
    if NODE_ERR_ASSERTION_RE.match(stripped):
        return False
    if DENO_WRAPPED_ERROR_RE.match(stripped):
        return False
    lines = text.splitlines()
    if _node_error_follows_line(lines, line_index):
        return False
    return True


def _append_node_error_line(error_lines: list[str], stripped: str) -> None:
    m_bracket = NODE_ERROR_BRACKET_CODE_RE.match(stripped)
    if m_bracket:
        code = m_bracket.group("code")
        rest = (m_bracket.group("rest") or "").strip()
        headline = f"Error [{code}]"
        error_lines.append(f"{headline}: {rest}" if rest else f"{headline}:")
        return
    m_assert = NODE_ERR_ASSERTION_RE.match(stripped)
    if m_assert:
        rest = (m_assert.group("rest") or "").strip()
        error_lines.append(f"AssertionError [ERR_ASSERTION]: {rest}" if rest else "AssertionError [ERR_ASSERTION]")
        return
    m_deno = DENO_WRAPPED_ERROR_RE.match(stripped)
    if m_deno:
        rest = (m_deno.group("rest") or "").strip()
        head = m_deno.group("head")
        error_lines.append(f"{head}: {rest}" if rest else f"{head}:")
        return
    m_head = NODE_ERROR_HEADLINE_RE.match(stripped)
    if m_head:
        rest = (m_head.group("rest") or "").strip()
        headline = m_head.group("head")
        error_lines.append(f"{headline}: {rest}" if rest else f"{headline}:")
        return
    m_core = NODE_CORE_ERROR_RE.match(stripped)
    if m_core:
        rest = (m_core.group("rest") or "").strip()
        headline = m_core.group("head")
        error_lines.append(f"{headline}: {rest}" if rest else f"{headline}:")
        return
    m_cap = NODE_CAPITAL_ERROR_RE.match(stripped)
    if m_cap:
        rest = (m_cap.group("rest") or "").strip()
        error_lines.append(f"Error: {rest}" if rest else "Error:")
        return
    m_bracket = NODE_BRACKET_REJECTION_RE.match(stripped)
    if m_bracket:
        error_lines.append(f"[UnhandledPromiseRejection: {m_bracket.group('rest')}]")
        return
    m_npm = NPM_ERR_RE.match(stripped)
    if m_npm:
        error_lines.append(f"npm ERR! {m_npm.group('rest')}")
        return
    m_jest = JEST_FAILURE_BULLET_RE.match(stripped)
    if m_jest:
        error_lines.append(f"Jest: {m_jest.group('rest')}")


def parse_node_stack(text: str, report: Report) -> None:
    error_lines: list[str] = []
    err_codes: list[str] = []
    causes: list[str] = []
    frames_by_error: list[list[StackFrame]] = []
    current_frames: list[StackFrame] = []

    def _flush_frames() -> None:
        nonlocal current_frames
        if current_frames:
            frames_by_error.append(current_frames)
            current_frames = []

    lines = text.splitlines()
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if _skip_node_error_line_in_python_context(text, line_index, stripped):
            continue
        if (
            parse_exception_summary_line(stripped)
            and not NODE_CORE_ERROR_RE.match(stripped)
            and not NODE_ERROR_HEADLINE_RE.match(stripped)
            and not NODE_CAPITAL_ERROR_RE.match(stripped)
            and not NODE_ERROR_BRACKET_CODE_RE.match(stripped)
        ):
            continue
        if COMPILER_LOWERCASE_ERROR_RE.match(stripped) and not DENO_WRAPPED_ERROR_RE.match(
            stripped
        ):
            continue
        m_cause = NODE_CAUSE_RE.match(stripped)
        if m_cause:
            causes.append(m_cause.group("rest").strip())
            continue
        before = len(error_lines)
        _append_node_error_line(error_lines, stripped)
        if len(error_lines) > before:
            _flush_frames()
        m_bracket_code = NODE_ERROR_BRACKET_CODE_RE.match(stripped)
        if m_bracket_code:
            err_codes.append(m_bracket_code.group("code"))
        m_code = NODE_ERR_CODE_RE.search(stripped)
        if m_code:
            err_codes.append(m_code.group(1))
        frame = _parse_node_at_line(line)
        if frame:
            current_frames.append(frame)

    _flush_frames()

    all_frames = [f for chunk in frames_by_error for f in chunk]

    user_syntax = _node_syntax_user_location(text)
    if user_syntax:
        report.add_found(f"node syntax error location: {user_syntax.format()}")

    if error_lines:
        report.add_found(f"node error lines: {len(error_lines)}")
        report.add_found(f"node error line: {error_lines[0][:500]}")
        for i, err in enumerate(error_lines[1:6], start=2):
            report.add_found(f"node error line {i}: {err[:400]}")
    else:
        report.add_not_found("node error headline")
        if all_frames:
            report.add_hint(
                f"node stack without error headline: {all_frames[-1].format()}"
            )

    if causes:
        report.add_found(f"node error causes: {len(causes)}")
        report.add_found(f"node error cause (first): {causes[0][:400]}")

    if err_codes:
        report.add_found(f"node error code: {err_codes[0]}")

    if all_frames:
        report.add_found(f"node stack frame (first): {all_frames[0].format()}")
        report.add_found(f"node stack frame (last): {all_frames[-1].format()}")
        report.add_found(f"node stack frame count: {len(all_frames)}")
        for i, chunk in enumerate(frames_by_error[:5], start=1):
            if chunk:
                report.add_found(
                    f"node error {i} stack frame (first): {chunk[0].format()}"
                )
    else:
        report.add_not_found("node stack frames")

    if error_lines or err_codes or user_syntax:
        hint = error_lines[0] if error_lines else err_codes[0] if err_codes else ""
        if user_syntax:
            hint = f"{user_syntax.format()} {hint}".strip()
        report.add_hint(f"web search keywords: {hint[:400]}")



def _bounded_xml_iter(root: ET.Element) -> Iterator[ET.Element]:
    count = 0
    for elem in root.iter():
        count += 1
        if count > MAX_XML_TAGS:
            raise ValueError("xml too large")
        yield elem


PYTEST_E_EXC_LINE_RE = re.compile(
    r"^\s*E\s+(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Failed|Timeout))(?:\s*:|$)"
)
PYTEST_E_ASSERT_LINE_RE = re.compile(r"^\s*E\s+assert\b")
JUNIT_DIFF_LINE_RE = re.compile(r"^\s*[+-]\s+")
DART_ERROR_LINE_RE = re.compile(
    r"^(?P<line>\S+\.dart:\d+:\d+:\s+Error:\s*.+)$"
)
DART_NOISE_LINE_RE = re.compile(
    r"^-\s+'Object'\s+is\s+from\s+'dart:core'\."
)


def _junit_child_text(parent: ET.Element, local_name: str) -> str:
    for child in parent:
        if child.tag.split("}")[-1] == local_name:
            return sanitize_report_text((child.text or "").strip())
    return ""


def _junit_case_label(testcase: ET.Element) -> str:
    classname = (testcase.attrib.get("classname") or "").strip()
    name = (testcase.attrib.get("name") or "").strip()
    if classname and name:
        return f"{classname}::{name}"
    return name or classname or "testcase"


def _junit_failure_blob(testcase: ET.Element, elem: ET.Element) -> str:
    parts: list[str] = []
    msg = sanitize_report_text((elem.attrib.get("message") or "").strip())
    body = sanitize_report_text((elem.text or "").strip())
    if msg and body:
        if body == msg or msg in body:
            parts.append(body)
        elif body in msg:
            parts.append(msg)
        else:
            parts.append(msg)
            parts.append(body)
    elif msg:
        parts.append(msg)
    elif body:
        parts.append(body)
    for extra in ("system-out", "system-err"):
        chunk = _junit_child_text(testcase, extra)
        if chunk:
            parts.append(chunk)
    return "\n".join(parts)


def _junit_flatten_for_report(text: str, limit: int = 400) -> str:
    flat = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return flat[:limit]


def _junit_snippet(elem: ET.Element, testcase: ET.Element | None = None) -> str:
    if testcase is not None:
        blob = _junit_failure_blob(testcase, elem)
        if blob.strip():
            return _junit_flatten_for_report(blob)
        return "failure"
    msg = sanitize_report_text((elem.attrib.get("message") or "").strip())
    body = sanitize_report_text((elem.text or "").strip())
    if msg and body:
        if body == msg or msg in body:
            return _junit_flatten_for_report(body)
        if body in msg:
            return _junit_flatten_for_report(msg)
        if msg not in body:
            return _junit_flatten_for_report(f"{msg}\n{body}")
    if msg:
        return _junit_flatten_for_report(msg)
    if body:
        return _junit_flatten_for_report(body)
    return "failure"


def _junit_is_pytest_collection_failure(error_elem: ET.Element, blob: str) -> bool:
    msg = (error_elem.attrib.get("message") or "").strip().lower()
    if msg == "collection failure":
        return True
    low = blob.lower()
    return "importerror while importing" in low or "collection error" in low


def _junit_pytest_exception_type(blob: str) -> str:
    for line in blob.splitlines():
        m = PYTEST_E_EXC_LINE_RE.match(line)
        if m:
            name = m.group("exc")
            if name not in PYTHON_BUILTIN_EXCEPTIONS:
                return name
    return ""


def _junit_pytest_assert_hint(blob: str) -> str:
    for line in blob.splitlines():
        if PYTEST_E_ASSERT_LINE_RE.match(line):
            return line.strip()[:300]
    return ""


def _junit_diff_hint(blob: str) -> str:
    lines = [
        ln
        for ln in blob.splitlines()
        if JUNIT_DIFF_LINE_RE.match(ln) and not DART_NOISE_LINE_RE.match(ln.strip())
    ]
    if not lines:
        return ""
    return " ".join(lines[:6])[:400]


def _junit_dart_error_line(blob: str) -> str:
    for line in blob.splitlines():
        stripped = line.strip()
        if not stripped or DART_NOISE_LINE_RE.match(stripped):
            continue
        if stripped.startswith("Try correcting") or stripped.startswith("^"):
            continue
        m = DART_ERROR_LINE_RE.match(stripped)
        if m:
            return m.group("line")[:400]
    return ""


def _junit_testsuite_collection_errors(root: ET.Element) -> list[str]:
    """Pytest/JUnit collection failures reported as <error> directly under <testsuite>."""
    out: list[str] = []
    for suite in root.iter():
        if suite.tag.split("}")[-1] != "testsuite":
            continue
        for child in suite:
            if child.tag.split("}")[-1] != "error":
                continue
            msg = sanitize_report_text((child.attrib.get("message") or "").strip())
            body = sanitize_report_text((child.text or "").strip())
            if msg and body:
                out.append(_junit_flatten_for_report(f"{msg}\n{body}"))
            elif msg:
                out.append(msg[:300])
            elif body:
                out.append(_junit_flatten_for_report(body))
    return out


def _junit_framework_hint(classname: str) -> str:
    lowered = classname.lower()
    if "playwright" in lowered or classname.startswith("e2e."):
        return "playwright"
    if "flutter" in lowered or "dart" in lowered:
        return "flutter/dart"
    if classname.startswith("tests.") or "." in classname:
        return "pytest/python"
    return ""


def parse_junit_xml(text: str, source: str, report: Report) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        report.add_not_found(f"junit xml parse ({source})")
        return

    failures: list[str] = []
    errors: list[str] = []
    skipped: list[str] = []
    failure_blobs: list[str] = []
    failing_testcases: list[ET.Element] = []
    first_classname = ""
    collection_errors: list[str] = []
    try:
        for elem in _bounded_xml_iter(root):
            tag = elem.tag.split("}")[-1]
            if tag == "testcase":
                testcase = elem
                label = _junit_case_label(testcase)
                for child in testcase:
                    child_tag = child.tag.split("}")[-1]
                    if child_tag == "failure":
                        if not first_classname:
                            first_classname = (testcase.attrib.get("classname") or "").strip()
                        failing_testcases.append(testcase)
                        blob = _junit_failure_blob(testcase, child)
                        failure_blobs.append(blob)
                        failures.append(_junit_snippet(child, testcase))
                    elif child_tag == "error":
                        if not first_classname:
                            first_classname = (testcase.attrib.get("classname") or "").strip()
                        failing_testcases.append(testcase)
                        blob = _junit_failure_blob(testcase, child)
                        failure_blobs.append(blob)
                        errors.append(_junit_snippet(child, testcase))
                        if _junit_is_pytest_collection_failure(child, blob):
                            msg = (child.attrib.get("message") or "").strip()
                            if msg:
                                collection_errors.append(
                                    _junit_flatten_for_report(f"{msg}\n{blob[:200]}")
                                )
                            else:
                                collection_errors.append(label)
                    elif child_tag == "skipped":
                        msg = sanitize_report_text((child.attrib.get("message") or "").strip())
                        body = sanitize_report_text((child.text or "").strip())
                        if msg:
                            skipped.append(msg[:300])
                        elif body:
                            skipped.append(body.splitlines()[0][:300])
                        else:
                            skipped.append("skipped")
        collection_errors.extend(_junit_testsuite_collection_errors(root))
    except ValueError:
        report.add_found(f"junit xml truncated (tag limit) ({source})")

    if failures:
        report.add_found(f"junit failures ({source}): {len(failures)}")
        report.add_found(f"junit failure (first) ({source}): {failures[0]}")
        if len(failures) > 1:
            report.add_found(f"junit failure (last) ({source}): {failures[-1]}")
    else:
        report.add_not_found(f"junit failures ({source})")

    if errors:
        report.add_found(f"junit errors ({source}): {len(errors)}")
        if errors:
            report.add_found(f"junit error (first) ({source}): {errors[0]}")
    else:
        report.add_not_found(f"junit errors ({source})")

    if skipped:
        report.add_found(f"junit skipped ({source}): {len(skipped)}")
        report.add_found(f"junit skipped (first) ({source}): {skipped[0]}")
    else:
        report.add_not_found(f"junit skipped ({source})")

    primary_blob = failure_blobs[0] if failure_blobs else ""
    if first_classname:
        report.add_found(f"junit testcase classname (first) ({source}): {first_classname[:200]}")
        fw = _junit_framework_hint(first_classname)
        if fw:
            report.add_hint(f"junit framework guess ({source}): {fw}")

    if primary_blob:
        exc_type = _junit_pytest_exception_type(primary_blob)
        if exc_type:
            report.add_found(f"junit pytest exception type ({source}): {exc_type}")
        else:
            report.add_not_found(f"junit pytest exception type ({source})")
        assert_hint = _junit_pytest_assert_hint(primary_blob)
        if assert_hint:
            report.add_hint(f"junit pytest assert line ({source}): {assert_hint}")
        diff_hint = _junit_diff_hint(primary_blob)
        if diff_hint:
            report.add_hint(f"junit assertion diff ({source}): {diff_hint}")
        if "Timeout" in primary_blob or "timed out" in primary_blob.lower():
            report.add_hint(f"junit timeout signal ({source}): possible timeout in failure text")
        dart_err = _junit_dart_error_line(primary_blob)
        if dart_err:
            report.add_found(f"junit dart error (first) ({source}): {dart_err}")
        else:
            report.add_not_found(f"junit dart error (first) ({source})")
        syserr = ""
        if failing_testcases:
            syserr = _junit_child_text(failing_testcases[0], "system-err")
        if syserr:
            report.add_found(
                f"junit system-err (first line) ({source}): {syserr.splitlines()[0][:300]}"
            )
        else:
            report.add_not_found(f"junit system-err ({source})")

    if collection_errors:
        report.add_found(f"junit collection errors ({source}): {len(collection_errors)}")
        report.add_found(f"junit collection error (first) ({source}): {collection_errors[0][:300]}")
    else:
        report.add_not_found(f"junit collection errors ({source})")

    if failures or errors:
        sample = failures[0] if failures else errors[0]
        report.add_hint(f"web search keywords ({source}): {sample[:400]}")


def _has_syntax_error_line(text: str) -> bool:
    if _text_has_node_stack_frames(text) and "Traceback (most recent call last):" not in text:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if SYNTAX_ERROR_LINE_RE.match(stripped) and not _is_node_syntax_error_line(stripped):
            return True
    return False


def _is_node_syntax_error_line(stripped: str) -> bool:
    m = SYNTAX_ERROR_LINE_RE.match(stripped)
    if not m:
        return False
    msg = (m.group("msg") or "").strip()
    return bool(NODE_SYNTAX_PATH_RE.search(msg) or msg.startswith("/"))



def _has_python_failure_signal(text: str) -> bool:
    if "Traceback (most recent call last):" in text or any(m in text for m in PYTHON_CHAIN_MARKERS):
        return True
    if EXCEPTION_GROUP_TB_START_RE.search(text):
        return True
    if _has_syntax_error_line(text):
        return True
    if "ExceptionGroup" in text or "BaseExceptionGroup" in text:
        return True
    return False


def _hint_best_effort_compilers(text: str, report: Report) -> bool:
    lines = text.splitlines()
    structured = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if DENO_WRAPPED_ERROR_RE.match(stripped):
            continue
        m_rust = RUSTC_ERROR_CODE_RE.match(stripped)
        if m_rust:
            loc = ""
            if i + 1 < len(lines):
                m_arrow = RUSTC_ARROW_RE.match(lines[i + 1].strip())
                if m_arrow:
                    loc = f" {m_arrow.group('file')}:{m_arrow.group('line')}:{m_arrow.group('col')}"
            report.add_hint(f"rustc error (best effort):{loc} {stripped[:360]}")
            structured = True
            continue
        m_gcc = GCC_ERROR_RE.match(stripped)
        if m_gcc:
            report.add_hint(f"gcc error (best effort): {stripped[:400]}")
            structured = True
            continue
        m_dart = DART_COMPILER_ERROR_RE.match(stripped)
        if m_dart:
            report.add_hint(
                f"dart error (best effort): {m_dart.group('file')}:"
                f"{m_dart.group('line')}: {m_dart.group('rest')[:300]}"
            )
            structured = True
            continue
        if COMPILER_LOWERCASE_ERROR_RE.match(stripped):
            report.add_hint(f"compiler error line (not node): {stripped[:400]}")
    return structured


def _should_parse_node_stack(text: str) -> bool:
    if not _looks_like_node_failure(text):
        return False
    if not _has_python_failure_signal(text):
        return True
    return bool(
        re.search(r"\n\s*at .+:\d+", text)
        or NPM_ERR_RE.search(text)
        or NODE_ERR_ASSERTION_RE.search(text)
        or DENO_WRAPPED_ERROR_RE.search(text)
        or JEST_FAILURE_BULLET_RE.search(text)
        or NODE_ERR_CODE_RE.search(text)
    )


def _looks_like_node_failure(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if COMPILER_LOWERCASE_ERROR_RE.match(stripped):
            continue
        if _is_node_syntax_error_line(stripped):
            return True
        if NODE_AT_LOOSE_RE.match(line) or NODE_AT_FRAME_RE.match(line):
            return True
        if SYNTAX_ERROR_LINE_RE.match(stripped):
            continue
        if NODE_ERROR_BRACKET_CODE_RE.match(stripped):
            return True
        if NODE_ERROR_HEADLINE_RE.match(stripped) or NODE_CORE_ERROR_RE.match(stripped):
            return True
        if NPM_ERR_RE.match(stripped) or JEST_FAILURE_BULLET_RE.match(stripped):
            return True
    if NODE_ERR_CODE_RE.search(text):
        return True
    if NODE_BRACKET_REJECTION_RE.search(text):
        return True
    return bool(re.search(r"\n\s*at .+:\d+:\d+", text))


def embedded_failure_signals(text: str) -> bool:
    if not text.strip():
        return False
    if _has_python_failure_signal(text) or _looks_like_node_failure(text):
        return True
    return any(COMPILER_LOWERCASE_ERROR_RE.match(ln.strip()) for ln in text.splitlines())


def detect_and_parse_log(text: str, report: Report, label_suffix: str = "") -> None:
    if not text.strip():
        report.add_not_found("log text (empty)")
        return
    label = label_suffix.strip()
    parsed = False
    if _has_python_failure_signal(text):
        parse_python_traceback(text, report, label=label)
        parsed = True
    if _should_parse_node_stack(text):
        parse_node_stack(text, report)
        parsed = True
    compiler_structured = _hint_best_effort_compilers(text, report)
    if not parsed and not compiler_structured:
        report.add_not_found("recognized failure pattern in log/stderr")



def collect_runtime_facts(report: Report, root: Path | None) -> None:
    report.add_found(f"python version: {sys.version.split()[0]} ({sys.implementation.name})")

    node = shutil.which("node")
    if node:
        try:
            out = subprocess.run(
                [node, "-v"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ver = (out.stdout or out.stderr or "").strip()
            if ver:
                report.add_found(f"node version: {ver}")
            else:
                report.add_not_found("node version")
        except (OSError, subprocess.TimeoutExpired):
            report.add_not_found("node version")
    else:
        report.add_not_found("node binary")

    gh = shutil.which("gh")
    if gh:
        try:
            out = subprocess.run(
                [gh, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            first = (out.stdout or "").splitlines()
            if first:
                report.add_found(f"gh version: {first[0].strip()}")
            else:
                report.add_not_found("gh version")
        except (OSError, subprocess.TimeoutExpired):
            report.add_not_found("gh version")
    else:
        report.add_not_found("gh binary")

    search_root = root if root is not None else Path.cwd()
    present = [name for name in LOCK_FILES if (search_root / name).is_file()]
    if present:
        report.add_found(f"lock files present: {', '.join(sorted(present))}")
    else:
        report.add_not_found("lock files in project root")

    names = sorted(os.environ)
    report.add_found(f"environment variable count: {len(names)}")
    if names:
        sample = names[:MAX_ENV_NAMES_LISTED]
        suffix = "" if len(names) <= MAX_ENV_NAMES_LISTED else ", ..."
        report.add_found(f"environment variable names: {', '.join(sample)}{suffix}")


def triage(
    *,
    exit_code: int | None,
    stderr_text: str | None,
    log_text: str | None,
    actions_log_text: str | None = None,
    junit_paths: Sequence[str],
    project_root: Path | None,
) -> Report:
    report = Report()

    if exit_code is not None:
        report.add_found(f"exit code: {exit_code}")
        if exit_code == 0:
            report.add_hint("exit code is zero; only a minimal report unless failure evidence is supplied")
    else:
        report.add_not_found("exit code")

    texts: list[tuple[str, str]] = []
    if stderr_text is not None:
        texts.append(("stderr", stderr_text))
    if log_text is not None:
        texts.append(("log", log_text))

    if not texts and not junit_paths and actions_log_text is None:
        if exit_code == 0:
            report.add_not_found("failure evidence (none supplied)")
        else:
            report.add_not_found("failure evidence (no stderr, log, junit, or actions log)")

    for label, text in texts:
        text = strip_terminal_controls(text)
        if not text.strip():
            report.add_not_found(f"{label} text (empty)")
            continue
        detect_and_parse_log(text, report)

    if actions_log_text is not None:
        from err_o_roadrunner.actions_log import parse_actions_log

        if not actions_log_text.strip():
            report.add_not_found("actions log text (empty)")
        else:
            parse_actions_log(actions_log_text, "actions-log", report)

    for raw_path in junit_paths:
        path = Path(raw_path)
        xml_text = safe_read_text(path, report)
        if xml_text is None:
            continue
        parse_junit_xml(xml_text, str(path), report)

    collect_runtime_facts(report, project_root)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="err-o-roadrunner",
        description="Read-only triage after a failure (stdlib only, no network).",
    )
    parser.add_argument(
        "--exit-code",
        type=int,
        default=None,
        help="Process exit code from the failed command.",
    )
    parser.add_argument(
        "--stderr",
        metavar="PATH",
        help='Stderr capture file, or "-" for stdin (after other flags).',
    )
    parser.add_argument(
        "--log",
        metavar="PATH",
        help="Log file path (same safety rules as --stderr).",
    )
    parser.add_argument(
        "--actions-log",
        metavar="PATH",
        help="Downloaded GitHub Actions job log (same safety rules as --stderr).",
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        metavar="PATH",
        help="JUnit XML file (repeatable).",
    )
    parser.add_argument(
        "--project-root",
        metavar="DIR",
        default=".",
        help="Directory for lock-file detection (default: current directory).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            return EXIT_OK
        return EXIT_USAGE

    report = Report()
    stderr_text = read_stream_arg(args.stderr, report, "stderr")
    log_text = read_stream_arg(args.log, report, "log")
    actions_log_text = read_stream_arg(args.actions_log, report, "actions-log")

    root: Path | None
    try:
        root = Path(args.project_root).resolve()
        if not root.is_dir():
            report.add_not_found(f"project root: {args.project_root}")
            root = None
    except OSError:
        report.add_not_found(f"project root: {args.project_root}")
        root = None

    main_report = triage(
        exit_code=args.exit_code,
        stderr_text=stderr_text,
        log_text=log_text,
        actions_log_text=actions_log_text,
        junit_paths=args.junit,
        project_root=root,
    )
    main_report.extend(report)

    for line in main_report.lines():
        print(line)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
