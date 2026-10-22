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
| `outcome` | object | `exit_code` (normalized, `124` on timeout), `raw_exit_code`, `timed_out`, `signal` (child), `termination_signal` (sentinel kill), `junit_failure`. |
| `capture` | object | Redacted `stdout_tail` and `stderr_tail` from ring buffers. |
| `environment` | object | Redacted env summary (`variable_names`, `values_redacted`, …). |
| `triage` | object | `found`, `not_found`, `hints` lists (same semantics as CLI triage lines). |
| `locations` | object[] | Parsed file/line hints from FOUND lines. |
| `next_investigation_steps` | string[] | Suggested follow-ups for humans/agents. |
| `human_summary` | string | Markdown block suitable for logs or `GITHUB_STEP_SUMMARY`. |
| `fix_candidates` | object[] | Optional. Suggested fixes (never executed). See below. |
| `groups` | object | Optional. Aggregated normalized errors. See below. |
| `records` | object[] | Optional. Per-testcase JUnit failure records (merged across `--junit` / `--corpus`). See below. |
| `squads` | object[] | Optional. Parallel squad results (`name`, `status`, `elapsed_ms`, `findings`, …). |
| `hypotheses` | object[] | Optional. Merged hypotheses (`id`, `summary`, `confidence`, `explains`, `source_squads`). |
| `diagnostic_hooks` | object[] | Present when `--hook` was used; per-hook `command`, `exit_code`, `stdout`, `stderr`. |

Older consumers may ignore `fix_candidates`, `groups`, `records`, `squads`, and `hypotheses`; all other v1 fields are unchanged.

## `fix_candidates[]`

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable label in this report, e.g. `F1`. |
| `kind` | string | `python_import`, `import_cascade`, `missing_command`, `permission`, `timeout`, `node_module`, `assertion`, `junit`, `syntax`, `actions_step`, `generic`. |
| `confidence` | string | `high`, `medium`, or `low`. |
| `evidence` | string[] | Matching `triage.found` / `triage.hints` lines. |
| `location` | object | Optional `loc`, `line`. |
| `suggestion` | string | Human-readable next step. |
| `verify_command` | string | Same as `command.reproduce_command` (re-run to verify). |
| `source_squads` | string[] | Optional. Squad names that supported this candidate (e.g. `code`, `history`). |

Built from a static rule table over triage lines. Deduped by `(kind, loc)`, sorted by confidence then evidence count, capped by `--max-fixes` (default 5). When JUnit `records` are present, grouping uses them first so `error_type` reflects real exception types. Squad hypotheses may prepend import-contamination candidates.

## `records[]` (JUnit failures)

| Field | Type | Description |
|-------|------|-------------|
| `error_type` | string | Parsed exception type (e.g. `ModuleNotFoundError`), not `JUnitFailure`. |
| `message` | string | Normalized failure message excerpt. |
| `signature` | string | Stable key with paths/numbers stripped (for merge and corpus). |
| `test_id` | string | Pytest-style `classname::name`. |
| `source_file` | string | Best-effort file from traceback. |
| `line` | number | Optional failing line. |
| `variants` | string[] | Python versions seen across artifacts (e.g. `py3.11` … `py3.15`). |
| `artifact` | string | Representative source XML filename. |

Records with the same `(signature, test_id)` are merged; `variants` lists all artifact variants.

## `squads[]` / `hypotheses[]`

See **`docs/sentinel-squads.md`** (Japanese) for squad behaviour, CLI flags, and stdout extensions.

## `groups` (normalized error aggregation)

| Field | Type | Description |
|-------|------|-------------|
| `group_by` | string[] | Keys used for this report, e.g. `["error_type"]`. |
| `sort` | string | `count`, `first_seen`, or `name`. |
| `error_count` | number | Total normalized errors. |
| `buckets` | object[] | One bucket per distinct group key. |

Each **normalized error** (inside `buckets[].errors`) includes:

| Field | Type | Description |
|-------|------|-------------|
| `error_type` | string | e.g. `ModuleNotFoundError`, `ValidationError`, `sqlite3.OperationalError`, `psycopg.Error`, `SQLAlchemyError`, or `generic`. |
| `language` | string | `python`, `node`, `shell`, `actions`, `junit`, or `generic`. |
| `phase` | string | `collect`, `import`, `setup`, `test`, `teardown`, `runtime`, `timeout`. |
| `signature` | string | Normalized message (paths, numbers, ids stripped). |
| `location` | object | Optional `loc`, `line`. |
| `kind` | string | Fix-rule kind aligned with `fix_candidates.kind`. |
| `first_seen` | number | Index in `triage.found` order. |
| `message` | string | Raw message excerpt. |
| `source_line` | string | Primary FOUND line. |

Detection uses **stdlib regex rules only** in `sentinel.py`; unknown types become `generic`.

CLI: `--group-by KEY[,KEY]` (`error_type`, `language`, `phase`, `signature`, `location`, `kind`; default `error_type`), `--sort {count,first_seen,name}`.

## Stdout formats (`--format`)

Affects **stdout only** on escalation (or STOP for `short`). `--report-json` always writes the full schema above.

| Format | Behavior |
|--------|----------|
| `full` | Classic `FOUND:` / `NOT FOUND:` / `HINT:` lines (same as `--print-triage`). |
| `short` | Pytest-style summary: status letter, optional detail (`--tb`), `fix:` line, `G` group lines, `== totals ==`. STOP: `sentinel: STOP exit=0 0.05s`. |
| `porcelain` | Tab-separated, stable columns; header `# sentinel-report/v1 …`; `F`/`H`/`?` rows; `C` fix rows; `# totals`. STOP: empty. |
| `table` | Coverage-style table; when `groups` present, lists group buckets (`Group`, `Count`, `First`). |

| Flag | Purpose |
|------|---------|
| `--tb short\|line\|no` | Detail density for `full`/`short` (default `short`). |
| `-v` / `--verbose` | Include `? NOT FOUND` in `short`. |
| `--max-fixes N` | Cap `fix_candidates` (default 5). |

All stdout lines are redacted and capped at **200** characters.

### Porcelain column order (fixed)

Each row is tab-separated. Fields are never reordered within a row type.

| Row kind | Col 1 | Col 2 | Col 3 | Col 4 | Col 5 | Col 6 | Col 7 | Col 8 |
|----------|-------|-------|-------|-------|-------|-------|-------|-------|
| Header | `# sentinel-report/v1` | `mode` | `reproduce_command` | `exit_code` | — | — | — | — |
| `F` | `F` | `found` | `{triage line}` | — | — | — | — | — |
| `H` | `H` | `hint` | `{triage line}` | — | — | — | — | — |
| `?` | `?` | `not_found` | `{triage line}` | — | — | — | — | — |
| `C` | `C` | `id` | `kind` | `confidence` | `loc` | `line` | `suggestion` | `verify_command` |
| `squad` | `squad` | `name` | `status` | `elapsed_ms` | `findings_count` | — | — | — |
| Totals | `# totals` | `found=N` | `hints=N` | `not_found=N` | `fixes=N` | `elapsed=SECONDS` | — | — |

Row order in the stream: **header** → all **F** rows → all **H** rows → all **?** rows → all **C** rows → all **squad** rows → **totals** (single line).

## Triage line semantics

Inside `triage.found`, `triage.not_found`, and `triage.hints`, strings match the plain report without the `FOUND:` / `NOT FOUND:` / `HINT:` prefixes. Classification rules are identical to the root `triage` CLI (including JUnit aggregation, redaction, and the standard-error HINT floor).

## CLI

```bash
err-o-roadrunner sentinel --report-json /tmp/sentinel.json --print-triage --format short --group-by error_type,language -- \
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
| `--print-triage` | Emit triage to stdout on escalation (`--format`, default `full`). |
| `--format full\|short\|porcelain\|table` | Stdout style (default `full`). |
| `--tb short\|line\|no` | Traceback/detail for stdout formats. |
| `-v` | Verbose short output (`? NOT FOUND`). |
| `--max-fixes N` | Cap fix candidates. |
| `--group-by KEY[,KEY]` | Group normalized errors in JSON and short/table stdout. |
| `--sort count\|first_seen\|name` | Sort group buckets. |
| `--exit-mode child\|signal` | `child` (default): wrapped exit code. `signal`: `0`=stop, `1`=go. |
| `--go-on-stderr-traceback` | Opt-in **go** when exit is `0` but stderr contains a Python traceback (default: **stop**). |
| `--squads` | Error squads on escalation (`code,history` default; `web` opt-in; `none` to disable). |
| `--squad-timeout` / `--squad-budget` / `--squad-workers` | Squad time limits and parallelism. |
| `--offline` | Skip network (web squad). |
| `--corpus` | JUnit corpus directory for history-squad signature index only (repeatable; defaults to `tests/fixtures/junit_corpus` under project root when present). Does not add corpus XML to `records` (only `--junit` does). |
| `--web-endpoint` | Allowlisted URL prefix for web squad (repeatable). |

With `--exit-mode child` (default), the process exit code is the wrapped command's (or `124` after timeout). Escalation failures never override it. With `--exit-mode signal`, see `docs/sentinel-exit-codes.md`.

Squad details: **`docs/sentinel-squads.md`**.

Pytest alignment survey: **`docs/pytest-options-survey.md`**.
