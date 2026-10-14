"""artifact digest subcommand: zip/dir/file sources, JUnit, JSON receipts, bounds."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

from triage import digest

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / "triage.py"

JUNIT = b"""<testsuite tests="3"><testcase classname="a.B" name="ok"/>
<testcase classname="a.B" name="bad"><failure message="boom">line1
   line2 trace</failure></testcase>
<testcase name="err"><error message="E">x</error></testcase>
<testcase name="skip"><skipped/></testcase></testsuite>"""
RECEIPT = json.dumps(
    {
        "schema": "route-audit/1",
        "stage": "failed",
        "failed_step": "find-door-item",
        "huge": "x" * 5000,
    }
).encode()


def make_zip(path: Path, **members: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name.replace("__", "/"), data)


def test_zip_digest_counts_failures_and_picks_receipt_keys(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    make_zip(z, **{"junit__r.xml": JUNIT, "t__route-audit.json": RECEIPT, "t__shot.png": b"\x89PNG"})
    lines, failing, problems = digest([str(z)])
    text = "\n".join(lines)
    assert failing == 2
    assert problems == 0
    assert "junit junit/r.xml: tests=4 failures=1 errors=1 skipped=1" in text
    assert "FAIL junit/r.xml a.B::bad: boom | line1 line2 trace" in text
    assert '"failed_step": "find-door-item"' in text and "xxxx" not in text
    assert "other: .png×1" in text


def test_limit_and_width_bound_the_output(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    make_zip(z, **{"r.xml": JUNIT})
    lines, failing, problems = digest([str(z)], limit=1, width=12)
    assert failing == 2
    assert problems == 0
    assert any(line.startswith("... 1 more") for line in lines)
    assert max(len(line) for line in lines if line.startswith("FAIL")) < 60


def test_directory_and_single_file_sources(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "r.xml").write_bytes(JUNIT)
    assert digest([str(tmp_path / "d")])[1:] == (2, 0)
    assert digest([str(tmp_path / "d" / "r.xml")])[1:] == (2, 0)


def test_doctype_invalid_xml_and_oversize_count_as_unverifiable(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    make_zip(
        z,
        **{
            "evil.xml": b'<!DOCTYPE x [<!ENTITY a "b">]><x/>',
            "bad.xml": b"<x>",
            "big.bin": b"0" * 100,
        },
    )
    lines, failing, problems = digest([str(z)], max_bytes=50)
    text = "\n".join(lines)
    assert failing == 0
    assert problems == 3
    assert "problem evil.xml: DOCTYPE/ENTITY" in text and "problem bad.xml: invalid XML" in text
    assert "skipped (over --max-bytes): big.bin (100 bytes)" in text
    assert "unverifiable inputs: 3" in text


def test_cli_exit_codes_and_python_dash_S(tmp_path: Path) -> None:
    ok = tmp_path / "ok.xml"
    ok.write_bytes(b'<testsuite><testcase name="a"/></testsuite>')
    bad = tmp_path / "bad.xml"
    bad.write_bytes(JUNIT)
    malformed = tmp_path / "malformed.xml"
    malformed.write_bytes(b"<testsuite>")
    unsafe = tmp_path / "unsafe.xml"
    unsafe.write_bytes(b'<!DOCTYPE testsuite><testsuite/>')

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-S", str(TRIAGE), "digest", *args],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )

    assert run(str(ok)).returncode == 0
    assert run(str(bad)).returncode == 1
    assert run(str(malformed)).returncode == 2
    assert run(str(unsafe)).returncode == 2
    missing = run(str(tmp_path / "nope.zip"))
    assert missing.returncode == 2 and "nope.zip" in missing.stderr


def test_mixed_multiple_files_preserve_separate_failure_and_problem_counts(tmp_path: Path) -> None:
    valid = tmp_path / "valid.xml"
    valid.write_bytes(b'<testsuite><testcase name="bad"><failure message="no"/></testcase></testsuite>')
    unsafe = tmp_path / "unsafe.xml"
    unsafe.write_bytes(b'<!DOCTYPE testsuite><testsuite/>')
    lines, failing, problems = digest([str(valid), str(unsafe)])
    text = "\n".join(lines)
    assert failing == 1
    assert problems == 1
    assert "one.zip:" not in text
    assert "valid.xml" in text and "unsafe.xml" in text
    assert "failing testcases: 1" in text
    assert "unverifiable inputs: 1" in text
    result = subprocess.run(
        [sys.executable, "-S", str(TRIAGE), "digest", str(valid), str(unsafe)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 2


def test_invalid_receipt_json_is_an_input_problem_and_exits_nonzero(tmp_path: Path) -> None:
    receipt = tmp_path / "route-audit.json"
    receipt.write_text('{"stage":', encoding="utf-8")
    lines, failing, problems = digest([str(receipt)])
    assert failing == 0
    assert problems == 1
    assert any(line.startswith("problem route-audit.json: invalid JSON") for line in lines)

    result = subprocess.run(
        [sys.executable, "-S", str(TRIAGE), "digest", str(receipt)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 2


def test_unmatched_json_is_not_summarized_only_extension_count(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    secret = json.dumps({"token": "ghp_" + "A" * 40, "note": "keep out"}).encode()
    make_zip(z, **{"misc__package-lock.json": secret})
    lines, failing, problems = digest([str(z)])
    text = "\n".join(lines)
    assert failing == 0
    assert problems == 0
    assert "json " not in text
    assert "ghp_" not in text
    assert "other: .json×1" in text


def test_passing_testcase_emits_no_fail_line(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    make_zip(z, **{"r.xml": b'<testsuite><testcase classname="a" name="ok"/></testsuite>'})
    lines, failing, problems = digest([str(z)])
    text = "\n".join(lines)
    assert failing == 0
    assert problems == 0
    assert text.count("FAIL ") == 0
    assert "junit r.xml: tests=1 failures=0 errors=0 skipped=0" in text


def test_list_members_names_other_files_without_dumping_contents(tmp_path: Path) -> None:
    z = tmp_path / "a.zip"
    make_zip(z, **{"data__secret.bin": b"TOPSECRET", "nested__log.txt": b"hello"})
    lines, _, _ = digest([str(z)], list_members=True)
    text = "\n".join(lines)
    assert "TOPSECRET" not in text
    assert "  data/secret.bin" in text or "  nested/log.txt" in text
    assert "other:" in text


def test_multi_source_labels_include_archive_basename(tmp_path: Path) -> None:
    z1 = tmp_path / "one.zip"
    z2 = tmp_path / "two.zip"
    make_zip(z1, **{"a.xml": b'<testsuite><testcase name="x"><failure message="f"/></testcase></testsuite>'})
    make_zip(z2, **{"b.xml": b'<testsuite><testcase name="y"><failure message="g"/></testcase></testsuite>'})
    lines, failing, problems = digest([str(z1), str(z2)])
    assert failing == 2
    assert problems == 0
    joined = "\n".join(lines)
    assert "one.zip:a.xml" in joined
    assert "two.zip:b.xml" in joined
