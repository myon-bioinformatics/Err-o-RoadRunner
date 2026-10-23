# Sentinel error squads

On the **go** path of `err-o-roadrunner sentinel` (escalation on failure), JUnit failures are broken down into machine-readable **records**, and parallel **squads** gather extra clues. No network is used by default.

## Purpose

- Keep per-testcase failures (`error_type`, message, `signature`, `test_id`, `source_file` / line from the traceback), and merge multiple JUnit files (e.g. `py3.11` … `py3.15`) as **variants**.
- Keep `--group-by error_type` from collapsing into `JUnitFailure` (records take priority over the older triage-line normalization).
- Put hypotheses from code, history, and (optionally) the web into `hypotheses` and `fix_candidates.source_squads`.

## CLI

| Flag | Default | Description |
|--------|------|------|
| `--squads` | `code,history` | Comma-separated `web,code,history`, `all`, or `none` |
| `--squad-timeout` | `10` | Limit per squad (seconds) |
| `--squad-budget` | `30` | Overall wall-clock budget (seconds) |
| `--squad-workers` | `3` | Number of `ThreadPoolExecutor` workers |
| `--offline` | off | Skip the web squad (no network) |
| `--corpus` | Auto if the repository has `tests/fixtures/junit_corpus` | JUnit corpus directory (repeatable). **Used only for the history squad's signature index** (not included in the current failure `records`) |
| `--web-endpoint` | GitHub Search API | URL prefix allowlist for the web squad (repeatable) |
| `--project-root` | `.` | Root for the code squad and contamination checks |

The **web squad** runs only when `--squads` includes `web` and `--offline` is not set.

## Squad return value

Each squad never lets an exception escape and returns this shape:

```json
{
  "name": "code",
  "status": "ok",
  "elapsed_ms": 120,
  "findings": [],
  "hypotheses": []
}
```

`status` is `ok` / `timeout` / `error` / `skipped`. The process exit code is not changed.

### code

- Locate the test file from `test_id` and read a few lines around the failing line.
- List imports with `ast` and put unresolved ones in `unresolved_imports`.
- Detect `sys.path.insert` / `sys.modules` changes in `tests` / `conftest`.

### history

- `skipped` when there is no `.git`.
- `git log -n20` and `git blame -L` (porcelain) per failing file.
- Match the first and last appearance of the same `signature` in the `--corpus` XML, and may add a **flaky** marker based on pass/fail of sibling variants.

### web

- Build a search query from `error_type`, the normalized message, and the guessed framework, and always output `queries[]`.
- Only `https://api.github.com/search/issues` by default. Results are `{title, url, score}`, at most 5. Paths and tokens are stripped with `junit_redact.redact_text`.

## Hypothesis merging

- Identical `summary` values across squads are combined, with `source_squads` unioned.
- `merge_squad_hypotheses` runs **HYPOTHESIS_RULES** over `records` in addition to the `hypotheses` returned by squads, and merges results by the same `summary` key (parallel agents only append rules to the end of the list).
- **import-path contamination** (H1): `rule_import_cascade` — when there are several `ModuleNotFoundError` / `AttributeError` for short module names that exist in the repository, list the test IDs in `explains` as a root-cause candidate.
- **stale installed metadata or env override**: `rule_stale_package_metadata` — when parametrized cases of the same test function split `assert_left` into several values and the name or message mentions version / metadata / pyproject.
- **matrix-invariant failure** (M line): `rule_matrix_invariance` — classify as `invariant` (repository content or logic suspected) when every matrix cell has the same failure, `os-specific` when only some OSes fail, and `python-specific` when only some Pythons fail. Matrix hypotheses appear in the report even with `--squads none`.

## Extension points (`squad.py`)

Parallel agents **append functions to the end of the existing lists**. Do not insert at the front or replace existing entries (where order matters, see the table).

| List | Caller | Signature | Return value | Order |
|--------|------------|------------|--------|------|
| `EXCEPTION_PARSERS` | `_exception_type` | `(message: str, blob: str, first_line: str) -> str \| None` | Exception type name, `None` if undetermined | The first parser to succeed wins. `Unknown` if all return `None`. |
| `RECORD_FIELD_PARSERS` | `parse_junit_failure_records` | `(message: str, blob: str, first_line: str) -> dict \| None` | Optional fields merged into the record (e.g. overriding `error_type`, `expected_exception`). `None` if undetermined | Each parser's dict is applied with `update` in order. An `error_type` key takes priority over `_exception_type` when building. |
| `TRACEBACK_LOCATION_PARSERS` | `_traceback_location` | `(blob: str) -> (file, line) \| None` | Tuple of file path and line number, `None` if undetermined | The first result with a non-empty file path wins. `source_file` is empty if none match. |
| `VARIANT_PARSERS` | `variant_from_artifact` | `(path: str) -> str \| None` | Variant name (e.g. `py3.12`, `windows:38186460415`), `None` if undetermined | The first non-`None` result wins. `unknown` if all are `None`. |
| `VARIANT_AXES_PARSERS` | `parse_junit_failure_records` / `merge_failure_records` | `(artifact: str) -> dict[str, str] \| None` | Partial dict of matrix axes `os` / `python` / `flavor` (same shape as S3's `variant_axes`) | The first non-`None` result goes into `variant_axes` / `matrix_cells`. Axes are unioned when merging. |
| `LANGUAGE_DETECTORS` | `parse_junit_failure_records` (when building records) | `(record: Mapping[str, Any]) -> str \| None` | e.g. `python`, `None` if undetermined | The first non-`None` language is set on `records[].language`. `language` is `null` if all are `None`. |
| `HYPOTHESIS_RULES` | `merge_squad_hypotheses` (and `build_cascade_hypotheses`) | `(records, project_root, squad_names) -> list[dict]` | List of hypothesis dicts (`summary`, `confidence`, `explains`, optional `id`) | Each rule's output is merged in order. Identical `summary` values union `source_squads` and concatenate `explains`. |
| `FIX_RULES` | `enrich_fix_candidates` | `(candidates, hypotheses) -> list[dict] \| None` | Updated candidate list, `None` if unchanged | Rules are applied in order. Rules returning `None` are skipped; a non-`None` return replaces the candidate list (import cascade, Dart import, editable reinstall suggestions, etc.). |

For `AssertionError`, `parse_junit_failure_records` uses `extract_assert_operands` to add `assert_left` / `assert_right` (paths redacted, up to 80 characters) right after `signature`. `summarize_top_causes` returns up to 3 top signatures for large JUnit runs (`top_causes` in the report JSON). Hypothesis `id`s (`H1` …) are assigned all at once by `merge_squad_hypotheses` after merging.

`records[]` carries `language` from JUnit parsing (by default only Python JUnit is detected, as `python`). `records_to_normalized_errors` prefers the record's `language` and falls back to the older guess only when it is missing.

## Report JSON

Optional fields in `docs/sentinel-report-schema.md`:

- `records[]` — merged failure records (with `variants`)
- `squads[]` — the squad results above
- `hypotheses[]` — `{id, summary, confidence, explains, source_squads}`

## Standard output

- **short**: lines such as `S code ok 120ms 4 findings`, `H1 import-path contamination (explains 9/19)`, `M fails on all 7 variants (os: 3/3, python: 5/5)`.
- **porcelain**: `squad` lines — columns are `squad`, name, `status`, elapsed ms, findings count (tab-separated).
- **table**: a column per squad name to the right of the group table (each `status` on the TOTAL row).

The implementation is in `squad.py` (standard library only) and is called from `sentinel.py` on go.
