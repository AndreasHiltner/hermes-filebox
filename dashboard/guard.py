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


def guard_tree(path: str, roots: list[str]) -> None:
    """Guard every entry in a source tree, not just the root.

    A nested symlink inside a directory could point outside the whitelist
    while the directory root itself is inside. Walk with followlinks=False
    and guard each directory and file entry individually so no operation
    dereferences a link that escapes the whitelist.
    """
    real = guard_path(path, roots)
    if not os.path.isdir(real):
        return
    for dirpath, dirnames, filenames in os.walk(real, followlinks=False):
        for name in dirnames + filenames:
            guard_path(os.path.join(dirpath, name), roots)
