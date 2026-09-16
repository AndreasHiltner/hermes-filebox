"""Whitelist guard for filebox paths.

Every path request resolves to a canonical realpath and is checked against the
configured root whitelist. The guard RETURNS the canonical path; callers must
open only that return value (never the raw input) to close the symlink-swap
TOCTOU window.
"""
from __future__ import annotations

import os


class GuardError(PermissionError):
    """Raised when a path is outside the configured whitelist."""


def _canon(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _canon_roots(roots: list[str]) -> list[str]:
    return [_canon(r) for r in roots]


def guard_path(path: str, roots: list[str]) -> str:
    """Return canonical path if inside whitelist, else raise GuardError."""
    real = _canon(path)
    for root in _canon_roots(roots):
        try:
            if os.path.commonpath([real, root]) == root:
                return real
        except ValueError:
            continue
    raise GuardError(f"path outside whitelist: {path}")
