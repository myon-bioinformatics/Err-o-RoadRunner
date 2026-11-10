#!/usr/bin/env python3
"""Read-only GitHub repository failure scan, using the existing gh login."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BUG = re.compile(r"\b(bug|error|failure|failed|crash|regression|broken|defect)\b", re.I)
TIMEOUT = 45


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"gh exited {result.returncode}: {result.stderr.strip()[:500]}")
    return result.stdout


def load(path: str):
    return json.loads(gh("api", path))


def issues(repo: str, limit: int) -> list[str]:
    items = load(f"repos/{repo}/issues?state=open&per_page={limit}")
    findings = []
    for item in items:
        if "pull_request" in item:
            continue
        labels = ", ".join(str(label.get("name", "")) for label in item.get("labels", []))
        title = str(item.get("title", ""))
        if BUG.search(title + " " + labels):
            findings.append(f"FOUND issue #{item['number']}: {clean(title)} | {item['html_url']}")
    return findings or ["NOT FOUND: bug-like open issues in the fetched page"]


def runs(repo: str, limit: int) -> list[str]:
    data = load(f"repos/{repo}/actions/runs?status=failure&per_page={limit}")
    result = []
    for item in data.get("workflow_runs", []):
        result.append(
            f"FOUND failed run {item['id']}: {clean(item.get('name', ''))} "
            f"branch={clean(item.get('head_branch') or '?')} "
            f"sha={str(item.get('head_sha', ''))[:12]} "
            f"updated={item.get('updated_at', '?')} | {item['html_url']}"
        )
    return result or ["NOT FOUND: failed workflow runs in the fetched page"]


def clean(value: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value)).split())[:300]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", help="owner/repository; default: current gh repository")
    parser.add_argument("--limit", type=int, default=30, help="maximum items per source, 1-100")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    try:
        repo = args.repository or json.loads(gh("repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
        if not REPO.fullmatch(repo):
            raise ValueError("repository must have the form owner/name")
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: repository discovery failed: {clean(str(exc))}", flush=True)
        return 2
    print(f"START: scanning {repo} (up to {args.limit} items per source)", flush=True)
    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fn, repo, args.limit): label for fn, label in ((issues, "issues"), (runs, "workflow runs"))}
        for future in concurrent.futures.as_completed(futures):
            label = futures[future]
            try:
                for line in future.result():
                    print(line, flush=True)
            except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, ValueError, KeyError, TypeError) as exc:
                failed = True
                print(f"ERROR: {label}: {clean(str(exc))}", flush=True)
            print(f"DONE: {label}", flush=True)
    print("NOTE: failed runs are historical observations; check the latest run for the branch before treating them as current failures.", flush=True)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
