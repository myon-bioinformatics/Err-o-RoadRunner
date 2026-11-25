# JUnit failure corpus

Real failing testcases harvested from `myon-bioinformatics/*` GitHub Actions runs.

- **Size:** 329 XML files (101 legacy + 227 harvested 2026-10-15 from 14 public repos); originally **101** XML files embedding **286** failing `<failure>` / `<error>` testcases (plus `flutter_navigation_basic_38139681545_flutter.xml` for an extra golden, not in `manifest.json`).
- **Source:** all public, non-archived repos in `scripts/harvest_sources.json`; failed runs (Copilot review and `Dependabot Updates` runs skipped; only runs last updated within the 14-day raw-result retention window, see `docs/harvest-coverage.md`) and artifacts whose name contains `junit` / `failure-identity` / `test-report` / `test-results` / `playwright-report` / `pytest-events` / `results`, downloaded via `gh api .../artifacts/{id}/zip` + safe extraction. JSON/JSONL results go to `../results_corpus/`.
- **Incremental:** `python3 scripts/harvest_corpus.py harvest --allow-partial --per-repo 25`, then `scripts/harvest_corpus.py redact --corpus-only`, then `scripts/harvest_corpus.py jobs`, then `scripts/capture_goldens.py junit-corpus` (refresh all goldens after redaction). Scheduled version: `.github/workflows/harvest-corpus.yml`.
- **Provenance:** `manifest.json` (`repo`, `run_id`, `artifact`, `job`, `date`). Run `python3 scripts/harvest_corpus.py jobs` after harvest to set `job` to workflow job names.
- **Regenerate:** `python3 scripts/harvest_corpus.py harvest`, re-redact the corpus, patch jobs, then `python3 scripts/capture_goldens.py junit-corpus`.

`mcp-toolcall-lab` used to yield 0 entries for two reasons: its failed-run list was dominated by Copilot review runs (no artifacts), and `gh run download` rejected its `junit-python-3.11` artifact with "would result in path traversal" even though the zip only contains `pytest.xml`. Both are fixed (Copilot runs skipped; zip API + safe extraction); it now has 9 entries.

The scheduled workflow reuses `junit_redact.py` before golden generation and validates both XML and generated goldens for leaks before opening a PR. Redaction or validation failures block PR creation; collection coverage reports are preserved even on failure.

## Historical unavailable evidence

Seven historical xprobe `controlled-failure/junit.xml` entries collided with
`junit.xml` entries. The checked-in XML matches only the latter SHA-256 in each
pair; the original runs (41283935484 and 40047853401) list no downloadable
artifacts. `unavailable-evidence.json` retains the original rows and both
checksums without treating the surviving XML or golden as the missing record.
Only available evidence belongs in the active manifest, classifier and
ONTO events. Restore a row only after finding matching bytes, assigning
distinct paths and passing redaction and golden checks.

## Promotion and publication

`golden/*.out` means expected CLI output; generating one does not itself grant Gold status.

| Stage | Required processing | Publication |
| --- | --- | --- |
| Bronze | Download and select failing records | Raw artifact bodies remain temporary on the runner; no corpus upload or PR |
| Silver | Apply shared redaction and pass the XML leak scan | XML may feed golden generation; no corpus PR yet |
| Gold | Generate/refresh goldens, then pass golden consistency, JSON/JSONL checks and the fixture leak scan (including `.out`) | The workflow may open a corpus PR; it never auto-merges |

Collection coverage reports contain repository/run/artifact identifiers, counts and collection errors, not test-result bodies. They remain available on partial collection or failed promotion and do not imply Gold status. Any residual leak or preparation error fails the job and blocks corpus PR creation.
