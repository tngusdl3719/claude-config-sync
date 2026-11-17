"""A shared history kept as rows of git bundles: a Notion database, or a synced folder.

Each upload is one row that is never changed afterwards: a bundle of HEAD, either the whole history
(full) or what is new since the remote tip (delta, with that tip as its base). Order comes from git
ancestry: the remote tip is the newest row head that no other row head contains. Every FULL_EVERY rows
one row carries the whole history, and a PC reads from the latest such row onward.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .gitio import git
from .lang import t
from .paths import Paths

FULL_EVERY = 30
REMOTE_REF = "refs/remotes/origin/main"
# Where a row's bundle is fetched to (named when Notion was the only storage of rows).
ROW_REFS = "refs/notion/"
MOVED = ("다른 PC가 먼저 올렸어요. 다음 동기화에서 합쳐 올려요", "Another PC uploaded first. The next sync merges and uploads")


@dataclass(frozen=True)
class Row:
    id: str  # unique, and fit for a ref name
    time: str  # sorts in the order the rows were made
    full: bool
    head: str
    base: str = ""


class Store:
    """Where the rows are. `read` writes a row's bundle to `path` (False: the row is damaged)."""

    def window(self) -> List[Row]:
        raise NotImplementedError

    def read(self, row: Row, path: str) -> bool:
        raise NotImplementedError

    def add(self, *, full: bool, head: str, base: str, summary: str, pc: str, data: bytes) -> None:
        raise NotImplementedError


def key(row: Row) -> Tuple[str, str]:
    return row.time, row.id


def present(p: Paths, sha: str) -> bool:
    return bool(sha) and git(p, "cat-file", "-e", "%s^{commit}" % sha, check=False).returncode == 0


def _ancestor(p: Paths, a: str, b: str) -> bool:
    return git(p, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def _receive(p: Paths, store: Store, row: Row, tmp: str) -> None:
    path = os.path.join(tmp, row.id + ".bundle")
    try:
        if not store.read(row, path):
            return  # a damaged row is skipped like any bundle that does not verify
    except OSError:
        return  # not readable now (a synced folder still bringing it): later
    if git(p, "bundle", "verify", "-q", path, check=False).returncode != 0:
        return  # its base is not here yet (or the row is damaged): later, or never
    ref = ROW_REFS + row.id
    if git(p, "fetch", "-q", "--no-tags", path, "HEAD:%s" % ref, check=False).returncode != 0:
        return
    if git(p, "rev-parse", ref, check=False).stdout.strip() != row.head:
        git(p, "update-ref", "-d", ref, check=False)


def absorb(p: Paths, store: Store, rows: List[Row]) -> None:
    """Fetch every row whose head is missing, bases first: the newest full row first (the older ones
    it contains are then here already), then the rest oldest first."""
    pending = [r for r in rows if r.head]
    os.makedirs(p.sync_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="rows-", dir=p.sync_dir)
    try:
        while True:
            pending = [r for r in pending if not present(p, r.head)]
            ready = [r for r in pending if r.full or present(p, r.base)]
            if not ready:
                return
            fulls = [r for r in ready if r.full]
            row = max(fulls, key=key) if fulls else min(ready, key=key)
            _receive(p, store, row, tmp)
            pending.remove(row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tip(p: Paths, rows: List[Row]) -> Optional[str]:
    heads = {}
    for r in rows:
        if present(p, r.head) and (r.head not in heads or key(r) > heads[r.head]):
            heads[r.head] = key(r)
    tips = [h for h in heads if not any(o != h and _ancestor(p, h, o) for o in heads)]
    return max(tips, key=lambda h: heads[h]) if tips else None


def set_remote(p: Paths, sha: Optional[str], rows: List[Row]) -> None:
    if sha:
        git(p, "update-ref", REMOTE_REF, sha)
    else:
        git(p, "update-ref", "-d", REMOTE_REF, check=False)
    keep = {ROW_REFS + r.id for r in rows}
    for ref in git(p, "for-each-ref", "--format=%(refname)", ROW_REFS).stdout.split():
        if ref not in keep:
            git(p, "update-ref", "-d", ref, check=False)


def forget_rows(p: Paths) -> None:
    for name in git(p, "for-each-ref", "--format=%(refname)", ROW_REFS).stdout.split():
        git(p, "update-ref", "-d", name, check=False)


def fetch(p: Paths, store: Store) -> None:
    """Bring in new rows and point origin/main at the remote tip."""
    rows = store.window()
    absorb(p, store, rows)
    set_remote(p, tip(p, rows), rows)


def moved_since(p: Paths, store: Store, rows: List[Row], remote: Optional[str]) -> bool:
    """Another PC uploaded after this run's fetch: take its tip as the remote and stop."""
    absorb(p, store, rows)
    now = tip(p, rows)
    if now == remote:
        return False
    set_remote(p, now, rows)
    return True


def needs_full(p: Paths, rows: List[Row], remote: Optional[str]) -> bool:
    fulls = [r for r in rows if r.full and present(p, r.head)]
    if remote is None or not fulls:
        return True
    base = max(fulls, key=key)
    for r in fulls:
        if _ancestor(p, base.head, r.head) and r.head != base.head:
            base = r
    since = {r.head for r in rows if present(p, r.head) and r.head != base.head and _ancestor(p, base.head, r.head)}
    return len(since) >= FULL_EVERY


def summary(p: Paths, sha: str) -> str:
    subject = git(p, "log", "-1", "--format=%s", sha).stdout.strip()
    if subject.startswith("sync: "):
        subject = subject.split(": ", 2)[-1]
    return subject or "변경"


def push(p: Paths, pc: str, store: Store, remote: Optional[str], current: str) -> Optional[str]:
    """Upload HEAD (== current) as one row; None, or why it waits."""
    rows = store.window()
    if moved_since(p, store, rows, remote):
        return t(*MOVED)
    full = needs_full(p, rows, remote)
    os.makedirs(p.sync_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="rows-", dir=p.sync_dir)
    try:
        path = os.path.join(tmp, "out.bundle")
        spec = ["HEAD"] if full else ["HEAD", "^%s" % remote]
        git(p, "bundle", "create", "-q", path, *spec)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    store.add(full=full, head=current, base="" if full else remote or "", summary=summary(p, current), pc=pc, data=data)
    git(p, "update-ref", REMOTE_REF, current)
    return None
