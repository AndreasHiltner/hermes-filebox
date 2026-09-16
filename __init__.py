"""Hermes Filebox - unified plugin package.

Python backend in dashboard/ (FastAPI router mounted at
/api/plugins/filebox/), desktop renderer in desktop/plugin.js.
Neither half registers core agent tools/hooks - routine use is token-free.
"""


def register(ctx) -> None:
    """No-op registration for the capability probe."""
    return None
