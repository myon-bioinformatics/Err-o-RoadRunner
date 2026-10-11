"""Err-o-RoadRunner: read-only failure triage (no AI, stdlib only)."""

from err_o_roadrunner.triage import EXIT_OK, EXIT_USAGE, Report, main, triage

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "EXIT_OK",
    "EXIT_USAGE",
    "Report",
    "main",
    "triage",
]
