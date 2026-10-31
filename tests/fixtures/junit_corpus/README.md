# JUnit failure corpus

Real failing testcases harvested from `myon-bioinformatics/*` GitHub Actions runs.

- **Size:** 329 XML files (101 legacy + 227 harvested 2026-10-15 from 14 public repos); originally **101** XML files embedding **286** failing `<failure>` / `<error>` testcases (plus `flutter_navigation_basic_38139681545_flutter.xml` for an extra golden, not in `manifest.json`).
- **Source:** all public, non-archived repos in `scripts/harvest_sources.json`; failed runs (Copilot review runs skipped) and artifacts whose name contains `junit` / `failure-identity` / `test-report` / `test-results` / `playwright-report` / `pytest-events` / `results`, downloaded via `gh api .../artifacts/{id}/zip` + safe extraction. JSON/JSONL results go to `../results_corpus/`.
- **Incremental:** `python3 scripts/harvest_junit_corpus.py --allow-partial --per-repo 25`, then `scripts/reredact_junit_corpus.py --corpus-only`, then `scripts/patch_junit_manifest_jobs.py`, then `scripts/capture_junit_corpus_goldens.py` (refresh all goldens after redaction). Scheduled version: `.github/workflows/harvest-corpus.yml`.
- **Provenance:** `manifest.json` (`repo`, `run_id`, `artifact`, `job`, `date`). Run `python3 scripts/patch_junit_manifest_jobs.py` after harvest to set `job` to workflow job names.
- **Regenerate:** `python3 scripts/harvest_junit_corpus.py`, re-redact the corpus, patch jobs, then `python3 scripts/capture_junit_corpus_goldens.py`.

`mcp-toolcall-lab` used to yield 0 entries for two reasons: its failed-run list was dominated by Copilot review runs (no artifacts), and `gh run download` rejected its `junit-python-3.11` artifact with "would result in path traversal" even though the zip only contains `pytest.xml`. Both are fixed (Copilot runs skipped; zip API + safe extraction); it now has 9 entries.

The scheduled workflow reuses `junit_redact.py` before golden generation and validates both XML and generated goldens for leaks before opening a PR. Redaction or validation failures block PR creation; collection coverage reports are preserved even on failure.

## Promotion and publication

`golden/*.out` means expected CLI output; generating one does not itself grant Gold status.

| Stage | Required processing | Publication |
| --- | --- | --- |
| Bronze | Download and select failing records | Raw artifact bodies remain temporary on the runner; no corpus upload or PR |
| Silver | Apply shared redaction and pass the XML leak scan | XML may feed golden generation; no corpus PR yet |
| Gold | Generate/refresh goldens, then pass golden consistency, JSON/JSONL checks and the fixture leak scan (including `.out`) | The workflow may open a corpus PR; it never auto-merges |

Collection coverage reports contain repository/run/artifact identifiers, counts and collection errors, not test-result bodies. They remain available on partial collection or failed promotion and do not imply Gold status. Any residual leak or preparation error fails the job and blocks corpus PR creation.
