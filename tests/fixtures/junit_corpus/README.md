# JUnit failure corpus

Real failing testcases harvested from `myon-bioinformatics/*` GitHub Actions runs.

- **Size:** **101** XML files embedding **286** failing `<failure>` / `<error>` testcases (plus `flutter_navigation_basic_38139681545_flutter.xml` for an extra golden, not in `manifest.json`).
- **Source:** `gh run list --status failure` + `gh run download` for `junit*` / `failure-identity` artifacts.
- **Provenance:** `manifest.json` (`repo`, `run_id`, `artifact`, `job`, `date`). Run `python3 scripts/patch_junit_manifest_jobs.py` after harvest to set `job` to workflow job names.
- **Regenerate:** `python3 scripts/harvest_junit_corpus.py`, patch jobs, then `python3 scripts/capture_junit_corpus_goldens.py`.

`mcp-toolcall-lab` had no non-expired JUnit XML in recent failure artifacts at harvest time.
