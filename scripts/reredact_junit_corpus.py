#!/usr/bin/env python3
"""Re-apply JUnit redaction to all checked-in fixture XML files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

sys.path.insert(0, str(ROOT))

from junit_redact import find_corpus_leaks, redact_xml_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-only", action="store_true",
                        help="Only process the harvested JUnit corpus, preserving other fixtures")
    args = parser.parse_args(argv)
    corpus = FIXTURES / "junit_corpus"
    scope = corpus if args.corpus_only else FIXTURES
    paths = sorted(scope.rglob("*.xml"))
    manifest = corpus / "manifest.json"
    entries = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    changed_hashes = {}
    updated = 0
    errors = 0
    leaks_after: list[tuple[str, list[str]]] = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        try:
            new = redact_xml_document(raw)
        except Exception as exc:
            print(f"SKIP {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            errors += 1
            continue
        if new != raw:
            path.write_text(new, encoding="utf-8")
            updated += 1
            if path.is_relative_to(corpus):
                changed_hashes[path.relative_to(corpus).as_posix()] = hashlib.sha256(new.encode("utf-8")).hexdigest()
        post_leaks = find_corpus_leaks(new)
        if post_leaks:
            leaks_after.append((str(path.relative_to(ROOT)), post_leaks))
    manifest_changed = False
    for entry in entries:
        digest = changed_hashes.get(entry.get("xml"))
        if digest is not None and "sha256" in entry:
            entry["sha256"] = digest
            manifest_changed = True
    if manifest_changed:
        manifest.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Re-redacted {updated}/{len(paths)} XML files under {scope.relative_to(ROOT)}")
    if leaks_after:
        for rel, labels in leaks_after[:20]:
            print(f"LEAK {rel}: {', '.join(labels)}", file=sys.stderr)
        print(f"{len(leaks_after)} files still leak after redaction", file=sys.stderr)
        return 1
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
