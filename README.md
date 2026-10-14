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
2. **Python standard library only:** the main implementation lives in root-level modules (`triage.py`, `actions_log.py`, `junit_redact.py`, `project_version.py`).
3. **Node is optional:** Node stack parsing is regex-based on log text; no Node runtime is required for triage.
4. **Read-only:** it never changes code or settings, and it never posts or sends anything.
5. **Separates facts from guesses:** it shows `FOUND` and `NOT FOUND` explicitly, and labels inferences as `HINT`.
6. **Independent of AI agents:** the report can be read by humans and by agents alike, and the tool works on its own.
7. **Not tied to GitHub Actions:** it is meant to be called from local runners and wrappers, and Actions is not required.

## Status

**Current slice:** read-only triage for Python tracebacks, Node stacks, JUnit XML, and downloaded GitHub Actions job logs (`--actions-log`). Summarize a downloaded CI artifact zip (or directory / single XML or JSON) in place with `digest` (JUnit counts and failing cases, receipt JSON fields, extension counts for everything else).

## CLI usage

Install the distribution as **`err-o-roadrunner`** (PyPI-style name; matches the `err-o-roadrunner` console script):

```bash
pip install -e .
```

After installing, run:

```bash
err-o-roadrunner --exit-code "$?" --stderr /path/to/stderr.txt --junit reports/junit.xml
```

### Version string policy

`--version` and `triage.__version__` resolve in this order:

1. **`pyproject.toml` next to `triage.py`** when the file is readable UTF-8, parses as TOML with stdlib `tomllib`, `[project]` name matches `err-o-roadrunner` under [PEP 503](https://peps.python.org/pep-0503/) normalization, and `version` is a non-empty string after stripping.
2. **Installed distribution metadata** for `err-o-roadrunner` (for example after `pip install`).
3. **`unknown`** when neither source is usable.

Importing `triage` never crashes on version lookup; if the version is `unknown`, `--version` prints `err-o-roadrunner unknown` (exit code 0) and all other CLI behaviour is unchanged.

Or as a module:

```bash
python3 triage.py --exit-code 1 --log failure.log --project-root .
```

### Artifact digest (CI zip / directory)

Bound summary of a downloaded Actions artifact without extracting it:

```bash
python3 -S triage.py digest artifacts.zip
python3 -S triage.py digest junit-dir/ results.xml --limit 5 --width 300
python3 -S triage.py digest evidence.zip --json route-audit.json --keys stage,failed_step
```

Per JUnit XML: one counts line and one `FAIL class::name: message` line per failing testcase (`--limit`, `--width`). Matching receipt JSON (`route-audit.json`, `*receipt*.json` by default) prints only `--keys` fields. Other members are counted by extension (`--list` names them). Oversize members and unsafe XML (DOCTYPE/ENTITY) are reported without aborting the whole run.

| Digest exit code | Meaning |
|------------------|---------|
| `0` | No failing testcase |
| `1` | At least one failing testcase |
| `2` | Input error (missing path, bad zip, etc.) |

| Flag | Purpose |
|------|---------|
| `--exit-code N` | Exit code from the failed command |
| `--stderr PATH` | Stderr capture file (`-` for stdin) |
| `--log PATH` | Log file (same safety rules as stderr) |
| `--actions-log PATH` | Downloaded GitHub Actions job log (`gh run view --log`) |
| `--junit PATH` | JUnit XML file (repeatable) |
| `--project-root DIR` | Directory checked for lock files (default: `.`) |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Triage finished and the report was printed |
| `2` | Invalid CLI usage (also used by `digest` for input errors) |

The default triage mode always exits `0` after printing a report. The `digest` subcommand additionally exits `1` when JUnit reports failing testcases.

Triage does not re-raise the original failure; it always aims to print a report instead of crashing.

## Real report examples

These samples were captured by running the real CLI on real failures (see `tests/fixtures/readme/` and `tests/test_readme_golden.py`). Paths appear as `<repo>`; runtime version lines match the machine that recorded them.

### 1. Python ModuleNotFoundError

*FOUND names the missing module; HINT suggests search terms.*

**Command**

```bash
python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_module_not_found.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'totally_missing_pkg_xyz'
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python exception type: ModuleNotFoundError
FOUND: python exception message: No module named 'totally_missing_pkg_xyz'
FOUND: python traceback frame (first): <string>:1 in <module>
FOUND: python traceback frame (last): <string>:1 in <module>
FOUND: python traceback frame count: 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: web search keywords: ModuleNotFoundError No module named 'totally_missing_pkg_xyz'
```

### 2. Python ImportError

*FOUND captures ImportError and the import stack frame.*

**Command**

```bash
python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_import_error.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
Traceback (most recent call last):
  File "<repo>/tests/fixtures/readme/inputs/_import_err_helper.py", line 1, in <module>
    from json import ThisNameDoesNotExist
ImportError: cannot import name 'ThisNameDoesNotExist' from 'json' (/usr/lib/python3.12/json/__init__.py)
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python exception type: ImportError
FOUND: python exception message: cannot import name 'ThisNameDoesNotExist' from 'json' (/usr/lib/python3.12/json/__init__.py)
FOUND: python traceback frame (first): <repo>/tests/fixtures/readme/inputs/_import_err_helper.py:1 in <module>
FOUND: python traceback frame (last): <repo>/tests/fixtures/readme/inputs/_import_err_helper.py:1 in <module>
FOUND: python traceback frame count: 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: web search keywords: ImportError cannot import name 'ThisNameDoesNotExist' from 'json' (/usr/lib/python3.12/json/__init__.py)
```

### 3. Python chained exceptions

*FOUND reports both blocks; primary vs chained labels separate causes.*

**Command**

```bash
python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_chained.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
Traceback (most recent call last):
  File "<repo>/tests/fixtures/readme/inputs/chained.py", line 2, in <module>
    1 / 0
    ~~^~~
ZeroDivisionError: division by zero

The above exception was the direct cause of the following exception:

...
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python chained traceback blocks: 2
FOUND: python exception type (primary): ZeroDivisionError
FOUND: python exception message (primary): division by zero
FOUND: python traceback frame (first) (primary): <repo>/tests/fixtures/readme/inputs/chained.py:2 in <module>
FOUND: python traceback frame (last) (primary): <repo>/tests/fixtures/readme/inputs/chained.py:2 in <module>
FOUND: python traceback frame count (primary): 1
FOUND: python exception type (chained-1): ValueError
FOUND: python exception message (chained-1): while handling
FOUND: python traceback frame (first) (chained-1): <repo>/tests/fixtures/readme/inputs/chained.py:4 in <module>
FOUND: python traceback frame (last) (chained-1): <repo>/tests/fixtures/readme/inputs/chained.py:4 in <module>
FOUND: python traceback frame count (chained-1): 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: web search keywords: ValueError while handling
```

### 4. Python SyntaxError

*FOUND reports SyntaxError even without a full traceback header.*

**Command**

```bash
python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/python_syntax_error.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
  File "<repo>/tests/fixtures/readme/inputs/bad_syntax.py", line 1
    print('unclosed
          ^
SyntaxError: unterminated string literal (detected at line 1)
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python exception type: SyntaxError
FOUND: python exception message: unterminated string literal (detected at line 1)
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: web search keywords: SyntaxError unterminated string literal (detected at line 1)
```

### 5. Log line after traceback (INFO)

*HINT records INFO as a trailing colon line, not the exception type.*

**Command**

```bash
python3 triage.py --exit-code 1 --log tests/fixtures/readme/inputs/python_with_info_trailing.log --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
KeyError: 'missing'
INFO: retrying in 5s
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python exception type: KeyError
FOUND: python exception message: 'missing'
FOUND: python traceback frame (first): <string>:1 in <module>
FOUND: python traceback frame (last): <string>:1 in <module>
FOUND: python traceback frame count: 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: trailing colon line: INFO: retrying in 5s
HINT: web search keywords: KeyError 'missing'
```

### 6. pytest assertion (JUnit XML)

*FOUND pulls failure text from JUnit without re-running pytest.*

**Command**

```bash
python3 triage.py --exit-code 1 --junit tests/fixtures/readme/inputs/pytest_assertion.junit.xml --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0" time="0.009" timestamp="2026-10-11T13:44:37.956493+00:00" hostname="cursor" /></testsuites>
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: junit failures (<repo>/tests/fixtures/readme/inputs/pytest_assertion.junit.xml)
NOT FOUND: junit errors (<repo>/tests/fixtures/readme/inputs/pytest_assertion.junit.xml)
NOT FOUND: junit skipped (<repo>/tests/fixtures/readme/inputs/pytest_assertion.junit.xml)
NOT FOUND: lock files in project root
```

### 7. Hostile OSC sequences stripped

*Report output contains no escape bytes; exception facts still FOUND.*

**Command**

```bash
python3 triage.py --exit-code 1 --stderr tests/fixtures/readme/inputs/hostile_osc.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
<OSC-8-bytes>Traceback (most recent call last):
  File "<string>", line 1, in <module>
RuntimeError: hostile demo
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: python exception type: RuntimeError
FOUND: python exception message: hostile demo
FOUND: python traceback frame (first): <string>:1 in <module>
FOUND: python traceback frame (last): <string>:1 in <module>
FOUND: python traceback frame count: 1
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: lock files in project root
HINT: web search keywords: RuntimeError hostile demo
```

### 8. Node Module Not Found

*FOUND captures Node headline and stack frames from real node output.*

**Command**

```bash
python3 triage.py --exit-code 1 --log tests/fixtures/readme/inputs/node_module_not_found.stderr --project-root tests/fixtures/readme/empty_root
```

**Input (trimmed)**

```text
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'missing-xyz-module-123' imported from <repo>/[eval]
    at Object.getPackageJSONURL (node:internal/modules/package_json_reader:268:9)
    at packageResolve (node:internal/modules/esm/resolve:768:81)
    at moduleResolve (node:internal/modules/esm/resolve:854:18)
    at defaultResolve (node:internal/modules/esm/resolve:984:11)
    at ModuleLoader.defaultResolve (node:internal/modules/esm/loader:685:12)
    at #cachedDefaultResolve (node:internal/modules/esm/loader:634:25)
    at ModuleLoader.resolve (node:internal/modules/esm/loader:617:38)
...
```

**Report (stdout)**

```text
FOUND: exit code: 1
FOUND: node error code: ERR_MODULE_NOT_FOUND
FOUND: node stack frame (first): node:internal/modules/package_json_reader:268 in Object.getPackageJSONURL
FOUND: node stack frame (last): node:diagnostics_channel:344 in TracingChannel.tracePromise
FOUND: node stack frame count: 10
FOUND: python version: 3.12.3 (cpython)
FOUND: node version: v22.14.0
FOUND: gh version: gh version 2.102.0 (2026-09-30)
FOUND: environment variable count: 38
FOUND: environment variable names: AGENT_TRANSCRIPTS, CARGO_HOME, CURSOR_AGENT, CURSOR_AGENT_SOCKET, CURSOR_CONVERSATION_ID, CURSOR_REQUEST_ID, CURSOR_RIPGREP_PATH, CURSOR_SPAWN_CWD_HOP, CURSOR_WORKLOAD_CGROUP, DISPLAY, EXEC_DAEMON_STARTUP_TRACEPARENT, FORCE_COLOR, GH_TELEMETRY, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_LFS_SKIP_SMUDGE, HOME, LANG, LC_ALL, NO_COLOR, NVM_BIN, NVM_CD_FLAGS, NVM_DIR, NVM_INC, OLDPWD, PATH, PS1, PWD, RUSTUP_HOME, RUST_VERSION, SHELL, SHLVL, TERM, USER, VNC_DPI, VNC_RESOLUTION, _, _ZO_DOCTOR, __CURSOR_SANDBOX_ENV_RESTORE
NOT FOUND: node error headline
NOT FOUND: lock files in project root
HINT: web search keywords: ERR_MODULE_NOT_FOUND
```

More captured scenarios (JUnit mix, hostile symlink refusal, additional Node errors) live under `tests/fixtures/readme/` and are checked by `tests/test_readme_golden.py`.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest -q tests
```

Tests are grouped with pytest markers: `success`, `failure`, and `adversarial`.
