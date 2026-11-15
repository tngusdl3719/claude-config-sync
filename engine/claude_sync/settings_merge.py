"""Shared settings keys: which paths are shared, three-way merge, plugin dirs."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .paths import Paths
from .state import write_atomic

CONTAINERS = ("env", "permissions", "enabledPlugins", "skillOverrides", "modelSettings")
NEVER = {
    ("hooks",),
    ("statusLine",),
    ("autoMode",),
    ("skipDangerousModePermissionPrompt",),
    ("env", "CLAUDE_CODE_PLUGIN_DIRS"),
}
PLUGIN_DIRS = ("env", "CLAUDE_CODE_PLUGIN_DIRS")
# Outermost of the user-tier mods, so its band can stack above the others'.
FIRST_MOD = "config-sync"


class _Delete:
    def __repr__(self):
        return "DELETE"


DELETE = _Delete()
MISSING = DELETE

Path = Tuple[str, ...]


def flatten(d: dict) -> Dict[Path, Any]:
    out: Dict[Path, Any] = {}
    for key, value in d.items():
        if key in CONTAINERS and isinstance(value, dict):
            for sub, subvalue in value.items():
                out[(key, sub)] = subvalue
        else:
            out[(key,)] = value
    return out


def get_path(d: dict, path: Path) -> Any:
    cur: Any = d
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


def set_path(d: dict, path: Path, value: Any) -> None:
    if value is DELETE:
        parent = get_path(d, path[:-1]) if len(path) > 1 else d
        if isinstance(parent, dict):
            parent.pop(path[-1], None)
        return
    cur = d
    for part in path[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[path[-1]] = copy.deepcopy(value)


def apply_patch(d: dict, patch: Dict[Path, Any]) -> dict:
    for path, value in patch.items():
        set_path(d, path, value)
    return d


def managed_paths(shared: dict, shared_skills: Set[str], local: dict,
                  base: Optional[Dict[Path, Any]] = None) -> Set[Path]:
    # A remembered deletion (receive-only "keep") stays managed although shared no longer has it.
    remembered = [path for path, value in (base or {}).items() if value is DELETE]
    paths: Set[Path] = set()
    for path in list(flatten(shared)) + remembered:
        if path in NEVER:
            continue
        if path[0] == "skillOverrides" and path[1] not in shared_skills:
            continue
        paths.add(path)
    for source in (local, shared):
        overrides = source.get("skillOverrides")
        if isinstance(overrides, dict):
            for name in overrides:
                if name in shared_skills:
                    paths.add(("skillOverrides", name))
    return paths


@dataclass
class MergeResult:
    new_local: dict
    new_shared: dict
    new_base: Dict[Path, Any]
    local_patch: Dict[Path, Any] = field(default_factory=dict)
    shared_patch: Dict[Path, Any] = field(default_factory=dict)
    conflicts: List[dict] = field(default_factory=list)
    overwritten: List[dict] = field(default_factory=list)


def _plain(value: Any) -> Any:
    return None if value is MISSING else value


def merge(base: Dict[Path, Any], local: dict, shared: dict, paths: Iterable[Path]) -> MergeResult:
    new_local = copy.deepcopy(local)
    new_shared = copy.deepcopy(shared)
    result = MergeResult(new_local=new_local, new_shared=new_shared, new_base={})
    for path in sorted(paths):
        l, s = get_path(local, path), get_path(shared, path)
        if path not in base:
            if s is MISSING:
                if l is not MISSING:
                    result.shared_patch[path] = l
                    result.new_base[path] = l
                continue
            if l != s:
                result.local_patch[path] = s
                if l is not MISSING:
                    result.overwritten.append({"path": list(path), "local": l, "shared": s})
            result.new_base[path] = s
            continue
        b = base[path]
        local_changed, shared_changed = l != b, s != b
        if local_changed and shared_changed and l != s:
            result.conflicts.append({"path": list(path), "local": _plain(l), "remote": _plain(s)})
            result.new_base[path] = b
        elif local_changed and not shared_changed:
            result.shared_patch[path] = l
            if l is not MISSING:
                result.new_base[path] = l
        elif shared_changed and not local_changed:
            result.local_patch[path] = s
            if s is not MISSING:
                result.new_base[path] = s
        elif l is not MISSING:
            result.new_base[path] = l
    apply_patch(new_local, result.local_patch)
    apply_patch(new_shared, result.shared_patch)
    return result


# A key both sides agreed is absent: known, unlike a key never synced (first install).
DELETED_MARK = {"$config-sync": "deleted"}


def encode_base(base: Dict[Path, Any]) -> Dict[str, Any]:
    return {json.dumps(list(k), ensure_ascii=False): (DELETED_MARK if v is DELETE else v) for k, v in base.items()}


def decode_base(raw: Dict[str, Any]) -> Dict[Path, Any]:
    return {tuple(json.loads(k)): (DELETE if v == DELETED_MARK else v) for k, v in raw.items()}


def plugin_dirs(current: Any, home: str, shared_mods: List[str], exists: Callable[[str], bool] = os.path.exists) -> str:
    mods_dir = os.path.join(home, ".claude", "mods")
    entries = [e for e in (current or "").split(":") if e]

    def real(entry: str) -> str:
        if entry == "~" or entry.startswith("~/"):
            entry = home + entry[1:]
        return os.path.normpath(entry)

    first = os.path.join(mods_dir, FIRST_MOD)
    kept = []
    for entry in entries:
        full = real(entry)
        if os.path.dirname(full) == mods_dir and not exists(full):
            continue
        if full == first and FIRST_MOD in shared_mods:
            continue
        kept.append(entry)
    present = {real(e) for e in kept}
    for name in shared_mods:
        full = os.path.join(mods_dir, name)
        if name != FIRST_MOD and full not in present:
            kept.append(full)
    if FIRST_MOD in shared_mods:
        kept.insert(0, first)
    return ":".join(kept)


def read_settings(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(p: Paths, mutate: Callable[[dict], dict], path: str = "") -> bool:
    path = path or p.settings
    current = read_settings(path)
    updated = mutate(copy.deepcopy(current))
    if updated == current and os.path.exists(path):
        return False
    text = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)
    write_atomic(path, text)
    return True
