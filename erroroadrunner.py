"""User-facing entry point for Err-o-RoadRunner."""
from __future__ import annotations

from collections.abc import Sequence
import sys


def main(argv: Sequence[str] | None = None) -> int:
    try:
        from triage import main as triage_main
    except ModuleNotFoundError as exc:
        if exc.name not in {"triage", "errlib", "errlib.junit_redact", "errlib.project_version"}:
            raise
        print("error: Err-o-RoadRunner's triage components are missing; install the package or use a complete checkout", file=sys.stderr)
        return 2
    return triage_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
