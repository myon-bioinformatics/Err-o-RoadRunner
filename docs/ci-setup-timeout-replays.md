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

## Artifact identity when harvesting

The bulk harvester downloads by the unique artifact ID returned for a run,
rather than looking up its display name again. A rerun can list two artifacts
with the same name. New corpus rows include the artifact ID so different
artifacts with the same member path do not overwrite one another. Identical
redacted content is still deduplicated. Existing rows, IDs and file paths are
left intact. For a legacy row, use its digest (or the readable corpus file)
when available; use the old name/path only if its digest is unavailable and
the artifact listing has a unique ID for that name.

An artifact ID identifies a run artifact, not a source job or run attempt.
A future job-specific intake must verify the requested repository, run,
attempt and job, and independently link the artifact to that job. If that
link is ambiguous or unavailable, report it as unverified and collect no
artifact for that job; do not infer the link from the display name or
fall back to a broader harvest.
