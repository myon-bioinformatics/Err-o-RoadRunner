# Sentinel exit codes

Sentinel uses **inverted control flow** relative to “fail if non-zero”:

| Wrapped outcome | Sentinel signal | Classifier / report |
|-----------------|-----------------|---------------------|
| Exit `0`, no timeout/signal, no JUnit failure | **stop** | Not run |
| Anything else (non-zero exit, timeout, signal, JUnit failure/error, wrapper error) | **go** | Full triage + optional JSON |

## `--exit-mode` (how the sentinel process itself exits)

### `child` (default, CI-safe)

Returns the **wrapped command’s exit code** unchanged (timeout normalization: if the child is killed by timeout and reports `0`, sentinel returns `124`).

Escalation and reports do not change this code. If escalation code crashes, the wrapped code is still returned.

### `signal` (agent / orchestrator hook)

Returns a **RoadRunner-specific** code so downstream automation can branch without parsing logs:

| Code | Meaning |
|------|---------|
| `0` | **stop** — wrapped command finished with exit `0` and no go triggers; no investigation ran |
| `1` | **go** — investigation path ran (triage invoked; report/hooks may have been written) |

Wrapper CLI misuse (missing command, bad flags) still returns `2` in both modes.

## Examples

```bash
# CI: preserve pytest/make exit codes
err-o-roadrunner sentinel -- pytest -q

# Agent: branch on 0 vs 1 from sentinel itself
err-o-roadrunner sentinel --exit-mode signal --report-json /tmp/report.json -- make test
if [ $? -eq 1 ]; then
  agent-read /tmp/report.json
fi
```

See also `docs/sentinel-report-schema.md` for JSON fields emitted only on **go**.
