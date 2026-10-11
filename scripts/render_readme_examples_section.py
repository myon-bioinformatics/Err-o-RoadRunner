#!/usr/bin/env python3
"""Render the README 'Real report examples' section from captured goldens."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "readme" / "manifest.json"

SHOWCASE_IDS = [
    "python_module_not_found",
    "python_import_error",
    "python_chained",
    "python_syntax_error",
    "python_info_hint",
    "pytest_junit_assertion",
    "hostile_osc",
    "node_module_not_found",
]


def trim_input(path: Path, max_lines: int = 8) -> str:
    if not path.is_file():
        return "(symlink; see command)"
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\x1b]8;;https://evil.test\x1b\\", "<OSC-8-bytes>")
    text = text.replace(str(ROOT.resolve()), "<repo>")
    lines = text.splitlines()
    out = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        out += "\n..."
    return out


def main() -> None:
    entries = {e["id"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    parts = [
        "## Real report examples",
        "",
        "These samples were captured by running the real CLI on real failures "
        "(see `tests/fixtures/readme/` and `tests/test_readme_golden.py`). "
        "Paths appear as `<repo>`; runtime version lines match the machine that recorded them.",
        "",
    ]
    for idx, eid in enumerate(SHOWCASE_IDS, start=1):
        entry = entries[eid]
        inp = ROOT / entry["input"]
        golden = (ROOT / entry["golden"]).read_text(encoding="utf-8")
        cmd = entry["command"].replace("python -m", "python3 -m")
        parts.extend(
            [
                f"### {idx}. {entry['title']}",
                "",
                f"*{entry['note']}*",
                "",
                "**Command**",
                "",
                "```bash",
                cmd,
                "```",
                "",
                "**Input (trimmed)**",
                "",
                "```text",
                trim_input(inp),
                "```",
                "",
                "**Report (stdout)**",
                "",
                "```text",
                golden.rstrip(),
                "```",
                "",
            ]
        )
    print("\n".join(parts))


if __name__ == "__main__":
    main()
