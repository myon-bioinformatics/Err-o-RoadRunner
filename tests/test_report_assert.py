"""Unit tests for strict report line assertions."""

from __future__ import annotations

import pytest

from tests.report_assert import assert_report_has_exact_line


def test_exact_line_matches_found() -> None:
    lines = ["FOUND: exit code: 1", "FOUND: actions log failing step (x): pytest"]
    assert_report_has_exact_line(lines, "FOUND: exit code: 1")


def test_substring_prefix_does_not_match_longer_line() -> None:
    lines = ["FOUND: actions log failing step (actions-log): pytest -q"]
    with pytest.raises(AssertionError, match="exactly one line equal"):
        assert_report_has_exact_line(lines, "FOUND: actions log failing step")


def test_not_found_does_not_satisfy_found_prefix() -> None:
    lines = ["NOT FOUND: actions log failing step (actions-log)"]
    with pytest.raises(AssertionError):
        assert_report_has_exact_line(lines, "FOUND: actions log failing step (actions-log)")


def test_startswith_style_helper_would_false_pass() -> None:
    """Document the PR #3 bug: startswith('FOUND: foo') matches NOT FOUND: foo."""
    lines = ["NOT FOUND: actions log failing step (actions-log)"]
    assert not any(ln == "FOUND: actions log failing step (actions-log)" for ln in lines)
    assert any(ln.startswith("FOUND: actions log failing step") for ln in lines) is False
    assert any("FOUND: actions log failing step" in ln for ln in lines) is True
