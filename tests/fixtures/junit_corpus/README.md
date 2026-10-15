# JUnit failure corpus

Real failing testcases harvested from `myon-bioinformatics/*` GitHub Actions runs.

- **Size:** 329 XML files (101 legacy + 227 harvested 2026-10-15 from 14 public repos); originally **101** XML files embedding **286** failing `<failure>` / `<error>` testcases (plus `flutter_navigation_basic_38139681545_flutter.xml` for an extra golden, not in `manifest.json`).
- **Source:** all public, non-archived repos in `scripts/harvest_sources.json`; failed runs (Copilot review runs skipped) and artifacts whose name contains `junit` / `failure-identity` / `test-report` / `test-results` / `playwright-report` / `pytest-events` / `results`, downloaded via `gh api .../artifacts/{id}/zip` + safe extraction. JSON/JSONL results go to `../results_corpus/`.
- **Incremental:** `python3 scripts/harvest_junit_corpus.py --allow-partial --per-repo 25`, then `scripts/patch_junit_manifest_jobs.py`, then `scripts/capture_junit_corpus_goldens.py --missing-only`. Scheduled version: `docs/ci/harvest-corpus.yml.proposed`.
- **Provenance:** `manifest.json` (`repo`, `run_id`, `artifact`, `job`, `date`). Run `python3 scripts/patch_junit_manifest_jobs.py` after harvest to set `job` to workflow job names.
- **Regenerate:** `python3 scripts/harvest_junit_corpus.py`, patch jobs, then `python3 scripts/capture_junit_corpus_goldens.py`.

`mcp-toolcall-lab` used to yield 0 entries for two reasons: its failed-run list was dominated by Copilot review runs (no artifacts), and `gh run download` rejected its `junit-python-3.11` artifact with "would result in path traversal" even though the zip only contains `pytest.xml`. Both are fixed (Copilot runs skipped; zip API + safe extraction); it now has 9 entries.
