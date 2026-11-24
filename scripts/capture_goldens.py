#!/usr/bin/env python3
"""Regenerate golden triage outputs with the runner the golden tests use (tests/support.py).

  python scripts/capture_goldens.py failure-suite                # tests/fixtures/failure_suite
  python scripts/capture_goldens.py readme                       # tests/fixtures/readme
  python scripts/capture_goldens.py readme-section > section.md  # README "Real report examples"
  python scripts/capture_goldens.py junit-corpus [--missing-only]
"""

from __future__ import annotations

import argparse, json, os, subprocess, sys, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from errlib.junit_redact import redact_xml_document
from tests.support import EMPTY, FIXTURES, RUNTIME_LINE_PREFIXES, mask_repo, triage_cli as _triage_cli

README_FIX = FIXTURES / "readme"

SHOWCASE_IDS = [
    "python_module_not_found",
    "python_import_error",
    "python_chained",
    "python_syntax_error",
    "python_info_hint",
    "pytest_junit_assertion",
    "hostile_osc",
    "node_module_not_found",
]


def run_fail(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, check=False)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def triage_golden(project_root: Path, *args: object, exit_code: int = 1) -> str:
    """Golden text: triage stdout with checkout paths as ``<repo>``."""
    return mask_repo(_triage_cli(*args, project_root=project_root, exit_code=exit_code))


def capture_failure_suite() -> None:
    """``failure-suite``: provoke real failures, store inputs, goldens and the manifest."""
    SUITE = FIXTURES / "failure_suite"
    INPUTS, GOLDEN, MANIFEST = SUITE / "inputs", SUITE / "golden", SUITE / "manifest.json"

    def triage_out(**kwargs: object) -> str:
        args: list[object] = []
        for key, val in kwargs.items():
            if key == "junit":
                for path in val:
                    args.extend(["--junit", path])
            elif val is not None:
                args.extend(["--" + key.replace("_", "-"), val])
        return triage_golden(EMPTY, *args)

    INPUTS.mkdir(parents=True, exist_ok=True)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    EMPTY.mkdir(parents=True, exist_ok=True)
    examples: list[dict] = []

    def add(entry: dict) -> None:
        examples.append(entry)

    # Python failures
    cases = {
        "import_error": ([sys.executable, str(INPUTS / "_imp.py")], None),
        "attribute_error": ([sys.executable, "-c", "''.missing"], None),
        "type_error": ([sys.executable, "-c", "1+'a'"], None),
        "recursion_error": ([sys.executable, "-c", "def f():\n f()\nf()"], None),
        "keyboard_interrupt": ([sys.executable, "-c", "raise KeyboardInterrupt"], None),
    }
    write(INPUTS / "_imp.py", "from json import NoSuchName\n")
    for name, (cmd, _) in cases.items():
        proc = run_fail(cmd)
        err = proc.stderr or proc.stdout
        p = INPUTS / f"python_{name}.stderr"
        write(p, err)
        out = triage_out(stderr=p)
        write(GOLDEN / f"python_{name}.out", out)
        add(
            {
                "id": f"python_{name}",
                "category": "python",
                "stderr": str(p.relative_to(ROOT)),
                "golden": str((GOLDEN / f"python_{name}.out").relative_to(ROOT)),
            }
        )

    # Timeout simulation (synthetic traceback text)
    timeout_log = INPUTS / "python_timeout.log"
    write(
        timeout_log,
        textwrap.dedent(
            """\
            Traceback (most recent call last):
              File "worker.py", line 9, in run
                work()
            TimeoutError: operation timed out after 30s
            """
        ),
    )
    write(GOLDEN / "python_timeout.out", triage_out(log=timeout_log))
    add(
        {
            "id": "python_timeout",
            "category": "python",
            "log": str(timeout_log.relative_to(ROOT)),
            "golden": str((GOLDEN / "python_timeout.out").relative_to(ROOT)),
        }
    )

    # pytest junit
    mini = INPUTS / "suite_fail.py"
    write(mini, "def test_fail():\n    assert 0\n")
    junit = INPUTS / "suite.junit.xml"
    run_fail(["uv", "run", "pytest", "-q", str(mini), f"--junitxml={junit}"], cwd=INPUTS)
    junit.write_text(redact_xml_document(junit.read_text(encoding="utf-8")), encoding="utf-8")
    write(GOLDEN / "pytest_junit.out", triage_out(junit=[junit]))
    add(
        {
            "id": "pytest_junit",
            "category": "junit",
            "junit": [str(junit.relative_to(ROOT))],
            "golden": str((GOLDEN / "pytest_junit.out").relative_to(ROOT)),
        }
    )

    # Actions logs (synthetic, gh-style)
    for name in ("python_fail", "workflow_error"):
        src = ROOT / "tests" / "fixtures" / "actions_logs" / f"{name}.log"
        dst = INPUTS / f"actions_{name}.log"
        write(dst, src.read_text(encoding="utf-8"))
        write(GOLDEN / f"actions_{name}.out", triage_out(actions_log=dst))
        add(
            {
                "id": f"actions_{name}",
                "category": "actions",
                "actions_log": str(dst.relative_to(ROOT)),
                "golden": str((GOLDEN / f"actions_{name}.out").relative_to(ROOT)),
            }
        )

    node = subprocess.run(["which", "node"], capture_output=True, text=True)
    if node.returncode == 0:
        for name, code in {
            "reference_error": "null.foo",
            "syntax_error": "syntax error here",
        }.items():
            proc = run_fail(["node", "-e", code])
            err = proc.stderr or proc.stdout
            p = INPUTS / f"node_{name}.stderr"
            write(p, err)
            write(GOLDEN / f"node_{name}.out", triage_out(log=p))
            add(
                {
                    "id": f"node_{name}",
                    "category": "node",
                    "log": str(p.relative_to(ROOT)),
                    "golden": str((GOLDEN / f"node_{name}.out").relative_to(ROOT)),
                }
            )

    write(MANIFEST, json.dumps(examples, indent=2) + "\n")
    print(f"Wrote {len(examples)} failure suite examples")


def capture_readme_examples() -> None:
    """``readme``: provoke the README example failures, store inputs, goldens and the manifest."""
    INPUTS, GOLDEN, EMPTY_ROOT, MANIFEST = (README_FIX / name for name in ("inputs", "golden", "empty_root", "manifest.json"))

    def triage_cli(*, exit_code: int, stderr: Path | None = None, log: Path | None = None, junit: list[Path] | None = None) -> str:
        args: list[object] = []
        if stderr:
            args.extend(["--stderr", stderr])
        if log:
            args.extend(["--log", log])
        for path in junit or []:
            args.extend(["--junit", path])
        return triage_golden(EMPTY_ROOT, *args, exit_code=exit_code)

    INPUTS.mkdir(parents=True, exist_ok=True)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    EMPTY_ROOT.mkdir(parents=True, exist_ok=True)

    examples: list[dict] = []

    # Python ModuleNotFoundError
    err = run_fail([sys.executable, "-c", "import totally_missing_pkg_xyz"]).stderr
    p = INPUTS / "python_module_not_found.stderr"
    write(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write(GOLDEN / "python_module_not_found.out", out)
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
    write(helper, "from json import ThisNameDoesNotExist\n")
    err = run_fail([sys.executable, str(helper)]).stderr
    p = INPUTS / "python_import_error.stderr"
    write(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write(GOLDEN / "python_import_error.out", out)
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
    err = run_fail([sys.executable, "-c", "({'a': 1})['missing']"]).stderr
    p = INPUTS / "python_key_error.stderr"
    write(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write(GOLDEN / "python_key_error.out", out)
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
    write(
        chained_py,
        "try:\n    1 / 0\nexcept ZeroDivisionError as exc:\n    raise ValueError('while handling') from exc\n",
    )
    err = run_fail([sys.executable, str(chained_py)]).stderr
    p = INPUTS / "python_chained.stderr"
    write(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write(GOLDEN / "python_chained.out", out)
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
    write(bad, "print('unclosed\n")
    err = run_fail([sys.executable, str(bad)]).stderr
    p = INPUTS / "python_syntax_error.stderr"
    write(p, err)
    out = triage_cli(exit_code=1, stderr=p)
    write(GOLDEN / "python_syntax_error.out", out)
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
    write(info_log, base + "INFO: retrying in 5s\n")
    out = triage_cli(exit_code=1, log=info_log)
    write(GOLDEN / "python_info_hint.out", out)
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
    write(mini_test, "def assert_fail():\n    assert 1 == 2\n")
    junit_path = INPUTS / "pytest_assertion.junit.xml"
    run_fail(
        ["uv", "run", "pytest", "-q", str(mini_test), f"--junitxml={junit_path}"],
        cwd=INPUTS,
    )
    junit_path.write(redact_xml_document(junit_path.read_text(encoding="utf-8")), encoding="utf-8")
    out = triage_cli(exit_code=1, junit=[junit_path])
    write(GOLDEN / "pytest_junit_assertion.out", out)
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
    write(
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
    write(GOLDEN / "junit_mix.out", out)
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
    err = run_fail([sys.executable, "-c", "raise RuntimeError('hostile demo')"]).stderr
    write(hostile_in, "\x1b]8;;https://evil.test\x1b\\" + err)
    out_osc = triage_cli(exit_code=1, stderr=hostile_in)
    write(GOLDEN / "hostile_osc.out", out_osc)
    link = INPUTS / "secret_link.stderr"
    target = INPUTS / "python_key_error.stderr"
    if link.exists():
        link.unlink()
    os.symlink(target, link)
    out_link = triage_cli(exit_code=1, stderr=link)
    write(GOLDEN / "hostile_symlink.out", out_link)
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
            err = run_fail(["node", "-e", code]).stderr
            p = INPUTS / f"{nid}.stderr"
            write(p, err)
            out = triage_cli(exit_code=1, log=p)
            write(GOLDEN / f"{nid}.out", out)
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

    write(MANIFEST, json.dumps(examples, indent=2) + "\n")
    print(f"Captured {len(examples)} examples -> {MANIFEST}")


def trim_input(path: Path, max_lines: int = 8) -> str:
    if not path.is_file():
        return "(symlink; see command)"
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\x1b]8;;https://evil.test\x1b\\", "<OSC-8-bytes>")
    text = text.replace(str(ROOT.resolve()), "<repo>")
    lines = text.splitlines()
    out = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        out += "\n..."
    return out


def render_readme_section() -> str:
    """``readme-section``: the README 'Real report examples' section from captured goldens."""
    entries = {e["id"]: e for e in json.loads((README_FIX / "manifest.json").read_text(encoding="utf-8"))}
    parts = [
        "## Real report examples",
        "",
        "These samples were captured by running the real CLI on real failures "
        "(see `tests/fixtures/readme/` and `tests/test_goldens.py`). "
        "Paths appear as `<repo>`; runtime version lines match the machine that recorded them.",
        "",
    ]
    for idx, eid in enumerate(SHOWCASE_IDS, start=1):
        entry = entries[eid]
        inp = ROOT / entry["input"]
        golden = (ROOT / entry["golden"]).read_text(encoding="utf-8")
        cmd = entry["command"].replace("python -m", "python3 -m")
        parts.extend(
            [
                f"### {idx}. {entry['title']}",
                "",
                f"*{entry['note']}*",
                "",
                "**Command**",
                "",
                "```bash",
                cmd,
                "```",
                "",
                "**Input (trimmed)**",
                "",
                "```text",
                trim_input(inp),
                "```",
                "",
                "**Report (stdout)**",
                "",
                "```text",
                golden.rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(parts)


def capture_junit_corpus(missing_only: bool = False) -> None:
    """``junit-corpus``: triage output of every corpus case as its golden (``--missing-only``: new ones)."""
    CORPUS = FIXTURES / "junit_corpus"
    entries = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    for entry in entries:
        xml = CORPUS / entry["xml"]
        golden = CORPUS / entry["golden"]
        if missing_only and golden.exists():
            continue
        golden.parent.mkdir(parents=True, exist_ok=True)
        text = triage_golden(EMPTY, "--junit", xml)
        if golden.exists():
            text = keep_runtime_lines(text, golden.read_text(encoding="utf-8"))
        golden.write_text(text, encoding="utf-8")
    print(f"Wrote {len(entries)} goldens")


def keep_runtime_lines(text: str, committed: str) -> str:
    """``text`` with each runtime line (Python/Node/gh version, env names) taken from ``committed``.

    Those lines describe the machine that captured a golden, and the golden comparison masks
    them. Carrying the committed ones over keeps a re-capture byte-for-byte identical unless a
    triage rule changed the report; a prefix the committed golden lacks keeps the new line.
    """
    kept: dict[str, str] = {}
    for line in committed.splitlines():
        prefix = next((p for p in RUNTIME_LINE_PREFIXES if line.startswith(p)), None)
        if prefix is not None and prefix not in kept:
            kept[prefix] = line
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        prefix = next((p for p in RUNTIME_LINE_PREFIXES if body.startswith(p)), None)
        if prefix in kept:
            line = kept[prefix] + line[len(body):]
        out.append(line)
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("failure-suite", help="deliberate failures -> tests/fixtures/failure_suite")
    sub.add_parser("readme", help="README example failures -> tests/fixtures/readme")
    sub.add_parser("readme-section", help="print the README 'Real report examples' section")
    cmd = sub.add_parser("junit-corpus", help="goldens for tests/fixtures/junit_corpus")
    cmd.add_argument("--missing-only", action="store_true", help="only cases without a golden yet")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "failure-suite":
        capture_failure_suite()
    elif args.command == "readme":
        capture_readme_examples()
    elif args.command == "readme-section":
        print(render_readme_section())
    else:
        capture_junit_corpus(args.missing_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
