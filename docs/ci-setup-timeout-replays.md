# CI setup timeout replays

These public runs stopped in setup. The links and facts are observations; the
test reconstructs short Actions log inputs and fresh setup JUnit locally. It
does not download report bodies, store raw logs, rerun GitHub Actions, or
treat the synthetic XML as a recovered historical artifact.

| Observed job | Setup stop | Downstream evidence |
| --- | --- | --- |
| [Ironmate clipboard WebKit](https://github.com/myon-bioinformatics/Ironmate/actions/runs/44517161759/job/135536047309) | OS dependencies, 140 MB requested, 15-minute limit | Clipboard tests did not run; no clipboard files to upload |
| [Ironmate Marp](https://github.com/myon-bioinformatics/Ironmate/actions/runs/44519780880/job/135545149637) | LibreOffice/UNO setup, 92.6 MB requested, 5-minute limit | Marp conversion tests did not run; no Marp JUnit to upload |
| [PySaiyanWeBoo iPhone WebKit emulation, attempt 2](https://github.com/myon-bioinformatics/PySaiyanWeBoo/actions/runs/44509826341/job/135536153757) | System libraries/browser setup, 126 MB requested, 20-minute limit | Browser tests did not run; no platform evidence to upload |

Run the replays with:

```sh
uv run --locked pytest -q tests/test_ci_setup_timeout_replays.py
```

Each replay checks that triage prioritizes the setup timeout over a later
missing-artifact error or warning. A disposable process exits with failure
to exercise fresh setup JUnit through failure-only extraction, leak checks,
and the public `digest` CLI. It produces no product-test JUnit.
