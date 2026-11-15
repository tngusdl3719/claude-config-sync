"""Changes from another PC that could run code here wait for the person's yes."""
from __future__ import annotations

import json
from typing import List, Optional

from .gitio import git
from .paths import Paths
from .settings_merge import MISSING, flatten


def needs_approval_paths(files: List[str]) -> List[str]:
    return [f for f in files if f.startswith("mods/") or (f.startswith("skills/") and not f.endswith(".md"))]


def settings_needs_approval(old: dict, new: dict) -> List[List[str]]:
    before, after = flatten(old), flatten(new)
    out: List[List[str]] = []
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path, MISSING), after.get(path, MISSING)
        if a == b:
            continue
        if path[0] == "enabledPlugins" and a is not MISSING and a:
            out.append(list(path))
        elif path == ("permissions", "defaultMode"):
            out.append(list(path))
    return out


def shared_settings_at(p: Paths, rev: str) -> dict:
    r = git(p, "show", "%s:settings.json" % rev, check=False)
    if r.returncode != 0:
        return {}
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def commits_between(p: Paths, start: Optional[str], end: str) -> List[dict]:
    span = end if start is None else "%s..%s" % (start, end)
    fmt = "%H%x1f%ct%x1f%an%x1f%(trailers:key=PC,valueonly,separator=)%x1f%s"
    out = []
    for line in git(p, "log", "--reverse", "--format=" + fmt, span).stdout.splitlines():
        sha, ts, author, pc, subject = line.split("\x1f", 4)
        out.append({"sha": sha, "time": int(ts), "pc": pc.strip() or author, "subject": subject,
                    "tagged": bool(pc.strip())})
    return out


def incoming_requiring_approval(p: Paths, head: Optional[str], remote: str, me: str) -> Optional[dict]:
    if head is None:
        base = None
    else:
        r = git(p, "merge-base", head, remote, check=False)
        base = r.stdout.strip() or None
    if base == remote:
        return None
    # Everything between the merge base and the remote came from elsewhere: this PC's own
    # pushed work is already behind the merge base. Commit metadata is never trusted here.
    commits = commits_between(p, base, remote)
    if base is None:
        files = git(p, "ls-tree", "-r", "--name-only", remote).stdout.splitlines()
        old_settings: dict = {}
    else:
        files = git(p, "diff", "--name-only", base, remote).stdout.splitlines()
        old_settings = shared_settings_at(p, base)
    approval_files = needs_approval_paths(files)
    approval_settings = settings_needs_approval(old_settings, shared_settings_at(p, remote))
    if not approval_files and not approval_settings:
        return None
    return {"remote": remote, "files": approval_files, "settings": approval_settings, "commits": commits}
