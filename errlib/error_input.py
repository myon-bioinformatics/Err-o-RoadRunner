"""Small, dependency-free helpers for interpreting directly supplied errors."""
from __future__ import annotations

import builtins
import re


def python_exception_candidate(text: str) -> str | None:
    """Return a conservative candidate for a single-line built-in exception."""
    match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*):\s*([^\n]*)", text.strip())
    if match is None:
        return None
    name, message = match.groups()
    candidate = getattr(builtins, name, None)
    if not isinstance(candidate, type) or not issubclass(candidate, BaseException):
        return None
    return f"{name}: {message[:400]}"
