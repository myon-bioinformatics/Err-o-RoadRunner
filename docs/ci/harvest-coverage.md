# Harvest coverage

The installed `.github/workflows/harvest-corpus.yml` and its proposed copy are identical.
Both use a 90-minute timeout, preserve the JSON/Markdown reports even on collection
failure, publish Markdown to the job summary, and reuse it verbatim as the generated
corpus PR body. No automatic merge is performed.

The JSON `repositories` mapping records each requested repository, its status,
number of failed runs returned, checked result files, and failures with stage,
run ID, artifact name and reason. `failures` also contains the aggregate list.

- `complete`: a valid failed-run query confirmed no relevant failed runs, or at
  least one readable test result was inspected without collection failures.
- `partial`: at least one readable test result was inspected, but other work in
  that repository failed or an artifact expired.
- `unavailable`: no readable result was confirmed and the repository could not be
  confirmed to have no failed runs. Empty/malformed artifacts, missing result
  artifacts, API/download failures and expired-only artifacts fall here.

With `--allow-partial`, at least one `complete` or `partial` repository is required.
If every repository is unavailable (or the source list is empty), exit status is 1
and corpus manifests/files are untouched. Expiration remains visible; it is never
evidence that no failures occurred. Without `--allow-partial`, any recorded failure
still prevents writes and exits 1. All statuses describe the configured recent-run
window and per-repository new-entry limit, rather than unlimited historical coverage.

A successful empty run query is affirmative evidence that no collection is needed
in that scope, so it counts as complete even without an artifact. A failed run
query returning invalid/empty response text does not count as that evidence.
Zero new entries means only that no new entries were found in confirmed scope;
`status`, `repositories`, and `failures` must be checked for collection gaps. Existing
corpus entries do not establish current coverage: their artifacts are rechecked.

The schedule itself is not run as a PR check: its cross-repository permissions and
PR creation still require validation after placement on main. Offline regression
tests exercise collection failure, partial/full success, empty run lists, expiration,
invalid responses, missing targets and already recorded artifacts.
