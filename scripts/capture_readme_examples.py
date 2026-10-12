#!/usr/bin/env python3
"""Capture real CLI output for README examples and golden tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "tests" / "fixtures" / "readme" / "inputs"
GOLDEN = ROOT / "tests" / "fixtures" / "readme" / "golden"
EMPTY_ROOT = ROOT / "tests" / "fixtures" / "readme" / "empty_root"
MANIFEST = ROOT / "tests" / "fixtures" / "readme" / "manifest.json"


def run_fail(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def triage_cli(
    *,
    exit_code: int,
    stderr: Path | None = None,
    log: Path | None = None,
    junit: list[Path] | None = None,
) -> str:
    cmd = [sys.executable, "-m", "triage", "--exit-code", str(exit_code), "--project-root", str(EMPTY_ROOT)]
    if stderr:
        cmd.extend(["--stderr", str(stderr)])
    if log:
        cmd.extend(["--log", str(log)])
    for path in junit or []:
        cmd.extend(["--junit", str(path)])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"triage failed: {proc.stderr}")
    return proc.stdout.replace(str(ROOT.resolve()), "<repo>")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    INPUTS.mkdir(parents=True, exist_ok=True)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    EMPTY_ROOT.mkdir(parents=True, exist_ok=True)

    examples: list[dict] = []

    # Python ModuleNotFoundError
    _, _, err = run_fail([sys.executable, "-c", "import totally_missing_pkg_xyz"])
    p = INPUTS / "python_module_not_found.stderr"
    write_text(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write_text(GOLDEN / "python_module_not_found.out", out)
    examples.append(
        {
            "id": "python_module_not_found",
            "title": "Python ModuleNotFoundError",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_module_not_found.stderr --project-root tests/fixtures/readme/empty_root",
            "input": p.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_module_not_found.out").relative_to(ROOT).as_posix(),
            "note": "FOUND names the missing module; HINT suggests search terms.",
        }
    )

    # ImportError (bad import from existing package)
    helper = INPUTS / "_import_err_helper.py"
    write_text(helper, "from json import ThisNameDoesNotExist\n")
    _, _, err = run_fail([sys.executable, str(helper)])
    p = INPUTS / "python_import_error.stderr"
    write_text(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write_text(GOLDEN / "python_import_error.out", out)
    examples.append(
        {
            "id": "python_import_error",
            "title": "Python ImportError",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_import_error.stderr --project-root tests/fixtures/readme/empty_root",
            "input": p.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_import_error.out").relative_to(ROOT).as_posix(),
            "note": "FOUND captures ImportError and the import stack frame.",
        }
    )

    # KeyError
    _, _, err = run_fail([sys.executable, "-c", "({'a': 1})['missing']"])
    p = INPUTS / "python_key_error.stderr"
    write_text(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write_text(GOLDEN / "python_key_error.out", out)
    examples.append(
        {
            "id": "python_key_error",
            "title": "Python KeyError",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_key_error.stderr --project-root tests/fixtures/readme/empty_root",
            "input": p.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_import_error.out").relative_to(ROOT).as_posix(),
            "note": "FOUND shows KeyError and the code line frame.",
        }
    )
    examples[-1]["golden"] = (GOLDEN / "python_key_error.out").relative_to(ROOT).as_posix()

    # Chained exception
    chained_py = INPUTS / "chained.py"
    write_text(
        chained_py,
        "try:\n    1 / 0\nexcept ZeroDivisionError as exc:\n    raise ValueError('while handling') from exc\n",
    )
    _, _, err = run_fail([sys.executable, str(chained_py)])
    p = INPUTS / "python_chained.stderr"
    write_text(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write_text(GOLDEN / "python_chained.out", out)
    examples.append(
        {
            "id": "python_chained",
            "title": "Python chained exceptions",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_chained.stderr --project-root tests/fixtures/readme/empty_root",
            "input": p.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_chained.out").relative_to(ROOT).as_posix(),
            "note": "FOUND reports both blocks; primary vs chained labels separate causes.",
        }
    )

    # SyntaxError
    bad = INPUTS / "bad_syntax.py"
    write_text(bad, "print('unclosed\n")
    _, _, err = run_fail([sys.executable, str(bad)])
    p = INPUTS / "python_syntax_error.stderr"
    write_text(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write_text(GOLDEN / "python_syntax_error.out", out)
    examples.append(
        {
            "id": "python_syntax_error",
            "title": "Python SyntaxError",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_syntax_error.stderr --project-root tests/fixtures/readme/empty_root",
            "input": p.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_syntax_error.out").relative_to(ROOT).as_posix(),
            "note": "FOUND reports SyntaxError even without a full traceback header.",
        }
    )

    # INFO line after traceback (append to real KeyError stderr)
    info_log = INPUTS / "python_with_info_trailing.log"
    base = (INPUTS / "python_key_error.stderr").read_text(encoding="utf-8")
    write_text(info_log, base + "INFO: retrying in 5s\n")
    out = triage_cli(exit_code=1, log=info_log)
    write_text(GOLDEN / "python_info_hint.out", out)
    examples.append(
        {
            "id": "python_info_hint",
            "title": "Log line after traceback (INFO)",
            "command": "python3 triage.py --exit-code 1 --log tests/fixtures/readme/inputs/python_with_info_trailing.log --project-root tests/fixtures/readme/empty_root",
            "input": info_log.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "python_info_hint.out").relative_to(ROOT).as_posix(),
            "note": "HINT records INFO as a trailing colon line, not the exception type.",
        }
    )

    # Pytest JUnit assertion failure
    mini_test = INPUTS / "mini_fail_case.py"
    write_text(mini_test, "def assert_fail():\n    assert 1 == 2\n")
    junit_path = INPUTS / "pytest_assertion.junit.xml"
    run_fail(
        [sys.executable, "-m", "pytest", "-q", str(mini_test), f"--junitxml={junit_path}"],
        cwd=INPUTS,
    )
    from junit_redact import redact_xml_document

    junit_path.write_text(redact_xml_document(junit_path.read_text(encoding="utf-8")), encoding="utf-8")
    out = triage_cli(exit_code=1, junit=[junit_path])
    write_text(GOLDEN / "pytest_junit_assertion.out", out)
    examples.append(
        {
            "id": "pytest_junit_assertion",
            "title": "pytest assertion (JUnit XML)",
            "command": "python3 triage.py --exit-code 1 --junit tests/fixtures/readme/inputs/pytest_assertion.junit.xml --project-root tests/fixtures/readme/empty_root",
            "input": junit_path.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "pytest_junit_assertion.out").relative_to(ROOT).as_posix(),
            "note": "FOUND pulls failure text from JUnit without re-running pytest.",
        }
    )

    # Combined JUnit failure/error/skipped
    junit_mix = INPUTS / "junit_mix.xml"
    write_text(
        junit_mix,
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="mix" tests="3" failures="1" errors="1" skipped="1">
  <testcase classname="demo" name="fail_one">
    <failure message="assert 1 == 0" type="AssertionError"/>
  </testcase>
  <testcase classname="demo" name="err_one">
    <error message="boom" type="RuntimeError">boom</error>
  </testcase>
  <testcase classname="demo" name="skip_one">
    <skipped message="not today"/>
  </testcase>
</testsuite>
""",
    )
    out = triage_cli(exit_code=1, junit=[junit_mix])
    write_text(GOLDEN / "junit_mix.out", out)
    examples.append(
        {
            "id": "junit_mix",
            "title": "JUnit failure, error, and skipped",
            "command": "python3 triage.py --exit-code 1 --junit tests/fixtures/readme/inputs/junit_mix.xml --project-root tests/fixtures/readme/empty_root",
            "input": junit_mix.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "junit_mix.out").relative_to(ROOT).as_posix(),
            "note": "FOUND counts failures, errors, and skipped separately.",
        }
    )

    # Hostile: OSC in stderr + symlink refused
    hostile_in = INPUTS / "hostile_osc.stderr"
    _, _, err = run_fail([sys.executable, "-c", "raise RuntimeError('hostile demo')"])
    write_text(hostile_in, "\x1b]8;;https://evil.test\x1b\\" + err)
    out_osc = triage_cli(exit_code=1, stderr=hostile_in)
    write_text(GOLDEN / "hostile_osc.out", out_osc)
    link = INPUTS / "secret_link.stderr"
    target = INPUTS / "python_key_error.stderr"
    if link.exists():
        link.unlink()
    os.symlink(target, link)
    out_link = triage_cli(exit_code=1, stderr=link)
    write_text(GOLDEN / "hostile_symlink.out", out_link)
    examples.append(
        {
            "id": "hostile_osc",
            "title": "Hostile OSC sequences stripped",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/hostile_osc.stderr --project-root tests/fixtures/readme/empty_root",
            "input": hostile_in.relative_to(ROOT).as_posix(),
            "golden": (GOLDEN / "hostile_osc.out").relative_to(ROOT).as_posix(),
            "note": "Report output contains no escape bytes; exception facts still FOUND.",
        }
    )
    examples.append(
        {
            "id": "hostile_symlink",
            "title": "Symlink stderr refused",
            "command": "python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/secret_link.stderr --project-root tests/fixtures/readme/empty_root",
            "input": "symlink -> python_key_error.stderr",
            "golden": (GOLDEN / "hostile_symlink.out").relative_to(ROOT).as_posix(),
            "note": "NOT FOUND explains the symlink was not followed.",
        }
    )

    node = subprocess.run(["which", "node"], capture_output=True, text=True)
    if node.returncode == 0:
        node_scripts = {
            "node_module_not_found": "import('missing-xyz-module-123').catch(e=>console.error(e))",
            "node_type_error": "const x = null; console.error(x.foo)",
            "node_range_error": "const a=1; console.error(a.toString(99))",
            "node_unhandled_rejection": "Promise.reject(new Error('reject me'))",
        }
        for nid, code in node_scripts.items():
            _, _, err = run_fail(["node", "-e", code])
            p = INPUTS / f"{nid}.stderr"
            write_text(p, err)
            out = triage_cli(exit_code=1, log=p)
            write_text(GOLDEN / f"{nid}.out", out)
            examples.append(
                {
                    "id": nid,
                    "title": nid.replace("_", " ").title(),
                    "command": f"python3 triage.py --exit-code 1 --log tests/fixtures/readme/inputs/{nid}.stderr --project-root tests/fixtures/readme/empty_root",
                    "input": p.relative_to(ROOT).as_posix(),
                    "golden": (GOLDEN / f"{nid}.out").relative_to(ROOT).as_posix(),
                    "note": "FOUND captures Node headline and stack frames from real node output.",
                }
            )

    write_text(MANIFEST, json.dumps(examples, indent=2) + "\n")
    print(f"Captured {len(examples)} examples -> {MANIFEST}")


if __name__ == "__main__":
    main()
