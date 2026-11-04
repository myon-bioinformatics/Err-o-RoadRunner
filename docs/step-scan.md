# Reuse combined Actions step collection

Step analysis reuses `_collect_log_wide` for official errors, workflow errors and
pytest failures, replacing three separate passes. Cancellation checks reuse the
collected official messages; the redundant second scan used identical matching
and stripping logic. Existing standalone collectors remain available internally.
No error prioritization, output schema, dependencies or network behavior changed.

```sh
python -S -m unittest discover -s tests -p test_step_scan_reuse.py
python -S -m scripts.benchmark_step_scan
```

Two tests compare combined/separate collectors and check ordinary output,
cancellation, failure evidence, body identity and a process-exit message mentioning
timeout. The pinned-baseline benchmark compares every step metadata field on
20000 ordinary lines and the same log with three error records appended.
CPython 3.12.14/Linux, one warmup plus seven alternating timing samples:
ordinary median 13.277 → 11.575 ms; error median 12.146 → 10.951 ms.
Raw evidence is in step-scan-measurement.json. Parsing/input construction and
full-report rendering excluded. No build-time, memory or full-CI speedup claimed.
