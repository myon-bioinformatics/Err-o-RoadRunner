#!/usr/bin/env python3
"""Set manifest job field to workflow job names (not artifact names)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "junit_corpus" / "manifest.json"


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


def guess_job(artifact: str, jobs: list[dict]) -> str:
    names = [j.get("name") or "" for j in jobs]
    art = artifact.lower()
    if "flutter" in art:
        for name in names:
            if "flutter" in name.lower():
                return name
    if "marp" in art:
        for name in names:
            if "marp" in name.lower():
                return name
    if "controlled" in art:
        for name in names:
            low = name.lower()
            if "junit" in low or "collect" in low or "refactor" in low or "identity" in low:
                return name
    if "python" in art or art.startswith("junit-py") or "junit-python" in art:
        for name in names:
            low = name.lower()
            if "python" in low or name.startswith("test"):
                return name
    for job in jobs:
        if job.get("conclusion") == "failure" and job.get("name"):
            return job["name"]
    return names[0] if names else artifact


def main() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache: dict[tuple[str, int], list[dict]] = {}
    for entry in entries:
        key = (entry["repo"], int(entry["run_id"]))
        if key not in cache:
            cache[key] = run_jobs(entry["repo"], key[1])
        entry["job"] = guess_job(entry["artifact"], cache[key])
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Patched {len(entries)} manifest entries")


if __name__ == "__main__":
    main()
