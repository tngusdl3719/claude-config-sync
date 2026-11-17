"""Where the shared history travels: a git remote (origin), or git bundles in a Notion database or a folder.

Both leave the remote tip at refs/remotes/origin/main; the rest of a sync only reads that ref.
"""
from __future__ import annotations

from typing import Optional

from . import folder_store, notion_store
from .gitio import git
from .paths import Paths
from .state import State

NETWORK_SECONDS = 120


def short(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1][:300] if lines else ""


def fetch(p: Paths, s: State) -> Optional[str]:
    """Bring the remote tip to origin/main; an error text, or None."""
    if s.backend == "notion":
        return notion_store.fetch(p, s)
    if s.backend == "folder":
        return folder_store.fetch(p, s)
    r = git(p, "fetch", "-q", "origin", check=False, timeout=NETWORK_SECONDS)
    return None if r.returncode == 0 else short(r.stderr)


def push(p: Paths, s: State, remote: Optional[str], current: str) -> Optional[str]:
    """Publish HEAD (== current) on top of `remote`; an error text, or None."""
    if s.backend == "notion":
        return notion_store.push(p, s, remote, current)
    if s.backend == "folder":
        return folder_store.push(p, s, remote, current)
    r = git(p, "push", "-q", "origin", "HEAD:main", check=False, timeout=NETWORK_SECONDS)
    if r.returncode != 0:
        return short(r.stderr)
    git(p, "fetch", "-q", "origin", check=False, timeout=NETWORK_SECONDS)
    return None
