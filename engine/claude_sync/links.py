"""Shared skills and mods live in shared/; ~/.claude/<kind>/<name> is a link."""
from __future__ import annotations

import os
from typing import List

from .paths import Paths

KINDS = ("skills", "mods")
RESERVED = {"synced"}


def kind_dir(p: Paths, kind: str) -> str:
    return p.skills if kind == "skills" else p.mods


def shared_names(p: Paths, kind: str) -> List[str]:
    root = os.path.join(p.shared, kind)
    if not os.path.isdir(root):
        return []
    return sorted(
        n for n in os.listdir(root)
        if not n.startswith(".") and n not in RESERVED and os.path.isdir(os.path.join(root, n))
    )


def _points_into(link: str, root: str) -> bool:
    target = os.readlink(link)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link), target)
    return os.path.normpath(target).startswith(os.path.normpath(root) + os.sep)


def reconcile(p: Paths) -> List[dict]:
    collisions: List[dict] = []
    for kind in KINDS:
        local_root = kind_dir(p, kind)
        shared_root = os.path.join(p.shared, kind)
        os.makedirs(local_root, exist_ok=True)
        for name in shared_names(p, kind):
            link = os.path.join(local_root, name)
            target = os.path.join(shared_root, name)
            if os.path.islink(link):
                if os.path.normpath(os.readlink(link)) == target:
                    continue
                collisions.append({"kind": kind, "name": name, "reason": "exists"})
                continue
            if os.path.lexists(link):
                collisions.append({"kind": kind, "name": name, "reason": "exists"})
                continue
            os.symlink(target, link)
        for name in os.listdir(local_root):
            link = os.path.join(local_root, name)
            if os.path.islink(link) and not os.path.exists(link) and _points_into(link, shared_root):
                os.remove(link)
    return collisions


def local_only(p: Paths, kind: str) -> List[str]:
    root = kind_dir(p, kind)
    if not os.path.isdir(root):
        return []
    return sorted(
        n for n in os.listdir(root)
        if not n.startswith(".") and n not in RESERVED
        and not os.path.islink(os.path.join(root, n)) and os.path.isdir(os.path.join(root, n))
    )
