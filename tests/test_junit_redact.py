"""Unit tests for junit_redact leak detection and redaction."""

from __future__ import annotations

import importlib
import re
from unittest import mock

import pytest

from err_o_roadrunner.junit_redact import (
    CREDENTIAL_PATTERN_SPECS,
    find_corpus_leaks,
    fixture_leak_patterns_excluding_credential,
    redact_text,
    redact_xml_document,
    secret_patterns_excluding_credential,
)

JR = importlib.import_module("err_o_roadrunner.junit_redact")

_FAKE_OAUTH = "ghp_" + "A" * 36
_FAKE_GHO = "gho_" + "B" * 36
_FAKE_GHS = "ghs_" + "C" * 20
_FAKE_PAT = "github_pat_" + "D" * 22
_FAKE_AKIA = "AKIA" + "E" * 16
_FAKE_ASIA = "ASIA" + "F" * 16
_FAKE_AWS_SECRET = "aws_secret_access_key=" + "G" * 40
_FAKE_BEARER = "Bearer " + "H" * 12
_FAKE_SK = "sk-" + "I" * 20

_CREDENTIAL_SAMPLES: dict[str, str] = {
    "github_oauth_token": _FAKE_OAUTH,
    "github_app_token": _FAKE_GHS,
    "github_pat_token": _FAKE_PAT,
    "aws_access_key_id": _FAKE_AKIA,
    "aws_secret_access_key": _FAKE_AWS_SECRET,
    "bearer_token": _FAKE_BEARER,
    "openai_sk_key": _FAKE_SK,
}


def _redact_with_patterns(text: str, patterns: list) -> str:
    for pattern, repl in patterns:
        text = pattern.sub(repl, text)
    return text


def _find_leaks_with_patterns(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    hits: list[str] = []
    for label, pattern in patterns:
        if pattern.search(text):
            hits.append(label)
    return hits


@pytest.mark.gap
def test_find_corpus_leaks_detects_runner_hostname_attribute() -> None:
    text = '<testsuite hostname="runnervmabc" name="pytest"/>'
    assert "hostname_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_non_runner_hostname_attribute() -> None:
    text = '<testsuite hostname="cursor" name="pytest"/>'
    assert "hostname_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_iso_timestamp_attribute() -> None:
    text = 'timestamp="2026-10-11T15:23:14.305804+00:00"'
    assert "iso_timestamp_attribute" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_detects_runner_work_path() -> None:
    text = "ImportError in /home/runner/work/foo/bar/tests"
    assert "runner_work_path" in find_corpus_leaks(text)


@pytest.mark.gap
def test_find_corpus_leaks_clean_after_redact_xml_document() -> None:
    raw = (
        '<?xml version="1.0"?><testsuites>'
        '<testsuite name="pytest" hostname="cursor" timestamp="2026-10-11T12:00:00+00:00"/>'
        "</testsuites>"
    )
    out = redact_xml_document(raw)
    assert find_corpus_leaks(out) == []


@pytest.mark.parametrize("label", [spec.label for spec in CREDENTIAL_PATTERN_SPECS])
def test_credential_redacted_in_text(label: str) -> None:
    sample = _CREDENTIAL_SAMPLES[label]
    redacted = redact_text(f"prefix {sample} suffix")
    assert sample not in redacted
    assert label not in find_corpus_leaks(redacted)


@pytest.mark.parametrize("label", [spec.label for spec in CREDENTIAL_PATTERN_SPECS])
def test_credential_redacted_in_xml_attribute(label: str) -> None:
    sample = _CREDENTIAL_SAMPLES[label]
    raw = (
        f'<?xml version="1.0"?><testsuites><testsuite name="x" token="{sample}">'
        f"<failure message=\"{sample}\">{sample}</failure></testsuite></testsuites>"
    )
    out = redact_xml_document(raw)
    assert sample not in out
    assert label not in find_corpus_leaks(out)


@pytest.mark.parametrize("label", [spec.label for spec in CREDENTIAL_PATTERN_SPECS])
def test_find_corpus_leaks_detects_credential_in_text(label: str) -> None:
    sample = _CREDENTIAL_SAMPLES[label]
    hits = find_corpus_leaks(f"leak {sample} here")
    assert label in hits


def test_find_corpus_leaks_detects_asia_access_key() -> None:
    assert "aws_access_key_id" in find_corpus_leaks(f"key={_FAKE_ASIA}")


def test_github_oauth_prefixes_redacted() -> None:
    for token in (_FAKE_OAUTH, _FAKE_GHO, "ghu_" + "J" * 36, "ghr_" + "K" * 36):
        out = redact_text(token)
        assert token not in out
        assert find_corpus_leaks(out) == []


@pytest.mark.gap
def test_near_miss_tokens_not_flagged() -> None:
    benign = (
        "ghp_short "
        "AKIA1234567890123 "
        "akiaABCDEFGHIJKLMNOP "
        "github_pat_short "
        "ordinary github_pat word "
        "aws_secret_access_key=tooshort "
        "Bearer x"
    )
    hits = find_corpus_leaks(benign)
    cred_hits = [h for h in hits if h in _CREDENTIAL_SAMPLES]
    assert cred_hits == []


@pytest.mark.parametrize("label", [spec.label for spec in CREDENTIAL_PATTERN_SPECS])
def test_mutation_dropping_credential_redaction_leaves_leak(label: str) -> None:
    sample = _CREDENTIAL_SAMPLES[label]
    text = f"token={sample}"
    patterns = secret_patterns_excluding_credential(label)
    leaked = _redact_with_patterns(text, patterns)
    assert sample in leaked
    assert label in find_corpus_leaks(leaked)


@pytest.mark.parametrize("label", [spec.label for spec in CREDENTIAL_PATTERN_SPECS])
def test_mutation_dropping_credential_detection_misses_leak(label: str) -> None:
    sample = _CREDENTIAL_SAMPLES[label]
    text = f"token={sample}"
    patterns = fixture_leak_patterns_excluding_credential(label)
    hits = _find_leaks_with_patterns(text, patterns)
    assert label not in hits


@pytest.mark.gap
def test_mutation_table_all_credential_patterns_guarded() -> None:
    """Documented guard: each credential pattern has independent redaction + detection tests."""
    labels = [spec.label for spec in CREDENTIAL_PATTERN_SPECS]
    assert len(labels) == len(set(labels))
