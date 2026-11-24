# ErrORR footprint (`errorr-footprint/v1`)

ErrORR already prints a triage report and, on sentinel escalation only, a
`sentinel-report/v1` JSON file. Neither is a fleet record: no workflow uploads
the sentinel JSON, and the JUnit goldens are text. This schema is the small
stable shape those observations need before ONTO can draw them.

The files under `docs/usage/` are a snapshot. Refresh them from a checkout:

```bash
PYTHONPATH=/path/to/ONTO python3 -S scripts/usage_graph.py from-checkout
```

Without `knowledgegraph` on `PYTHONPATH`, pass `--skip-view`. ONTO is not pinned
here. Its `ontology.violations` and `knowledgegraph.graph_mermaid` already accept
the graph this script builds, so ONTO itself does not need a change.

## `errorr-triage-event/v1` (JSONL)

One object per harvested JUnit golden. Paths and environment names stay in the
golden file; the event keeps the join keys and the headline.

| Field | Meaning |
| --- | --- |
| `schema` | `errorr-triage-event/v1` |
| `repository` | `owner/name` recorded on the corpus row |
| `run_id`, `run_url` | GitHub Actions run that produced the artifact |
| `artifact` | Artifact name |
| `case_id` | Corpus manifest id (unique in one file) |
| `observed_at` | Manifest timestamp |
| `classification` | `well_handled`, `partial`, or `unknown` (same rule as `harvest_corpus.classify`) |
| `exception_type` | Pytest exception type, or the first Dart error line, capped at 80 characters |
| `failure_count`, `error_count` | Integers from the `junit failures` / `junit errors` FOUND lines, or null |
| `found_keys` | Which richness keys were present on FOUND lines (`KEY_FOUND`: pytest exception type, Dart error, collection error, system-err, testcase classname, Playwright error, collection import mismatch; two or more is `well_handled`, one is `partial`) |

`case_id` is a key of the events file after shaping, not of the raw manifest.
Exact repeated rows collapse (`duplicate_manifest_rows`). When several different
rows share one id and one golden path, the event keeps the row whose `sha256`
matches the XML still on disk and lists the others in `colliding_sources`
(`colliding_manifest_rows`). A shared id that points at two files, or at a file
matching none of the recorded hashes, stops the export. `repository` + `run_id`
is not a key: a matrix run has several artifacts.

## `errorr-footprint/v1` (JSON)

Aggregates the events with the adoption snapshot (`docs/usage/adoption.json`):

- `totals` — case counts and exception-type counts
- `repositories[]` — `harvest_source`, `uses[]` (`feature`, `kind`, `evidence`), `triage`
- `renames[]` — older corpus names that now point at another public repository
- `vendor_entries_sourcing_errorr` — count of `vendor-consumers.json` entries whose source repository is ErrORR
- `workflow_invocations_outside_errorr` — workflow calls found outside this repository (empty in the 2026-11-24 snapshot)
- `classification_file_rows` — length of `classification.json` when it is present, which can lag the goldens

`uses[].kind` is a single token such as `documented`, `catalog`, `harvest-step`, or `workflow`.
It is an observation label, not proof that a step ran on a given day.

## ONTO graph

`usage_graph.py graph` prints `{nodes, edges}` for `ontology.violations` and
`knowledgegraph.graph_mermaid`.

| Relation | From | To | `basis` |
| --- | --- | --- | --- |
| `exposes` | Tool | Feature | |
| `observed_use` | Repository | Feature | the use `kind` |
| `harvests` | Feature `corpus-harvest` | Repository | `source-list` |
| `triaged` | Repository | Outcome | case count |
| `same_as` | Repository | Repository | `renamed` |

Classes are `Tool`, `Feature`, `Repository`, and `Outcome`. Repositories with no
use, no harvest listing, and no triage cases stay in the JSON inventory and out
of the diagram, so the picture stays the path from repositories through ErrORR
features to outcomes.

`knowledgegraph.graph_mermaid` does not draw the node `type`. The `name` is the
label. No ONTO change is required for that; a type badge on the node would be
the only addition worth making there later.
