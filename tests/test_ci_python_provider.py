"""Offline guards for the moving-latest CI interpreter lane."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_latest_python_policy_is_applied_during_setup_only():
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    job = re.search(r"^  test:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S).group(1)
    steps = re.split(r"^      - ", job, flags=re.M)[1:]
    setup = [step for step in steps if "uses: astral-sh/setup-uv@" in step]
    assert len(setup) == 1
    step = setup[0]
    assert "env:\n          UV_PYTHON_PREFERENCE: ${{ matrix.python-version == '3.x' && 'only-managed' || 'managed' }}" in step
    assert 'python-version: "${{ matrix.python-version == \'3.x\' && \'3\' || matrix.python-version }}"' in step
    assert "activate-environment: true" in step
    assert text.count("UV_PYTHON_PREFERENCE") == 1
    assert "UV_PYTHON_PREFERENCE" not in job.split("steps:", 1)[0]
    assert 'python-version: ["3.11", "3.12", "3.13", "3.14", "3.15", "3.x"]' in job
    assert "junit-py3.x/junit.xml" in text
