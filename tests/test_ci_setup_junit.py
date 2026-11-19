"""Check pre-test setup evidence through the public stdlib digest CLI."""

import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "scripts" / "ci_setup_junit.py"


def emit(path, outcomes):
    return subprocess.run(
        [sys.executable, "-S", str(WRITER), "pytest-py3.15", str(path)],
        env={**os.environ, "SETUP_OUTCOMES": outcomes},
        capture_output=True,
        text=True,
    )


def test_setup_junit_digest_distinguishes_setup_and_test_failures(tmp_path):
    setup = tmp_path / "junit-setup-py3.15" / "setup.xml"
    result = emit(setup, "Select Python=success\nSync & install=failure\n"
                  "Compile=skipped\nImport=\nLater=cancelled\n")
    assert result.returncode == 0, result.stderr
    suite = ET.parse(setup).getroot()
    assert suite.attrib == {
        "name": "ci.setup.pytest-py3.15", "tests": "5", "errors": "1", "failures": "0", "skipped": "3",
    }
    assert suite.find("./testcase/error").attrib == {
        "type": "SetupError", "message": "Sync & install failed",
    }
    test = tmp_path / "junit.xml"
    test.write_text('<testsuite><testcase classname="product" name="test_value">'
                    '<failure message="assertion"/></testcase></testsuite>', encoding="utf-8")
    digest = subprocess.run(
        [sys.executable, "-S", str(ROOT / "triage.py"), "digest", str(setup), str(test)],
        capture_output=True,
        text=True,
    )
    assert digest.returncode == 1, digest.stderr
    assert "ci.setup.pytest-py3.15::Sync & install: Sync & install failed" in digest.stdout
    assert "product::test_value: assertion" in digest.stdout
    assert "tests=5 failures=0 errors=1 skipped=3" in digest.stdout
    assert "tests=1 failures=1 errors=0 skipped=0" in digest.stdout


@pytest.mark.parametrize("outcomes", [
    "", "Sync=unexpected", "missing-separator", " =success", "Sync=success\n Sync=failure",
])
def test_invalid_setup_outcomes_do_not_write_passing_report(tmp_path, outcomes):
    report = tmp_path / "setup.xml"
    assert emit(report, outcomes).returncode != 0
    assert not report.exists()
