# Sentinel escalation report (JSON)

Stable schema id: **`sentinel-report/v1`**

The `err-o-roadrunner sentinel` wrapper keeps a low-cost ring buffer while a command runs.

**Stop / go (inverted logic):** wrapped exit `0` with no timeout, signal, or JUnit failure is a **stop** signal — triage and this JSON are **not** produced. Any other outcome is **go** — the read-only `triage.py` pipeline runs and an optional JSON document is written.

Process exit codes: see **`docs/sentinel-exit-codes.md`** (`--exit-mode child` vs `signal`).

## Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `schema` | string | Always `sentinel-report/v1` for this revision. |
| `mode` | string | `escalated` when the report is produced. |
| `escalation_reasons` | string[] | Machine tags, e.g. `non_zero_exit`, `timeout`, `signal:SIGTERM`, `junit_failure`. |
| `command` | object | `argv`, shell-safe `reproduce_command`, optional `cwd`. |
| `timing` | object | `elapsed_seconds`, `started_monotonic`. |
| `outcome` | object | `exit_code`, `timed_out`, `signal`, `junit_failure`. |
| `capture` | object | Redacted `stdout_tail` and `stderr_tail` from ring buffers. |
| `environment` | object | Redacted env summary (`variable_names`, `values_redacted`, …). |
| `triage` | object | `found`, `not_found`, `hints` lists (same semantics as CLI triage lines). |
| `locations` | object[] | Parsed file/line hints from FOUND lines. |
| `next_investigation_steps` | string[] | Suggested follow-ups for humans/agents. |
| `human_summary` | string | Markdown block suitable for logs or `GITHUB_STEP_SUMMARY`. |
| `diagnostic_hooks` | object[] | Present when `--hook` was used; per-hook `command`, `exit_code`, `stdout`, `stderr`. |

## Triage line semantics

Inside `triage.found`, `triage.not_found`, and `triage.hints`, strings match the plain report without the `FOUND:` / `NOT FOUND:` / `HINT:` prefixes. Classification rules are identical to the root `triage` CLI (including JUnit aggregation, redaction, and the standard-error HINT floor).

## CLI

```bash
err-o-roadrunner sentinel --report-json /tmp/sentinel.json --print-triage -- \
  pytest -q tests/test_example.py

err-o-roadrunner sentinel --timeout 600 --junit reports/junit.xml --summary -- \
  make test
```

| Flag | Purpose |
|------|---------|
| `--ring-bytes N` | Per-stream tail size (default 65536). |
| `--timeout SECONDS` | Kill wrapped command after limit. |
| `--junit PATH` | Repeatable; failures escalate even if exit code is 0. |
| `--actions-log PATH` | Include Actions log text in triage. |
| `--project-root DIR` | Lock-file detection root. |
| `--report-json PATH` | Write this schema to disk on escalation only. |
| `--summary` | Append `human_summary` to `GITHUB_STEP_SUMMARY` when set. |
| `--hook SHELL` | Repeatable diagnostic command on escalation. |
| `--print-triage` | Emit FOUND/NOT FOUND/HINT lines to stdout on escalation. |
| `--exit-mode child\|signal` | `child` (default): wrapped exit code. `signal`: `0`=stop, `1`=go. |

With `--exit-mode child` (default), the process exit code is the wrapped command's (or `124` after timeout). Escalation failures never override it. With `--exit-mode signal`, see `docs/sentinel-exit-codes.md`.
