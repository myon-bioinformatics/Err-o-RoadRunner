#!/usr/bin/env python3
"""Generate deliberate failure inputs and golden CLI outputs."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "fixtures" / "failure_suite"
INPUTS = SUITE / "inputs"
GOLDEN = SUITE / "golden"
EMPTY = SUITE / "empty_root"
MANIFEST = SUITE / "manifest.json"


def run_fail(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, check=False)
    return proc.stderr or proc.stdout


def triage_out(**kwargs: object) -> str:
    cmd = [
        sys.executable,
        "-m",
        "err_o_roadrunner",
        "--exit-code",
        "1",
        "--project-root",
        str(EMPTY),
    ]
    for key, val in kwargs.items():
        if val is None:
            continue
        if key == "stderr":
            cmd.extend(["--stderr", str(val)])
        elif key == "log":
            cmd.extend(["--log", str(val)])
        elif key == "actions_log":
            cmd.extend(["--actions-log", str(val)])
        elif key == "junit":
            for path in val:
                cmd.extend(["--junit", str(path)])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.replace(str(ROOT.resolve()), "<repo>")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
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
        err = run_fail(cmd)
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
    run_fail([sys.executable, "-m", "pytest", "-q", str(mini), f"--junitxml={junit}"], cwd=INPUTS)
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
            err = run_fail(["node", "-e", code])
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


if __name__ == "__main__":
    main()
