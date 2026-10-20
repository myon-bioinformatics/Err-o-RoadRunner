"""Redact host paths, tokens, and runner metadata from JUnit XML text."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

REDACTED_HOST_MARKERS = frozenset(
    {"<redacted-host>", "&lt;redacted-host&gt;", "redacted-host"}
)
REDACTED_TIMESTAMP_MARKERS = frozenset({"<timestamp>", "&lt;timestamp&gt;"})

HOSTNAME_ATTR_RE = re.compile(r"""hostname\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
TIMESTAMP_ATTR_RE = re.compile(
    r"""timestamp\s*=\s*["'](\d{4}-\d{2}-\d{2}T[^"']*)["']""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CredentialPatternSpec:
    """Shared credential definition for redaction and fixture leak scans."""

    label: str
    regex: str
    replacement: str


# Single source of truth for credential-like secrets in corpus XML/text.
CREDENTIAL_PATTERN_SPECS: tuple[CredentialPatternSpec, ...] = (
    CredentialPatternSpec(
        "github_oauth_token",
        r"gh[pousr]_[A-Za-z0-9]{36,}",
        "ghp_<redacted>",
    ),
    CredentialPatternSpec(
        "github_app_token",
        r"ghs_[A-Za-z0-9_]{20,}",
        "ghs_<redacted>",
    ),
    CredentialPatternSpec(
        "github_pat_token",
        r"github_pat_[A-Za-z0-9_]{22,}",
        "github_pat_<redacted>",
    ),
    CredentialPatternSpec(
        "aws_access_key_id",
        r"(?:AKIA|ASIA)[A-Z0-9]{16}",
        "<aws-access-key-id>",
    ),
    CredentialPatternSpec(
        "aws_secret_access_key",
        r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40,}",
        "aws_secret_access_key=<redacted>",
    ),
    CredentialPatternSpec(
        "bearer_token",
        r"Bearer\s+[A-Za-z0-9._-]{8,}",
        "Bearer <redacted>",
    ),
    CredentialPatternSpec(
        "openai_sk_key",
        r"sk-[A-Za-z0-9]{20,}",
        "sk-<redacted>",
    ),
)


def _oauth_replacement(match: re.Match[str]) -> str:
    token = match.group(0)
    return f"{token[:4]}<redacted>"


def _compile_credential_redaction(
    spec: CredentialPatternSpec,
) -> tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]:
    pattern = re.compile(spec.regex)
    if spec.label == "github_oauth_token":
        return pattern, _oauth_replacement
    return pattern, spec.replacement


_CREDENTIAL_REDUCTION: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    _compile_credential_redaction(spec) for spec in CREDENTIAL_PATTERN_SPECS
]

_PATH_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/home/runner/work/[^/]+/[^/]+"), "<runner-work>"),
    # pytest repr truncation can leave only the workspace prefix / repository.
    # Keep this after the full-path rule so normal relative paths stay intact.
    (re.compile(r"/home/runner/work/[^/\s<>\"']*"), "<runner-work>"),
    (re.compile(r"/Users/[^/\s]+"), "<user-home>"),
    (re.compile(r"/opt/hostedtoolcache/"), "<toolcache>/"),
    (re.compile(r"/tmp/pytest-of-runner/"), "<pytest-tmp>/"),
    (re.compile(r"0x[0-9a-fA-F]{8,}"), "0x<addr>"),
]

SECRET_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    *_CREDENTIAL_REDUCTION,
    *_PATH_REDACTION_PATTERNS,
]

_FIXTURE_PATH_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("runner_work_path", re.compile(r"/home/runner/work/")),
    ("user_home_path", re.compile(r"/Users/[^/\s<\"']+")),
    ("hostedtoolcache_path", re.compile(r"/opt/hostedtoolcache/")),
    ("pytest_runner_tmp", re.compile(r"/tmp/pytest-of-runner/")),
    ("github_runner_hostname", re.compile(r"hostname=\"runnervm", re.IGNORECASE)),
]

FIXTURE_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    *[(spec.label, re.compile(spec.regex)) for spec in CREDENTIAL_PATTERN_SPECS],
    *_FIXTURE_PATH_LEAK_PATTERNS,
]


def redact_text(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_attribute(name: str, value: str) -> str:
    value = redact_text(value)
    key = name.split("}")[-1].lower()
    if key in ("hostname", "host"):
        if value in REDACTED_HOST_MARKERS:
            return value
        return "<redacted-host>"
    if key == "timestamp" and ISO_TIMESTAMP_RE.match(value):
        return "<timestamp>"
    if key in ("file", "log", "url") and ("/" in value or "\\" in value):
        return redact_text(value)
    return value


def redact_element(elem: ET.Element) -> None:
    elem.attrib = {k: redact_attribute(k, v) for k, v in elem.attrib.items()}
    if elem.text:
        elem.text = redact_text(elem.text)
    if elem.tail:
        elem.tail = redact_text(elem.tail)


def redact_xml_tree(root: ET.Element) -> None:
    for elem in root.iter():
        redact_element(elem)


def redact_xml_document(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    redact_xml_tree(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _hostname_attribute_leaks(text: str) -> bool:
    for match in HOSTNAME_ATTR_RE.finditer(text):
        value = match.group(1)
        if value in REDACTED_HOST_MARKERS:
            continue
        if value.strip():
            return True
    return False


def _timestamp_attribute_leaks(text: str) -> bool:
    return bool(TIMESTAMP_ATTR_RE.search(text))


def find_corpus_leaks(text: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in FIXTURE_LEAK_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    if _hostname_attribute_leaks(text):
        hits.append("hostname_attribute")
    if _timestamp_attribute_leaks(text):
        hits.append("iso_timestamp_attribute")
    return hits


# Backwards-compatible alias for corpus tooling.
CORPUS_LEAK_PATTERNS = FIXTURE_LEAK_PATTERNS


def secret_patterns_excluding_credential(label: str) -> list[
    tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]
]:
    kept = [
        entry
        for spec, entry in zip(CREDENTIAL_PATTERN_SPECS, _CREDENTIAL_REDUCTION, strict=True)
        if spec.label != label
    ]
    return [*kept, *_PATH_REDACTION_PATTERNS]


def fixture_leak_patterns_excluding_credential(label: str) -> list[tuple[str, re.Pattern[str]]]:
    return [
        *(pair for pair in FIXTURE_LEAK_PATTERNS if pair[0] != label),
    ]
