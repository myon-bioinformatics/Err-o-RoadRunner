#!/usr/bin/env python3
"""Set manifest job field by matching artifact names to workflow matrix job names."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "junit_corpus" / "manifest.json"

PYTHON_VER_RE = re.compile(
    r"junit-(?:controlled-)?(?:python-)?(?:py)?(\d+\.\d+)",
    re.IGNORECASE,
)
PYTHON_VERX_RE = re.compile(r"junit-py(\d+)\.x\b", re.IGNORECASE)


def run_jobs(repo: str, run_id: int) -> list[dict]:
    proc = subprocess.run(
        ["gh", "run", "view", str(run_id), "--repo", repo, "--json", "jobs"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    data = json.loads(proc.stdout)
    return data.get("jobs") or []


def _python_version_keys(artifact: str) -> list[str]:
    keys: list[str] = []
    m = PYTHON_VER_RE.search(artifact)
    if m:
        keys.append(m.group(1))
        keys.append(f"({m.group(1)})")
    mx = PYTHON_VERX_RE.search(artifact)
    if mx:
        keys.append("3.x")
        keys.append("py3.x")
    return keys


def resolve_job(artifact: str, jobs: list[dict]) -> str | None:
    names = [j.get("name") or "" for j in jobs if j.get("name")]
    if not names:
        return None

    for key in _python_version_keys(artifact):
        matches = [n for n in names if key in n]
        if len(matches) == 1:
            return matches[0]

    art_low = artifact.lower()
    slug = art_low.replace("junit-", "").replace("controlled-", "")
    for name in names:
        norm = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        if slug and slug in norm:
            if sum(1 for n in names if slug in n.lower().replace(" ", "-")) == 1:
                return name

    keyword_sets = [
        ("flutter-refactor", ("flutter", "refactor")),
        ("flutter-core", ("flutter", "core")),
        ("junit-marp", ("marp",)),
        ("failure-identity", ("identity", "collect")),
        ("junit-controlled", ("controlled", "identity")),
    ]
    for art_needle, job_tokens in keyword_sets:
        if art_needle in art_low:
            matches = [
                n
                for n in names
                if all(tok in n.lower() for tok in job_tokens)
            ]
            if len(matches) == 1:
                return matches[0]

    return None


def main() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache: dict[tuple[str, int], list[dict]] = {}
    for entry in entries:
        key = (entry["repo"], int(entry["run_id"]))
        if key not in cache:
            cache[key] = run_jobs(entry["repo"], key[1])
        entry["job"] = resolve_job(entry["artifact"], cache[key])
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    resolved = sum(1 for e in entries if e.get("job"))
    print(f"Patched {len(entries)} entries ({resolved} resolved, {len(entries) - resolved} null)")


if __name__ == "__main__":
    main()
