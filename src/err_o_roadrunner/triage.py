"""Read-only failure triage CLI (stdlib only)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# Stable exit codes (documented in README).
EXIT_OK = 0
EXIT_USAGE = 2

MAX_READ_BYTES = 10 * 1024 * 1024
MAX_XML_TAGS = 100_000
MAX_FRAMES = 2_000
MAX_ENV_NAMES_LISTED = 40

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PYTHON_FRAME_RE = re.compile(
    r'^  File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.*))?$'
)
PYTHON_EXCEPTION_RE = re.compile(
    r"^(?P<exc>[A-Za-z_][\w.]*)(?::\s*(?P<msg>.*))?$"
)
NODE_STACK_RE = re.compile(
    r"^\s*at (?:async )?(?P<func>.*?)? ?\((?P<loc>[^:]+):(?P<line>\d+):(?P<col>\d+)\)\s*$"
)
NODE_STACK_SIMPLE_RE = re.compile(
    r"^\s*at (?P<loc>[^\s(]+):(?P<line>\d+):(?P<col>\d+)\s*$"
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
        self.found.append(line)

    def add_not_found(self, line: str) -> None:
        self.not_found.append(line)

    def add_hint(self, line: str) -> None:
        self.hints.append(line)

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


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def safe_read_text(path: Path, report: Report) -> str | None:
    """Read a user path safely; never follow symlinks outside resolution checks."""
    try:
        raw = path.expanduser()
        if ".." in path.parts:
            report.add_not_found(f"unsafe path (path traversal rejected): {path}")
            return None
        if not raw.is_file():
            report.add_not_found(f"file not readable: {path}")
            return None
        size = raw.stat().st_size
        if size > MAX_READ_BYTES:
            report.add_found(f"input truncated (size {size} > {MAX_READ_BYTES}): {path}")
        with raw.open("rb") as fh:
            data = fh.read(MAX_READ_BYTES)
    except OSError as exc:
        report.add_not_found(f"file not readable: {path} ({type(exc).__name__})")
        return None
    text = data.decode("utf-8", errors="replace")
    if "\x00" in text:
        report.add_found(f"binary or nul bytes present in: {path}")
    return strip_ansi(text)


def read_stream_arg(value: str | None, report: Report, label: str) -> str | None:
    if value is None:
        return None
    if value == "-":
        try:
            data = sys.stdin.buffer.read(MAX_READ_BYTES)
        except OSError as exc:
            report.add_not_found(f"{label} stdin ({type(exc).__name__})")
            return None
        text = data.decode("utf-8", errors="replace")
        return strip_ansi(text)
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


def parse_python_traceback(text: str, report: Report) -> None:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("Traceback (most recent call last):"):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    if not blocks:
        report.add_not_found("python traceback")
        return

    tb = blocks[-1]
    frames: list[StackFrame] = []
    exc_type = ""
    exc_msg = ""
    for line in tb[1:]:
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
            continue
        m = PYTHON_EXCEPTION_RE.match(line.strip())
        if m and m.group("exc"):
            exc_type = m.group("exc")
            exc_msg = (m.group("msg") or "").strip()

    if exc_type:
        report.add_found(f"python exception type: {exc_type}")
        if exc_msg:
            report.add_found(f"python exception message: {exc_msg}")
        else:
            report.add_not_found("python exception message")
    else:
        report.add_not_found("python exception type")

    if frames:
        report.add_found(f"python traceback frame (first): {frames[0].format()}")
        report.add_found(f"python traceback frame (last): {frames[-1].format()}")
        report.add_found(f"python traceback frame count: {len(frames)}")
    else:
        report.add_not_found("python stack frames")

    if exc_type or exc_msg:
        keywords = " ".join(part for part in (exc_type, exc_msg) if part)
        report.add_hint(f"web search keywords: {keywords[:500]}")


def parse_node_stack(text: str, report: Report) -> None:
    frames: list[StackFrame] = []
    error_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Error:") or stripped.startswith("TypeError:") or stripped.startswith(
            "ReferenceError:"
        ):
            error_lines.append(stripped)
        m = NODE_STACK_RE.match(line) or NODE_STACK_SIMPLE_RE.match(line)
        if m:
            loc = m.group("loc")
            func = (m.groupdict().get("func") or "").strip()
            frames.append(
                StackFrame(
                    file=loc,
                    line=int(m.group("line")),
                    func=func,
                )
            )

    if error_lines:
        report.add_found(f"node error line: {error_lines[0][:500]}")
    else:
        report.add_not_found("node error headline")

    if frames:
        report.add_found(f"node stack frame (first): {frames[0].format()}")
        report.add_found(f"node stack frame (last): {frames[-1].format()}")
        report.add_found(f"node stack frame count: {len(frames)}")
    else:
        report.add_not_found("node stack frames")


def _bounded_xml_iter(root: ET.Element) -> Iterator[ET.Element]:
    count = 0
    for elem in root.iter():
        count += 1
        if count > MAX_XML_TAGS:
            raise ValueError("xml too large")
        yield elem


def parse_junit_xml(text: str, source: str, report: Report) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        report.add_not_found(f"junit xml parse ({source})")
        return

    failures: list[str] = []
    errors: list[str] = []
    try:
        for elem in _bounded_xml_iter(root):
            tag = elem.tag.split("}")[-1]
            if tag == "failure":
                msg = elem.attrib.get("message", "").strip()
                body = (elem.text or "").strip()
                snippet = msg or body.splitlines()[0] if body else "failure"
                failures.append(snippet[:300])
            elif tag == "error":
                msg = elem.attrib.get("message", "").strip()
                errors.append(msg[:300] if msg else "error")
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

    if failures or errors:
        sample = failures[0] if failures else errors[0]
        report.add_hint(f"web search keywords ({source}): {sample[:400]}")


def detect_and_parse_log(text: str, report: Report) -> None:
    if not text.strip():
        report.add_not_found("log text (empty)")
        return
    if "Traceback (most recent call last):" in text:
        parse_python_traceback(text, report)
    elif re.search(r"\n\s*at .+:\d+:\d+", text) or text.strip().startswith("Error:"):
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
        text = strip_ansi(text)
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
