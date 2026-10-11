"""Redact host paths, tokens, and runner metadata from JUnit XML text."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ghs_[A-Za-z0-9_]{20,}"), "ghs_<redacted>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]+"), "Bearer <redacted>"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-<redacted>"),
    (re.compile(r"/home/runner/work/[^/]+/[^/]+"), "<runner-work>"),
    (re.compile(r"/Users/[^/\s]+"), "<user-home>"),
    (re.compile(r"/opt/hostedtoolcache/"), "<toolcache>/"),
    (re.compile(r"/tmp/pytest-of-runner/"), "<pytest-tmp>/"),
    (re.compile(r"0x[0-9a-fA-F]{8,}"), "0x<addr>"),
]

RUNNER_HOSTNAME_RE = re.compile(r"^runnervm[a-z0-9]+$", re.IGNORECASE)
ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def redact_text(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_attribute(name: str, value: str) -> str:
    value = redact_text(value)
    key = name.split("}")[-1].lower()
    if key == "hostname" or key == "host":
        if RUNNER_HOSTNAME_RE.match(value) or value.startswith("runner"):
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


# Patterns that must not appear in checked-in corpus fixtures (scan tests).
CORPUS_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_runner_hostname", re.compile(r'hostname="runnervm', re.IGNORECASE)),
    ("runner_work_path", re.compile(r"/home/runner/work/")),
    ("user_home_path", re.compile(r"/Users/[^/\s<]+")),
    ("hostedtoolcache_path", re.compile(r"/opt/hostedtoolcache/")),
    ("pytest_runner_tmp", re.compile(r"/tmp/pytest-of-runner/")),
    ("github_app_token", re.compile(r"ghs_[A-Za-z0-9_]{20,}")),
    ("openai_sk_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}")),
]


def find_corpus_leaks(text: str) -> list[str]:
    hits: list[str] = []
    for label, pattern in CORPUS_LEAK_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    return hits
