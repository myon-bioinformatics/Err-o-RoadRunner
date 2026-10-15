#!/usr/bin/env python3
"""Regenerate junit corpus golden CLI outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"
EMPTY = ROOT / "tests" / "fixtures" / "failure_suite" / "empty_root"


def normalize(text: str) -> str:
    return (
        text.replace(str(ROOT.resolve()), "<repo>")
        .replace("/workspace", "<repo>")
    )


def main() -> None:
    missing_only = "--missing-only" in sys.argv[1:]
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in entries:
        xml = CORPUS / entry["xml"]
        golden = CORPUS / entry["golden"]
        if missing_only and golden.exists():
            continue
        golden.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "triage",
            "--exit-code",
            "1",
            "--project-root",
            str(EMPTY),
            "--junit",
            str(xml),
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        golden.write_text(normalize(proc.stdout), encoding="utf-8")
    print(f"Wrote {len(entries)} goldens")


if __name__ == "__main__":
    main()
