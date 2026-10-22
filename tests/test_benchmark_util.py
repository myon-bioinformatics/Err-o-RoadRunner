"""benchmark_util normalization helpers."""

from __future__ import annotations

from tests.benchmark_util import normalize_short_benchmark


def test_normalize_short_benchmark_masks_tool_versions() -> None:
    raw = (
        "F cmd\n"
        "  python version: 3.12.3 (cpython)\n"
        "  node version: v22.23.4\n"
        "  gh version: gh version 2.102.0 (2026-09-30)\n"
        "== 1 failed in 0.01s ==\n"
    )
    out = normalize_short_benchmark(raw)
    assert "3.12.3" not in out
    assert "python version: <masked>" in out
    assert "node version: <masked>" in out
    assert "gh version: <masked>" in out
