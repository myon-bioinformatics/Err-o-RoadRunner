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
    re.compile(r"\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?"),
    re.compile(r"\x1bP[^\x1b\n]*(?:\x1b\\)?"),
    re.compile(r"\x1b\[" + _CSI_BODY),
    re.compile(r"\x9b" + _CSI_BODY),
    re.compile(r"\u009d[^\u009c\n]*\u009c?"),
    re.compile(r"\u0090[^\u009c\n]*\u009c?"),
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
    r"^(?P<head>(?:[A-Za-z_][\w]*Error|Error|UnhandledPromiseRejectionWarning|Unhandled\s+rejection(?:\s+\([^)]+\))?))\s*:\s*(?P<rest>.*)$",
    re.IGNORECASE,
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
    if name in PYTHON_BUILTIN_EXCEPTIONS:
        return True
    return bool(
        re.fullmatch(
            r"[A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit)",
            name,
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


def _split_python_trace_blocks(text: str) -> list[list[str]]:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("Traceback (most recent call last):"):
            if current:
                blocks.append(current)
            current = [line]
        elif line in PYTHON_CHAIN_MARKERS:
            if current:
                blocks.append(current)
            current = []
        elif current:
            current.append(line)
        elif SYNTAX_ERROR_LINE_RE.match(line.strip()):
            blocks.append([line])
    if current:
        blocks.append(current)
    return blocks


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
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("^"):
            continue
        tail_lines.append(stripped)

    for stripped in tail_lines:
        parsed = parse_exception_summary_line(stripped)
        if parsed:
            exc_type, exc_msg = parsed
            continue
        if re.match(r"^[A-Za-z_][\w.]*:", stripped):
            report.add_hint(f"{_report_key(label, 'trailing colon line')}: {stripped}")
        elif re.match(r"^[A-Za-z_][\w.]*$", stripped):
            report.add_hint(f"{_report_key(label, 'uncertain traceback line')}: {stripped}")

    if exc_type:
        report.add_found(f"{_report_key(label, 'python exception type')}: {exc_type}")
        if exc_msg:
            report.add_found(f"{_report_key(label, 'python exception message')}: {exc_msg}")
        else:
            report.add_not_found(_report_key(label, "python exception message"))
    else:
        report.add_not_found(_report_key(label, "python exception type"))

    if frames:
        report.add_found(f"{_report_key(label, 'python traceback frame (first)')}: {frames[0].format()}")
        report.add_found(f"{_report_key(label, 'python traceback frame (last)')}: {frames[-1].format()}")
        report.add_found(f"{_report_key(label, 'python traceback frame count')}: {len(frames)}")
    else:
        report.add_not_found(_report_key(label, "python stack frames"))

    return exc_type, exc_msg


def parse_python_traceback(text: str, report: Report) -> None:
    blocks = _split_python_trace_blocks(text)
    if not blocks:
        report.add_not_found("python traceback")
        return

    if len(blocks) > 1:
        report.add_found(f"python chained traceback blocks: {len(blocks)}")

    if len(blocks) == 1:
        block_labels = [""]
    else:
        block_labels = ["primary"] + [f"chained-{i}" for i in range(1, len(blocks))]
    last_type = ""
    last_msg = ""
    for block, label in zip(blocks, block_labels):
        exc_type, exc_msg = _parse_one_python_block(block, report, label)
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


def _parse_node_at_line(line: str) -> StackFrame | None:
    m = NODE_AT_FRAME_RE.match(line)
    if not m:
        return None
    func = (m.group("func") or "").strip()
    return StackFrame(file=m.group("loc"), line=int(m.group("line")), func=func)


def parse_node_stack(text: str, report: Report) -> None:
    frames: list[StackFrame] = []
    error_lines: list[str] = []
    err_codes: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        m_head = NODE_ERROR_HEADLINE_RE.match(stripped)
        if m_head:
            rest = (m_head.group("rest") or "").strip()
            headline = m_head.group("head")
            if rest:
                error_lines.append(f"{headline}: {rest}")
            else:
                error_lines.append(f"{headline}:")
        m_bracket = NODE_BRACKET_REJECTION_RE.match(stripped)
        if m_bracket:
            error_lines.append(f"[UnhandledPromiseRejection: {m_bracket.group('rest')}]")
        m_code = NODE_ERR_CODE_RE.search(stripped)
        if m_code:
            err_codes.append(m_code.group(1))
        frame = _parse_node_at_line(line)
        if frame:
            frames.append(frame)

    if error_lines:
        report.add_found(f"node error line: {error_lines[0][:500]}")
    else:
        report.add_not_found("node error headline")

    if err_codes:
        report.add_found(f"node error code: {err_codes[0]}")

    if frames:
        report.add_found(f"node stack frame (first): {frames[0].format()}")
        report.add_found(f"node stack frame (last): {frames[-1].format()}")
        report.add_found(f"node stack frame count: {len(frames)}")
    else:
        report.add_not_found("node stack frames")

    if error_lines or err_codes:
        hint = error_lines[0] if error_lines else err_codes[0]
        report.add_hint(f"web search keywords: {hint[:400]}")


def _bounded_xml_iter(root: ET.Element) -> Iterator[ET.Element]:
    count = 0
    for elem in root.iter():
        count += 1
        if count > MAX_XML_TAGS:
            raise ValueError("xml too large")
        yield elem


def _junit_snippet(elem: ET.Element) -> str:
    msg = sanitize_report_text((elem.attrib.get("message") or "").strip())
    body = sanitize_report_text((elem.text or "").strip())
    if msg:
        return msg[:300]
    if body:
        return body.splitlines()[0][:300]
    return "failure"


def parse_junit_xml(text: str, source: str, report: Report) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        report.add_not_found(f"junit xml parse ({source})")
        return

    failures: list[str] = []
    errors: list[str] = []
    skipped: list[str] = []
    try:
        for elem in _bounded_xml_iter(root):
            tag = elem.tag.split("}")[-1]
            if tag == "failure":
                failures.append(_junit_snippet(elem))
            elif tag == "error":
                errors.append(_junit_snippet(elem))
            elif tag == "skipped":
                msg = sanitize_report_text((elem.attrib.get("message") or "").strip())
                body = sanitize_report_text((elem.text or "").strip())
                if msg:
                    skipped.append(msg[:300])
                elif body:
                    skipped.append(body.splitlines()[0][:300])
                else:
                    skipped.append("skipped")
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
    else:
        report.add_not_found(f"junit errors ({source})")

    if skipped:
        report.add_found(f"junit skipped ({source}): {len(skipped)}")
        report.add_found(f"junit skipped (first) ({source}): {skipped[0]}")
    else:
        report.add_not_found(f"junit skipped ({source})")

    if failures or errors:
        sample = failures[0] if failures else errors[0]
        report.add_hint(f"web search keywords ({source}): {sample[:400]}")


def _has_syntax_error_line(text: str) -> bool:
    return any(SYNTAX_ERROR_LINE_RE.match(line.strip()) for line in text.splitlines())


def _looks_like_node_failure(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if SYNTAX_ERROR_LINE_RE.match(stripped):
            continue
        if NODE_ERROR_HEADLINE_RE.match(stripped):
            return True
    if NODE_ERR_CODE_RE.search(text):
        return True
    if NODE_BRACKET_REJECTION_RE.search(text):
        return True
    return bool(re.search(r"\n\s*at .+:\d+:\d+", text))


def detect_and_parse_log(text: str, report: Report) -> None:
    if not text.strip():
        report.add_not_found("log text (empty)")
        return
    if "Traceback (most recent call last):" in text or any(m in text for m in PYTHON_CHAIN_MARKERS):
        parse_python_traceback(text, report)
    elif _has_syntax_error_line(text):
        parse_syntax_error(text, report)
    elif _looks_like_node_failure(text):
        parse_node_stack(text, report)
    else:
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

    if not texts and not junit_paths:
        if exit_code == 0:
            report.add_not_found("failure evidence (none supplied)")
        else:
            report.add_not_found("failure evidence (no stderr, log, or junit)")

    for label, text in texts:
        text = strip_terminal_controls(text)
        if not text.strip():
            report.add_not_found(f"{label} text (empty)")
            continue
        detect_and_parse_log(text, report)

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
        junit_paths=args.junit,
        project_root=root,
    )
    main_report.extend(report)

    for line in main_report.lines():
        print(line)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
