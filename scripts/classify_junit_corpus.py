#!/usr/bin/env python3
"""Classify junit corpus cases by triage report richness."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"

KEY_FOUND = (
    "junit pytest exception type",
    "junit dart error (first)",
    "junit collection error (first)",
    "junit system-err (first line)",
    "junit testcase classname (first)",
)


def run_triage(xml: Path) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "err_o_roadrunner",
            "--exit-code",
            "1",
            "--project-root",
            str(EMPTY),
            "--junit",
            str(xml),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.splitlines()


def classify(lines: list[str]) -> str:
    found = [ln for ln in lines if ln.startswith("FOUND:")]
    if not any("junit failures" in ln or "junit errors" in ln for ln in found):
        return "unknown"
    rich = sum(1 for key in KEY_FOUND if any(key in ln for ln in found))
    if rich >= 2:
        return "well_handled"
    if rich == 1:
        return "partial"
    return "unknown"


def main() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    counts = Counter()
    for entry in entries:
        xml = CORPUS / entry["xml"]
        lines = run_triage(xml)
        bucket = classify(lines)
        counts[bucket] += 1
        rows.append({**entry, "classification": bucket})
    out = CORPUS / "classification.json"
    out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts), indent=2))


if __name__ == "__main__":
    main()
