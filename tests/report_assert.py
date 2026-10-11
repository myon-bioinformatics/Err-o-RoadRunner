"""Strict report-line assertions for tests (no substring false positives)."""

from __future__ import annotations


def assert_report_has_exact_line(lines: list[str], expected: str) -> str:
    """Require an exact full-line match; partial lines and NOT FOUND must not satisfy."""
    if expected.startswith("FOUND:"):
        not_found_line = f"NOT FOUND: {expected.removeprefix('FOUND: ')}"
        if not_found_line in lines:
            raise AssertionError(
                f"ambiguous: NOT FOUND line present for expected FOUND line {expected!r}"
            )
    matches = [ln for ln in lines if ln == expected]
    assert len(matches) == 1, (
        f"expected exactly one line equal to {expected!r}, "
        f"got {len(matches)} matches among {len(lines)} lines"
    )
    return matches[0]
