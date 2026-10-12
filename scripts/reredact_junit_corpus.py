#!/usr/bin/env python3
"""Re-apply JUnit redaction to all checked-in fixture XML files."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

sys.path.insert(0, str(ROOT / "src"))

from err_o_roadrunner.junit_redact import find_corpus_leaks, redact_xml_document


def main() -> int:
    paths = sorted(FIXTURES.rglob("*.xml"))
    updated = 0
    leaks_after: list[tuple[str, list[str]]] = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        try:
            new = redact_xml_document(raw)
        except Exception as exc:
            print(f"SKIP {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            continue
        if new != raw:
            path.write_text(new, encoding="utf-8")
            updated += 1
        post_leaks = find_corpus_leaks(new)
        if post_leaks:
            leaks_after.append((str(path.relative_to(ROOT)), post_leaks))
    print(f"Re-redacted {updated}/{len(paths)} XML files under tests/fixtures")
    if leaks_after:
        for rel, labels in leaks_after[:20]:
            print(f"LEAK {rel}: {', '.join(labels)}", file=sys.stderr)
        print(f"{len(leaks_after)} files still leak after redaction", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
