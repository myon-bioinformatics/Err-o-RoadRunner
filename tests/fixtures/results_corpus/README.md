# Results corpus (JSON / JSONL)

Real failing test results in non-JUnit formats, harvested by `scripts/harvest_junit_corpus.py`
alongside the JUnit corpus and redacted the same way (`junit_redact.redact_text`, host/timestamp fields normalised, long strings truncated).

Formats: `pytest-events-jsonl` (xprobe pytest plugin), `failures-json` (xprobe `failures.json`), `flutter-machine-jsonl`,
`playwright-json`, generic `status-json` / `status-jsonl` (records whose `outcome`/`status`/`result` is a failure).
Only failing records are kept. Provenance is in `manifest.json`. Validated by `tests/test_results_corpus.py`.
