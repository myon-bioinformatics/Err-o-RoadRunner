# Err-o-RoadRunner

Err-o-RoadRunner is a tool that does nothing in normal times. It starts working only after an error has happened. The name combines "Error" and "Road Runner".

Researching the cause of a Python, Node or GitHub Actions CI failure currently takes a lot of time. Most error messages are well known, and a web search often turns up hints. This project automates that first investigation **without depending on an AI agent**. It lists what it found and what it did not find on stdout, as a plain report.

## What it does

- It is woken up only when a test, script or CI job fails, from a runner or a wrapper.
- It reads the information related to the failure, such as stderr, the exit code, JUnit XML and logs.
- It extracts what it can locally, for example:
  - the exception type, the message and the first and last stack frames
  - the related file and line
  - the Python, Node and gh versions, the lock files and the environment variables (names only)
  - any known patterns found
- It writes a report to stdout, one line per item, using these labels:
  - `FOUND: ...`
  - `NOT FOUND: ...`
  - `HINT: ...`
- It can optionally attach web search keywords for the error message (no network calls by default).

## Design principles (draft)

1. **Stays idle in normal times:** it does nothing unless there is an error. It has no daemon and no resident process.
2. **Python standard library only:** the main implementation is a single module (`triage.py`) plus a thin `__main__` entry.
3. **Node is optional:** Node stack parsing is regex-based on log text; no Node runtime is required for triage.
4. **Read-only:** it never changes code or settings, and it never posts or sends anything.
5. **Separates facts from guesses:** it shows `FOUND` and `NOT FOUND` explicitly, and labels inferences as `HINT`.
6. **Independent of AI agents:** the report can be read by humans and by agents alike, and the tool works on its own.
7. **Not tied to GitHub Actions:** it is meant to be called from local runners and wrappers, and Actions is not required.

## Status

**First slice (in progress):** read-only triage CLI for Python tracebacks, basic Node stacks, and JUnit XML. GitHub Actions log parsing is not implemented yet.

## CLI usage

After installing the package (`pip install -e .`), run:

```bash
err-o-roadrunner --exit-code "$?" --stderr /path/to/stderr.txt --junit reports/junit.xml
```

Or as a module:

```bash
python -m err_o_roadrunner --exit-code 1 --log failure.log --project-root .
```

| Flag | Purpose |
|------|---------|
| `--exit-code N` | Exit code from the failed command |
| `--stderr PATH` | Stderr capture file (`-` for stdin) |
| `--log PATH` | Log file (same safety rules as stderr) |
| `--junit PATH` | JUnit XML file (repeatable) |
| `--project-root DIR` | Directory checked for lock files (default: `.`) |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Triage finished and the report was printed |
| `2` | Invalid CLI usage |

Triage does not re-raise the original failure; it always aims to print a report instead of crashing.

### Sample report

```text
FOUND: exit code: 1
FOUND: python exception type: ValueError
FOUND: python exception message: bad widget
FOUND: python traceback frame (first): /app/module.py:10 in run
FOUND: python traceback frame (last): /app/module.py:10 in run
FOUND: python traceback frame count: 1
FOUND: python version: 3.12.8 (cpython)
NOT FOUND: node binary
NOT FOUND: gh binary
NOT FOUND: lock files in project root
HINT: web search keywords: ValueError bad widget
```

(Path and lock-file lines depend on your environment.)

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest -q tests
```

Tests are grouped with pytest markers: `success`, `failure`, and `adversarial`.
