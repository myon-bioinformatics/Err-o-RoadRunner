"""missing_command fix candidates vs Dart / filesystem hint context."""

from __future__ import annotations

import pytest

from sentinel import build_fix_candidates


@pytest.mark.sentinel
def test_missing_command_from_exit_127_found() -> None:
    structured = {
        "found": ["exit code: 127"],
        "not_found": [],
        "hints": ["sh: definitely-missing-cmd: command not found"],
    }
    fixes = build_fix_candidates(structured, reproduce_command="missing-cmd")
    assert any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_from_exec_format_hint() -> None:
    structured = {
        "found": ["exit code: 127"],
        "not_found": [],
        "hints": ["/bin/sh: 1: /tmp/foo: No such file or directory"],
    }
    fixes = build_fix_candidates(structured, reproduce_command="sh /tmp/foo")
    assert any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_error_when_reading() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            "web search keywords (2 variants): Error when reading "
            "'lib/shared/screens/generic_screen.dart' : No such file or directory"
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="flutter test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_double_quoted_dart_read() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            'Error when reading "lib/foo.dart": No such file or directory in pubspec.yaml'
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="dart test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_missing_command_skipped_for_filesystem_exception_os_error() -> None:
    structured = {
        "found": ["exit code: 1"],
        "not_found": [],
        "hints": [
            "FileSystemException: Cannot open file, path = 'lib/a.dart' "
            "(OS Error: No such file or directory, errno = 2)"
        ],
    }
    fixes = build_fix_candidates(structured, reproduce_command="flutter test")
    assert not any(f.get("kind") == "missing_command" for f in fixes)


@pytest.mark.sentinel
def test_enrich_drops_missing_command_when_dart_import_added() -> None:
    import squad

    candidates = [
        {
            "id": "F1",
            "kind": "missing_command",
            "confidence": "low",
            "evidence": [],
            "location": {},
            "suggestion": "install cmd",
            "verify_command": "foo",
        }
    ]
    hypotheses = [
        {
            "id": "H1",
            "summary": "dart-compile-cascade",
            "confidence": "high",
            "explains": ["t1"],
            "source_squads": ["code"],
        }
    ]
    out = squad.enrich_fix_candidates(candidates, hypotheses)
    kinds = {c.get("kind") for c in out}
    assert "dart_import" in kinds
    assert "missing_command" not in kinds
