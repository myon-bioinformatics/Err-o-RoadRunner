"""benchmark_util normalization helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.benchmark_util import normalize_report_benchmark, normalize_short_benchmark

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_REPORT_GLOB = "tests/golden/benchmarks/*/report.json"
_FORBIDDEN_ENV_VALUE_RE = re.compile(
    r'"(?:HOME|PATH|PWD|OLDPWD|CURSOR_[A-Z0-9_]+)":\s*"(?!<(?:masked|redacted)>)[^"]+'
)


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


@pytest.mark.sentinel
def test_normalize_report_benchmark_clears_environment_values() -> None:
    doc = {
        "environment": {
            "variable_count": 2,
            "variable_names": ["HOME", "PATH"],
            "values_redacted": {"HOME": "/secret", "PATH": "/bin"},
        }
    }
    out = normalize_report_benchmark(doc)
    assert out["environment"]["values_redacted"] == {}
    assert out["environment"]["variable_names"] == ["<masked>"]


@pytest.mark.sentinel
def test_benchmark_golden_reports_contain_no_raw_env_values() -> None:
    paths = sorted(ROOT.glob(BENCHMARK_REPORT_GLOB))
    assert paths, "expected benchmark report goldens"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not _FORBIDDEN_ENV_VALUE_RE.search(text), (
            f"{path.relative_to(ROOT)} contains raw HOME/PATH/CURSOR_* env values"
        )
        doc = json.loads(text)
        values = (doc.get("environment") or {}).get("values_redacted") or {}
        assert values == {}, f"{path.name} values_redacted must be empty"
