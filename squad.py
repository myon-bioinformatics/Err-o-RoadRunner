"""Parallel error squads for sentinel GO escalation (stdlib only)."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from junit_redact import redact_text

DEFAULT_SQUAD_TIMEOUT = 10.0
DEFAULT_SQUAD_BUDGET = 30.0
DEFAULT_SQUAD_WORKERS = 3

SQUAD_CODE = "code"
SQUAD_HISTORY = "history"
SQUAD_WEB = "web"
ALL_SQUADS = (SQUAD_CODE, SQUAD_HISTORY, SQUAD_WEB)
DEFAULT_SQUADS = (SQUAD_CODE, SQUAD_HISTORY)

GITHUB_SEARCH_API = "https://api.github.com/search/issues"

_PYTEST_VARIANT_RE = re.compile(r"junit-(py3\.\d+)", re.I)
_PYTHON_FRAME_RE = re.compile(
    r'^  File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.*))?$'
)
_PYTEST_FILE_LINE_RE = re.compile(r"^(?P<file>[^\s:]+\.py):(?P<line>\d+):\s*$")
_PYTEST_E_EXC_LINE_RE = re.compile(
    r"^\s*E\s+(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Failed|Timeout|Exit))(?:\s*:|$)"
)
_JUNIT_MSG_EXCEPTION_RE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Failed|Timeout|Exit))(?:\s*:\s*|$)"
)
_JUNIT_BARE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][\w.]+$")
_SIGNATURE_PATH_RE = re.compile(
    r"(?:/[\w.~$-]+)+|(?:[A-Za-z]:\\[\w.~$-\\]+)+|<repo>[\w./-]*"
)
_SIGNATURE_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SIGNATURE_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_SIGNATURE_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_ASSERT_EQ_RE = re.compile(
    r"(?:AssertionError:\s*)?assert\s+(.+?)\s+==\s+(.+?)\s*$",
    re.I,
)
_ASSERT_IS_NONE_RE = re.compile(
    r"(?:AssertionError:\s*)?assert\s+(.+?)\s+is\s+(None|not\s+None)\s*$",
    re.I,
)
_METADATA_HINT_RE = re.compile(r"(version|metadata|pyproject)", re.I)
_OPERAND_MAX_LEN = 80
_DID_YOU_MEAN_SUFFIX_RE = re.compile(
    r"\.\s*Did you mean:\s*(?:'[^']*'|\"[^\"]*\"|\S.*)?$",
    re.I,
)
_MODULE_NOT_FOUND_RE = re.compile(
    r"No module named ['\"](?P<mod>[^'\"]+)['\"]", re.I
)
_ATTRIBUTE_ERROR_MOD_RE = re.compile(
    r"(?:AttributeError:\s*)?<module ['\"](?P<mod>[^'\"]+)['\"]", re.I
)

SquadRunner = Callable[..., dict[str, Any]]


@dataclass
class SquadContext:
    records: list[dict[str, Any]]
    project_root: Path | None
    junit_paths: tuple[str, ...]
    corpus_dirs: tuple[str, ...]
    offline: bool
    web_endpoints: tuple[str, ...]
    timeout: float
    project_root_explicit: bool = False


def parse_squads_flag(value: str) -> frozenset[str]:
    raw = (value or "").strip().lower()
    if not raw or raw == "none":
        return frozenset()
    if raw == "all":
        return frozenset(ALL_SQUADS)
    names = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = names - set(ALL_SQUADS)
    if unknown:
        raise ValueError(f"invalid --squads names: {', '.join(sorted(unknown))}")
    return frozenset(names)


def _cap_redacted_operand(fragment: str) -> str:
    text = redact_text(fragment.strip())
    if len(text) > _OPERAND_MAX_LEN:
        return text[: _OPERAND_MAX_LEN - 1] + "…"
    return text


def _assert_line_candidates(message: str, blob: str, first_line: str) -> list[str]:
    lines: list[str] = []
    if first_line.strip():
        lines.append(first_line.strip())
    msg = (message or "").strip()
    if msg:
        for part in msg.splitlines()[:3]:
            part = part.strip()
            if part and part not in lines:
                lines.append(part)
    for raw in blob.splitlines():
        stripped = raw.strip()
        if "assert " not in stripped:
            continue
        if stripped.startswith("E "):
            stripped = stripped[2:].strip()
        if stripped.startswith("AssertionError:"):
            stripped = stripped.split(":", 1)[1].strip()
        if stripped and stripped not in lines:
            lines.append(stripped)
    return lines


def extract_assert_operands(message: str, blob: str, first_line: str) -> tuple[str | None, str | None]:
    """Parse pytest assert left/right operands (redacted, capped) from failure text."""
    for line in _assert_line_candidates(message, blob, first_line):
        match = _ASSERT_EQ_RE.search(line)
        if match:
            return _cap_redacted_operand(match.group(1)), _cap_redacted_operand(match.group(2))
        match = _ASSERT_IS_NONE_RE.search(line)
        if match:
            return _cap_redacted_operand(match.group(1)), _cap_redacted_operand(match.group(2))
    return None, None


def normalize_signature(message: str) -> str:
    text = message.strip()
    text = _DID_YOU_MEAN_SUFFIX_RE.sub("", text).strip()
    text = _SIGNATURE_PATH_RE.sub("<path>", text)
    text = _SIGNATURE_UUID_RE.sub("<id>", text)
    text = _SIGNATURE_HEX_RE.sub("<id>", text)
    text = _SIGNATURE_NUMBER_RE.sub("<n>", text)
    text = " ".join(text.split())
    if len(text) > 160:
        text = text[:159] + "…"
    return text or "generic"


def _variant_from_pytest_junit(path: str) -> str | None:
    match = _PYTEST_VARIANT_RE.search(path)
    if match:
        return match.group(1).lower()
    return None


VariantParser = Callable[[str], str | None]

VARIANT_PARSERS: list[VariantParser] = [_variant_from_pytest_junit]


def variant_from_artifact(path: str) -> str:
    for parser in VARIANT_PARSERS:
        variant = parser(path)
        if variant:
            return variant
    return "unknown"


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1]


def _failure_blob(testcase: ET.Element, elem: ET.Element) -> str:
    parts: list[str] = []
    msg = (elem.attrib.get("message") or "").strip()
    body = (elem.text or "").strip()
    if msg and body:
        if body == msg or msg in body:
            parts.append(body)
        elif body in msg:
            parts.append(msg)
        else:
            parts.extend([msg, body])
    elif msg:
        parts.append(msg)
    elif body:
        parts.append(body)
    for extra in ("system-out", "system-err"):
        for child in testcase:
            if _local_tag(child) == extra and (child.text or "").strip():
                parts.append((child.text or "").strip())
    return "\n".join(parts)


def _case_test_id(testcase: ET.Element) -> str:
    classname = (testcase.attrib.get("classname") or "").strip()
    name = (testcase.attrib.get("name") or "").strip()
    if classname and name:
        return f"{classname}::{name}"
    return name or classname or "testcase"


def _message_first_line(message: str, blob: str) -> str:
    msg = (message or "").strip()
    first = msg.splitlines()[0].strip() if msg else ""
    if first:
        return first
    for line in blob.splitlines():
        if line.strip():
            return line.strip()
    return ""


ExceptionParser = Callable[[str, str, str], str | None]


def _exc_from_pytest_e_lines(message: str, blob: str, first_line: str) -> str | None:
    for line in blob.splitlines():
        match = _PYTEST_E_EXC_LINE_RE.match(line)
        if match:
            return match.group("exc")
    return None


def _exc_from_assert_prefix(message: str, blob: str, first_line: str) -> str | None:
    if first_line.startswith("assert "):
        return "AssertionError"
    return None


def _exc_from_message_attr(message: str, blob: str, first_line: str) -> str | None:
    msg = (message or "").strip()
    if not msg:
        return None
    head = msg.splitlines()[0]
    match = _JUNIT_MSG_EXCEPTION_RE.match(head)
    if match:
        return match.group(1)
    if _JUNIT_BARE_EXCEPTION_RE.match(head.strip()) and head.strip()[0].isupper():
        return head.strip()
    return None


def _exc_from_first_line(message: str, blob: str, first_line: str) -> str | None:
    match = _JUNIT_MSG_EXCEPTION_RE.match(first_line)
    if match:
        return match.group(1)
    return None


EXCEPTION_PARSERS: list[ExceptionParser] = [
    _exc_from_pytest_e_lines,
    _exc_from_assert_prefix,
    _exc_from_message_attr,
    _exc_from_first_line,
]


def _exception_type(message: str, blob: str, first_line: str) -> str:
    for parser in EXCEPTION_PARSERS:
        exc = parser(message, blob, first_line)
        if exc:
            return exc
    return "Unknown"


def _parse_message_text(message: str, blob: str) -> str:
    first = _message_first_line(message, blob)
    match = _JUNIT_MSG_EXCEPTION_RE.match(first)
    if match and first.startswith(f"{match.group(1)}:"):
        rest = first[match.end() :].strip()
        return rest or first
    return first or blob.splitlines()[0].strip() if blob else ""


LanguageDetector = Callable[[Mapping[str, Any]], str | None]


def _detect_language_python(record: Mapping[str, Any]) -> str | None:
    variant = str(record.get("variant") or "")
    if variant.startswith("py3."):
        return "python"
    artifact = str(record.get("artifact") or "")
    if _PYTEST_VARIANT_RE.search(artifact):
        return "python"
    return None


LANGUAGE_DETECTORS: list[LanguageDetector] = [_detect_language_python]


def _record_language(record: Mapping[str, Any]) -> str | None:
    for detector in LANGUAGE_DETECTORS:
        language = detector(record)
        if language:
            return language
    return None


def _traceback_location(blob: str) -> tuple[str, int | None]:
    for line in blob.splitlines():
        match = _PYTHON_FRAME_RE.match(line)
        if match:
            return match.group("file"), int(match.group("line"))
        match = _PYTEST_FILE_LINE_RE.match(line.strip())
        if match:
            return match.group("file"), int(match.group("line"))
    return "", None


def parse_junit_failure_records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        root = ET.fromstring(text)
    except (OSError, ET.ParseError):
        return []

    artifact = path.name
    variant = variant_from_artifact(artifact)
    records: list[dict[str, Any]] = []

    for testcase in root.iter():
        if _local_tag(testcase) != "testcase":
            continue
        for child in testcase:
            tag = _local_tag(child)
            if tag not in ("failure", "error"):
                continue
            blob = _failure_blob(testcase, child)
            message_attr = (child.attrib.get("message") or "").strip()
            first_line = _message_first_line(message_attr, blob)
            error_type = _exception_type(message_attr, blob, first_line)
            message = _parse_message_text(message_attr, blob)
            if not message and message_attr:
                match = _JUNIT_MSG_EXCEPTION_RE.match(message_attr.splitlines()[0])
                message = (
                    message_attr.split(":", 1)[1].strip()
                    if match and ":" in message_attr
                    else message_attr
                )
            source_file, line_no = _traceback_location(blob)
            test_id = _case_test_id(testcase)
            signature = normalize_signature(message or error_type)
            assert_left, assert_right = extract_assert_operands(message_attr, blob, first_line)
            draft = {
                "error_type": error_type,
                "message": message[:500],
                "signature": signature,
                "test_id": test_id,
                "source_file": source_file,
                "line": line_no,
                "variant": variant,
                "artifact": artifact,
            }
            if assert_left is not None:
                draft["assert_left"] = assert_left
            if assert_right is not None:
                draft["assert_right"] = assert_right
            draft["language"] = _record_language(draft)
            records.append(draft)
    return records


def collect_junit_paths(
    junit_paths: Sequence[str],
    corpus_dirs: Sequence[str],
) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in junit_paths:
        p = Path(raw)
        key = str(p.resolve()) if p.exists() else raw
        if key not in seen:
            seen.add(key)
            paths.append(p)
    for raw_dir in corpus_dirs:
        base = Path(raw_dir)
        if not base.is_dir():
            continue
        for xml in sorted(base.glob("*.xml")):
            key = str(xml.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(xml)
    return paths


def merge_failure_records(
    paths: Sequence[Path],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        for rec in parse_junit_failure_records(path):
            key = str(rec["test_id"])
            variant = rec.get("variant") or "unknown"
            if key in merged:
                entry = merged[key]
                variants = entry.setdefault("variants", [])
                if variant != "unknown" and variant not in variants:
                    variants.append(variant)
                if not entry.get("source_file") and rec.get("source_file"):
                    entry["source_file"] = rec["source_file"]
                    entry["line"] = rec.get("line")
            else:
                copy = dict(rec)
                copy["variants"] = [variant] if variant != "unknown" else []
                merged[key] = copy
    for entry in merged.values():
        entry["variants"] = sorted(set(entry.get("variants") or []))
    return list(merged.values())


def records_to_normalized_errors(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for idx, rec in enumerate(records):
        location: dict[str, str] = {}
        if rec.get("source_file"):
            location["loc"] = str(rec["source_file"])
        if rec.get("line") is not None:
            location["line"] = str(rec["line"])
        error_type = str(rec.get("error_type") or "generic")
        kind = "python_import" if error_type in ("ModuleNotFoundError", "ImportError") else "junit"
        if error_type == "AssertionError":
            kind = "assertion"
        language = rec.get("language")
        if not language:
            language = "python" if error_type not in ("Unknown", "generic") else "junit"
        errors.append(
            {
                "error_type": error_type,
                "language": str(language),
                "phase": "test",
                "signature": str(rec.get("signature") or "generic"),
                "location": location,
                "kind": kind,
                "first_seen": idx,
                "message": str(rec.get("message") or "")[:500],
                "source_line": str(rec.get("test_id") or ""),
                "test_id": str(rec.get("test_id") or ""),
                "variants": list(rec.get("variants") or []),
            }
        )
    return errors


def _repo_module_exists(project_root: Path | None, mod: str) -> bool:
    if not project_root or not mod:
        return False
    top = mod.split(".")[0]
    candidates = [
        project_root / f"{top}.py",
        project_root / top / "__init__.py",
    ]
    for rel in (top, *mod.split(".")):
        candidates.append(project_root / rel.replace(".", os.sep) / "__init__.py")
    return any(p.is_file() for p in candidates)


def _import_contamination_module(record: Mapping[str, Any]) -> str | None:
    message = str(record.get("message") or "")
    error_type = str(record.get("error_type") or "")
    mod = ""
    if error_type == "ModuleNotFoundError":
        match = _MODULE_NOT_FOUND_RE.search(message)
        if match:
            mod = match.group("mod").split(".")[0]
    elif error_type == "AttributeError":
        match = _ATTRIBUTE_ERROR_MOD_RE.search(message)
        if match:
            mod = match.group("mod").split(".")[-1]
    if mod and re.match(r"^[A-Za-z_]\w*$", mod):
        return mod
    return None


def _test_module_from_test_id(test_id: str) -> str:
    if "::" in test_id:
        return test_id.split("::", 1)[0]
    return test_id


def _import_related_error(record: Mapping[str, Any]) -> bool:
    return str(record.get("error_type") or "") in (
        "ModuleNotFoundError",
        "AttributeError",
        "ImportError",
    )


HypothesisRule = Callable[
    [Sequence[Mapping[str, Any]], Path | None, Sequence[str]],
    list[dict[str, Any]],
]


def rule_import_cascade(
    records: Sequence[Mapping[str, Any]],
    project_root: Path | None,
    squad_names: Sequence[str],
) -> list[dict[str, Any]]:
    if not records:
        return []
    explained: list[str] = []
    confidence = "medium"
    for rec in records:
        mod = _import_contamination_module(rec)
        if not mod:
            continue
        test_id = str(rec.get("test_id") or rec.get("signature"))
        if mod and _repo_module_exists(project_root, mod):
            if test_id not in explained:
                explained.append(test_id)
    if len(explained) >= 2:
        total = len(records)
        confidence = "high" if len(explained) >= total // 2 else "medium"
    else:
        explained = []
        by_module: dict[str, list[str]] = {}
        for rec in records:
            if not _import_related_error(rec) or not _import_contamination_module(rec):
                continue
            test_id = str(rec.get("test_id") or "")
            module = _test_module_from_test_id(test_id)
            by_module.setdefault(module, [])
            if test_id and test_id not in by_module[module]:
                by_module[module].append(test_id)
        for test_ids in by_module.values():
            if len(test_ids) >= 2:
                for test_id in test_ids:
                    if test_id not in explained:
                        explained.append(test_id)
        confidence = "medium"
    if len(explained) < 2:
        return []
    return [
        {
            "id": "H1",
            "summary": "import-path contamination",
            "confidence": confidence,
            "explains": explained,
            "source_squads": list(squad_names),
        }
    ]


def _base_test_id(test_id: str) -> str:
    if "[" in test_id:
        return test_id.split("[", 1)[0]
    return test_id


def rule_stale_package_metadata(
    records: Sequence[Mapping[str, Any]],
    project_root: Path | None,
    squad_names: Sequence[str],
) -> list[dict[str, Any]]:
    by_base: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        if str(rec.get("error_type") or "") != "AssertionError":
            continue
        left = rec.get("assert_left")
        if left is None:
            continue
        base = _base_test_id(str(rec.get("test_id") or ""))
        if not base:
            continue
        by_base.setdefault(base, []).append(rec)

    explained: list[str] = []
    for group in by_base.values():
        if len(group) < 2:
            continue
        left_values = {str(item.get("assert_left") or "") for item in group}
        if len(left_values) < 2:
            continue
        combined = " ".join(
            " ".join(
                (
                    str(item.get("test_id") or ""),
                    str(item.get("message") or ""),
                    str(item.get("assert_left") or ""),
                    str(item.get("assert_right") or ""),
                )
            )
            for item in group
        )
        if not _METADATA_HINT_RE.search(combined):
            continue
        for item in group:
            test_id = str(item.get("test_id") or "")
            if test_id and test_id not in explained:
                explained.append(test_id)

    if len(explained) < 2:
        return []
    confidence = "high" if len(explained) >= 6 else "medium"
    return [
        {
            "summary": "stale installed metadata or env override",
            "confidence": confidence,
            "explains": explained,
            "source_squads": list(squad_names),
        }
    ]


HYPOTHESIS_RULES: list[HypothesisRule] = [rule_import_cascade, rule_stale_package_metadata]


def summarize_top_causes(
    records: Sequence[Mapping[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Rank dominant failure signatures for large JUnit runs (report JSON)."""
    if not records:
        return []
    buckets: dict[str, dict[str, Any]] = {}
    for rec in records:
        signature = str(rec.get("signature") or "generic")
        entry = buckets.setdefault(
            signature,
            {
                "signature": signature,
                "count": 0,
                "error_type": str(rec.get("error_type") or "generic"),
            },
        )
        entry["count"] = int(entry["count"]) + 1
    ranked = sorted(
        buckets.values(),
        key=lambda item: (-int(item["count"]), str(item["signature"])),
    )
    return ranked[: max(0, limit)]


def build_cascade_hypotheses(
    records: Sequence[Mapping[str, Any]],
    project_root: Path | None,
    squad_names: Sequence[str],
) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    for rule in HYPOTHESIS_RULES:
        hypotheses.extend(rule(records, project_root, squad_names))
    return hypotheses


def _read_context_lines(path: Path, line: int | None, radius: int = 5) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if line is None or line < 1:
        start, end = 0, min(len(lines), radius * 2 + 1)
    else:
        start = max(0, line - 1 - radius)
        end = min(len(lines), line + radius)
    return "\n".join(lines[start:end])


def _resolve_imports(tree: ast.AST, source_path: Path) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                unresolved.append(f"relative:{mod or '.'}")
            elif mod:
                resolved.append(mod)
    return resolved, unresolved


def _find_call_sites(root: Path, symbol: str, limit: int = 8) -> list[str]:
    hits: list[str] = []
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern.search(text):
                rel = path.relative_to(root) if path.is_relative_to(root) else path
                hits.append(str(rel))
                if len(hits) >= limit:
                    return hits
    return hits


def _scan_path_contamination(
    paths: Sequence[Path],
    root: Path | None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    patterns = (
        ("sys.path.insert", re.compile(r"sys\.path\.(insert|append)\s*\(")),
        ("sys.modules", re.compile(r"sys\.modules\s*\[")),
    )
    seen_files: set[Path] = set()
    for anchor in paths:
        for directory in (anchor.parent, anchor):
            if not directory.is_dir():
                continue
            for name in sorted(os.listdir(directory)):
                if not name.endswith(".py"):
                    continue
                if "conftest" not in name and not name.startswith("test_"):
                    continue
                path = (directory / name).resolve()
                if path in seen_files:
                    continue
                seen_files.add(path)
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for label, pattern in patterns:
                    if pattern.search(text):
                        findings.append(
                            {
                                "kind": label,
                                "file": _report_path(path, root),
                            }
                        )
    return findings


def _source_matches_test_module(test_id: str, source: str) -> bool:
    expected = _test_module_from_test_id(test_id).replace(".", "/") + ".py"
    norm = source.replace("\\", "/")
    return norm == expected or norm.endswith("/" + expected)


def _resolve_record_test_file(root: Path, source: str) -> Path | None:
    if not source:
        return None
    candidate = (root / source).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _report_path(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return redact_text(str(path.resolve().relative_to(root.resolve())))
        except ValueError:
            pass
    return redact_text(path.name)


def run_code_squad(ctx: SquadContext) -> dict[str, Any]:
    started = time.monotonic()
    findings: list[dict[str, Any]] = []
    hypotheses: list[dict[str, Any]] = []
    root = ctx.project_root
    test_files: dict[str, Path] = {}
    if root:
        for rec in ctx.records:
            test_id = str(rec.get("test_id") or "")
            source = str(rec.get("source_file") or "")
            if not source:
                continue
            if ctx.project_root_explicit:
                resolved = _resolve_record_test_file(root, source)
            elif _source_matches_test_module(test_id, source):
                resolved = _resolve_record_test_file(root, source)
            else:
                resolved = None
            if resolved is not None:
                test_files[test_id] = resolved

    scan_allowed = bool(test_files)
    locations: list[dict[str, Any]] = []
    related_files: set[str] = set()
    unresolved_imports: set[str] = set()

    for test_id, path in list(test_files.items())[:12]:
        line = None
        for rec in ctx.records:
            if rec.get("test_id") == test_id:
                line = rec.get("line")
                break
        snippet = _read_context_lines(path, line if isinstance(line, int) else None)
        locations.append(
            {
                "test_id": test_id,
                "file": _report_path(path, root),
                "snippet": snippet[:800],
            }
        )
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            resolved, unresolved = _resolve_imports(tree, path)
            for mod in unresolved:
                unresolved_imports.add(mod)
            if root and ctx.project_root_explicit:
                for mod in resolved[:6]:
                    hits = _find_call_sites(root, mod.split(".")[-1], limit=3)
                    for hit in hits:
                        related_files.add(hit)
        except SyntaxError:
            unresolved_imports.add(f"syntax_error:{path.name}")

    if scan_allowed:
        for item in _scan_path_contamination(list(test_files.values()), root):
            findings.append(item)

    findings.extend({"location": loc} for loc in locations[:8])
    if unresolved_imports:
        findings.append({"unresolved_imports": sorted(unresolved_imports)[:20]})
    if related_files:
        findings.append({"related_files": sorted(related_files)[:20]})

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "name": SQUAD_CODE,
        "status": "ok",
        "elapsed_ms": elapsed_ms,
        "findings": findings,
        "hypotheses": hypotheses,
    }


def _git_root(start: Path | None) -> Path | None:
    if start is None:
        start = Path.cwd()
    try:
        completed = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            return Path(completed.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _git_log_file(repo: Path, file_path: str, limit: int = 20) -> list[str]:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                f"-n{limit}",
                "--oneline",
                "--",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if completed.returncode == 0:
            return [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def _git_blame_line(repo: Path, file_path: str, line: int) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "blame",
                "-L",
                f"{line},{line}",
                "--porcelain",
                "--",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()[:400]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


_CORPUS_RUN_ID_RE = re.compile(r"_(\d{10,})_")


def _corpus_module_index(corpus_dirs: Sequence[str]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw_dir in corpus_dirs:
        base = Path(raw_dir)
        if not base.is_dir():
            continue
        for xml in sorted(base.glob("*.xml")):
            run_match = _CORPUS_RUN_ID_RE.search(xml.name)
            run_id = run_match.group(1) if run_match else ""
            for rec in parse_junit_failure_records(xml):
                module = _test_module_from_test_id(str(rec.get("test_id") or ""))
                if not module:
                    continue
                entry = index.setdefault(
                    module,
                    {
                        "first_seen": xml.name,
                        "last_seen": xml.name,
                        "run_ids": set(),
                    },
                )
                entry["last_seen"] = xml.name
                if run_id:
                    entry["run_ids"].add(run_id)
    return index


def _corpus_signature_index(corpus_dirs: Sequence[str]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw_dir in corpus_dirs:
        base = Path(raw_dir)
        if not base.is_dir():
            continue
        for xml in sorted(base.glob("*.xml")):
            run_match = _CORPUS_RUN_ID_RE.search(xml.name)
            run_id = run_match.group(1) if run_match else ""
            for rec in parse_junit_failure_records(xml):
                sig = str(rec.get("signature") or "")
                if not sig:
                    continue
                entry = index.setdefault(
                    sig,
                    {
                        "first_seen": xml.name,
                        "last_seen": xml.name,
                        "repos": set(),
                        "variants": set(),
                        "run_ids": set(),
                    },
                )
                entry["last_seen"] = xml.name
                entry["variants"].add(rec.get("variant") or "unknown")
                if run_id:
                    entry["run_ids"].add(run_id)
    return index


def run_history_squad(ctx: SquadContext) -> dict[str, Any]:
    started = time.monotonic()
    repo = _git_root(ctx.project_root)
    if repo is None:
        return {
            "name": SQUAD_HISTORY,
            "status": "skipped",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "findings": [],
            "hypotheses": [],
            "skip_reason": "no_git",
        }

    findings: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for rec in ctx.records[:15]:
        source = str(rec.get("source_file") or "")
        if not source or source in seen_files:
            continue
        seen_files.add(source)
        log_lines = _git_log_file(repo, source)
        if log_lines:
            findings.append({"file": source, "git_log": log_lines[:5]})
        line = rec.get("line")
        if isinstance(line, int) and line > 0:
            blame = _git_blame_line(repo, source, line)
            if blame:
                findings.append({"file": source, "line": line, "blame": blame})

    corpus_sig = _corpus_signature_index(ctx.corpus_dirs)
    corpus_mod = _corpus_module_index(ctx.corpus_dirs)
    for rec in ctx.records[:20]:
        sig = str(rec.get("signature") or "")
        module = _test_module_from_test_id(str(rec.get("test_id") or ""))
        sig_meta = corpus_sig.get(sig)
        mod_meta = corpus_mod.get(module)
        if sig_meta is None and mod_meta is None:
            continue
        base = sig_meta or mod_meta or {}
        run_ids: set[str] = set()
        if sig_meta:
            run_ids |= set(sig_meta.get("run_ids") or ())
        if mod_meta:
            run_ids |= set(mod_meta.get("run_ids") or ())
        variants = set(rec.get("variants") or [])
        corpus_variants = (sig_meta or {}).get("variants") or set()
        flaky = bool(variants and corpus_variants - variants)
        findings.append(
            {
                "signature": sig,
                "test_module": module,
                "corpus_first": base.get("first_seen"),
                "corpus_last": base.get("last_seen"),
                "corpus_run_ids": sorted(run_ids),
                "flaky_marker": flaky,
            }
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "name": SQUAD_HISTORY,
        "status": "ok",
        "elapsed_ms": elapsed_ms,
        "findings": findings,
        "hypotheses": [],
    }


def _allowed_endpoint(url: str, allowlist: Sequence[str]) -> bool:
    for prefix in allowlist:
        if url.startswith(prefix):
            return True
    return False


def _framework_guess(records: Sequence[Mapping[str, Any]]) -> str:
    for rec in records:
        test_id = str(rec.get("test_id") or "")
        if "tests." in test_id or ".tests." in test_id:
            return "pytest"
    return "python"


def _web_queries(records: Sequence[Mapping[str, Any]]) -> list[str]:
    queries: list[str] = []
    framework = _framework_guess(records)
    for rec in records[:5]:
        error_type = str(rec.get("error_type") or "error")
        message = normalize_signature(str(rec.get("message") or ""))
        q = f"{error_type} {message} {framework}".strip()
        queries.append(redact_text(q)[:200])
    return queries


def run_web_squad(ctx: SquadContext) -> dict[str, Any]:
    import urllib.error
    import urllib.parse
    import urllib.request

    started = time.monotonic()
    queries = _web_queries(ctx.records)
    allowlist = tuple(ctx.web_endpoints) if ctx.web_endpoints else (GITHUB_SEARCH_API,)

    if ctx.offline:
        return {
            "name": SQUAD_WEB,
            "status": "skipped",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "findings": [],
            "hypotheses": [],
            "queries": queries,
            "skip_reason": "offline",
        }

    results: list[dict[str, Any]] = []
    for query in queries[:3]:
        params = urllib.parse.urlencode({"q": query, "per_page": "5"})
        url = f"{GITHUB_SEARCH_API}?{params}"
        if not _allowed_endpoint(url, allowlist):
            continue
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "err-o-roadrunner-sentinel"},
            )
            with urllib.request.urlopen(request, timeout=min(ctx.timeout, 8)) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            for item in (payload.get("items") or [])[:5]:
                results.append(
                    {
                        "title": redact_text(str(item.get("title") or ""))[:200],
                        "url": str(item.get("html_url") or ""),
                        "score": float(item.get("score") or 0.0),
                    }
                )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
            continue

    elapsed_ms = int((time.monotonic() - started) * 1000)
    status = "ok_empty" if not results else "ok"
    return {
        "name": SQUAD_WEB,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "findings": results[:5],
        "hypotheses": [],
        "queries": queries,
    }


_SQUAD_RUNNERS: dict[str, SquadRunner] = {
    SQUAD_CODE: run_code_squad,
    SQUAD_HISTORY: run_history_squad,
    SQUAD_WEB: run_web_squad,
}


def _run_one_squad(
    name: str,
    ctx: SquadContext,
    squad_timeout: float,
    deadline: float,
) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {
            "name": name,
            "status": "skipped",
            "elapsed_ms": 0,
            "findings": [],
            "hypotheses": [],
            "skip_reason": "budget",
        }
    timeout = min(squad_timeout, remaining)
    runner = _SQUAD_RUNNERS.get(name)
    if runner is None:
        return {
            "name": name,
            "status": "error",
            "elapsed_ms": 0,
            "findings": [],
            "hypotheses": [],
            "error": "unknown_squad",
        }
    result_box: dict[str, Any] = {}
    error_box: list[BaseException] = []
    started = time.monotonic()

    def _target() -> None:
        try:
            result_box["result"] = runner(ctx)
        except BaseException as exc:  # noqa: BLE001 — squads must never escape
            error_box.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        return {
            "name": name,
            "status": "timeout",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "findings": [],
            "hypotheses": [],
        }
    if error_box:
        return {
            "name": name,
            "status": "error",
            "elapsed_ms": 0,
            "findings": [],
            "hypotheses": [],
            "error": type(error_box[0]).__name__,
        }
    return result_box.get(
        "result",
        {
            "name": name,
            "status": "error",
            "elapsed_ms": 0,
            "findings": [],
            "hypotheses": [],
        },
    )


def _merge_hypothesis_into(
    by_summary: dict[str, dict[str, Any]],
    hyp: Mapping[str, Any],
    squad_names: Sequence[str],
) -> None:
    summary = str(hyp.get("summary") or "")
    if not summary:
        return
    entry = by_summary.get(summary)
    squads = {str(name) for name in squad_names if name}
    if entry:
        entry["source_squads"] = sorted(set(entry.get("source_squads") or []) | squads)
        entry["explains"] = list(
            dict.fromkeys([*(entry.get("explains") or []), *(hyp.get("explains") or [])])
        )
        if hyp.get("confidence") == "high":
            entry["confidence"] = "high"
    else:
        copy = dict(hyp)
        copy["source_squads"] = sorted(squads)
        by_summary[summary] = copy


def merge_squad_hypotheses(
    squad_results: Sequence[Mapping[str, Any]],
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    by_summary: dict[str, dict[str, Any]] = {}
    active_squads = [
        str(squad.get("name") or "")
        for squad in squad_results
        if squad.get("status") in ("ok", "timeout")
    ]
    for squad in squad_results:
        if squad.get("status") not in ("ok", "timeout"):
            continue
        for hyp in squad.get("hypotheses") or []:
            _merge_hypothesis_into(by_summary, hyp, (str(squad.get("name") or ""),))
    if records is not None:
        for rule in HYPOTHESIS_RULES:
            for hyp in rule(records, project_root, active_squads):
                _merge_hypothesis_into(by_summary, hyp, active_squads)
    ordered = sorted(
        by_summary.values(),
        key=lambda h: (
            0 if h.get("confidence") == "high" else 1,
            -len(h.get("explains") or []),
            str(h.get("id") or ""),
        ),
    )
    for idx, hyp in enumerate(ordered, start=1):
        if not hyp.get("id"):
            hyp["id"] = f"H{idx}"
    return ordered


FixRule = Callable[[list[dict[str, Any]], Sequence[Mapping[str, Any]]], list[dict[str, Any]] | None]


def rule_fix_import_cascade(
    candidates: list[dict[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    for hyp in hypotheses:
        if hyp.get("summary") != "import-path contamination":
            continue
        out = list(candidates)
        squads = list(hyp.get("source_squads") or [])
        entry = {
            "id": f"F{len(out) + 1}",
            "kind": "import_cascade",
            "confidence": hyp.get("confidence", "medium"),
            "evidence": [f"hypothesis {hyp.get('id')}: import-path contamination"],
            "location": {},
            "suggestion": (
                "Treat as import-path cascade: isolate the failing test module imports and "
                "remove sys.path / sys.modules side effects in tests or conftest."
            ),
            "verify_command": out[0].get("verify_command", "") if out else "",
            "source_squads": squads,
        }
        out.insert(0, entry)
        return out
    return None


def rule_fix_reinstall_editable(
    candidates: list[dict[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    for hyp in hypotheses:
        if hyp.get("summary") != "stale installed metadata or env override":
            continue
        out = list(candidates)
        squads = list(hyp.get("source_squads") or [])
        entry = {
            "id": f"F{len(out) + 1}",
            "kind": "stale_metadata",
            "confidence": hyp.get("confidence", "medium"),
            "evidence": [
                f"hypothesis {hyp.get('id')}: stale installed metadata or env override"
            ],
            "location": {},
            "suggestion": (
                "Reinstall the package in editable mode (`pip install -e .` or "
                "`uv sync --reinstall`) and compare `importlib.metadata.version(...)` "
                "against the version in pyproject.toml / source."
            ),
            "verify_command": out[0].get("verify_command", "") if out else "",
            "source_squads": squads,
        }
        out.insert(0, entry)
        return out
    return None


FIX_RULES: list[FixRule] = [rule_fix_import_cascade, rule_fix_reinstall_editable]


def enrich_fix_candidates(
    candidates: list[dict[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not hypotheses:
        return candidates
    out = list(candidates)
    for rule in FIX_RULES:
        updated = rule(out, hypotheses)
        if updated is not None:
            out = updated
    return out


def run_error_squads(
    *,
    enabled: frozenset[str],
    records: Sequence[Mapping[str, Any]],
    project_root: Path | None,
    project_root_explicit: bool = False,
    junit_paths: Sequence[str],
    corpus_dirs: Sequence[str],
    offline: bool,
    web_endpoints: Sequence[str],
    squad_timeout: float = DEFAULT_SQUAD_TIMEOUT,
    squad_budget: float = DEFAULT_SQUAD_BUDGET,
    squad_workers: int = DEFAULT_SQUAD_WORKERS,
) -> dict[str, Any]:
    if not enabled:
        return {"squads": [], "hypotheses": []}

    ctx = SquadContext(
        records=[dict(r) for r in records],
        project_root=project_root,
        project_root_explicit=project_root_explicit,
        junit_paths=tuple(junit_paths),
        corpus_dirs=tuple(corpus_dirs),
        offline=offline,
        web_endpoints=tuple(web_endpoints),
        timeout=squad_timeout,
    )

    budget_start = time.monotonic()
    deadline = budget_start + max(0.1, squad_budget)
    names = [name for name in ALL_SQUADS if name in enabled]
    results: list[dict[str, Any]] = []

    workers = max(1, min(squad_workers, len(names) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for name in names:
            futures[pool.submit(_run_one_squad, name, ctx, squad_timeout, deadline)] = name
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:  # noqa: BLE001
                results.append(
                    {
                        "name": futures[future],
                        "status": "error",
                        "elapsed_ms": 0,
                        "findings": [],
                        "hypotheses": [],
                    }
                )

    results.sort(key=lambda r: ALL_SQUADS.index(r["name"]) if r["name"] in ALL_SQUADS else 99)
    hypotheses = merge_squad_hypotheses(
        results,
        records=ctx.records,
        project_root=ctx.project_root,
    )
    return {"squads": results, "hypotheses": hypotheses}


def load_failure_records_for_escalation(
    junit_paths: Sequence[str],
) -> list[dict[str, Any]]:
    paths = collect_junit_paths(junit_paths, ())
    if not paths:
        return []
    return merge_failure_records(paths)
