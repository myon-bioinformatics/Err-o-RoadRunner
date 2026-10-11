# JUnit failure corpus

Real failing testcases harvested from `myon-bioinformatics/*` GitHub Actions runs.

- **Source:** `gh run list --status failure` + `gh run download` for `junit*` / `failure-identity` artifacts.
- **Contents:** only `<failure>` / `<error>` testcases (plus `system-out` / `system-err` when present).
- **Provenance:** `manifest.json` (`repo`, `run_id`, `artifact`, `date`).
- **Regenerate:** `python3 scripts/harvest_junit_corpus.py` then `PYTHONPATH=src python3 scripts/capture_junit_corpus_goldens.py`.

`mcp-toolcall-lab` had no non-expired JUnit XML in recent failure artifacts at harvest time.
