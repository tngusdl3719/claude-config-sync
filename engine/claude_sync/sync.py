"""One sync run: capture -> fetch -> approval -> merge -> apply -> blocklist -> push."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .lang import t
from . import approval, blocklist, transport
from .claudemd import ensure_import
from .gitio import EMPTY_TREE, GitError, commit_all, git, head, identity, is_cache, message, ref
from .links import reconcile, shared_names
from .lock import Lock
from .paths import Paths
from .settings_merge import (
    DELETE,
    MISSING,
    PLUGIN_DIRS,
    apply_patch,
    decode_base,
    encode_base,
    flatten,
    get_path,
    managed_paths,
    merge,
    plugin_dirs,
    read_settings,
    set_path,
    write_settings,
)
from .state import State, load_state, save_state, write_atomic, write_bytes_atomic

REMOTE = "origin/main"
_short = transport.short


def log(p: Paths, line: str) -> None:
    os.makedirs(p.sync_dir, exist_ok=True)
    with open(p.log_file, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))


def areas(names: List[str]) -> str:
    out: List[str] = []
    for name in names:
        parts = name.split("/")
        if name == "CLAUDE.md":
            label = "지침"
        elif name == "settings.json":
            label = "설정"
        elif parts[0] == "skills" and len(parts) > 1:
            label = "스킬 %s" % parts[1]
        elif parts[0] == "mods" and len(parts) > 1:
            label = "mod %s" % parts[1]
        else:
            label = name
        if label not in out:
            out.append(label)
    return ", ".join(out) or "변경"


def summarize(p: Paths) -> str:
    lines = git(p, "status", "--porcelain", "-uall").stdout.splitlines()
    return areas([line[3:].split(" -> ")[-1].strip('"') for line in lines])


def write_shared_settings(p: Paths, data: dict) -> None:
    write_atomic(p.shared_settings(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


IGNORED = ("__pycache__/", "*.pyc", ".DS_Store")


def ensure_gitignore(p: Paths) -> None:
    """Caches and Finder files never travel; stop tracking any that already did.
    Only these patterns: a PC's own ignore rules must never untrack shared files."""
    path = os.path.join(p.shared, ".gitignore")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    # The last matching rule wins in .gitignore, so the cache rules must end the file:
    # a `!keep.pyc` written earlier can then never bring a cache back.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if tuple(lines[-len(IGNORED):]) != IGNORED:
        if text and not text.endswith("\n"):
            text += "\n"
        write_atomic(path, text + "# config-sync: never shared\n" + "".join(x + "\n" for x in IGNORED))
    tracked = [t for t in git(p, "ls-files", "-z").stdout.split("\0") if t and is_cache(t)]
    if tracked:
        git(p, "rm", "-q", "--cached", "--", *tracked)


def capture(p: Paths, s: State) -> None:
    local = read_settings(p.settings)
    shared = read_settings(p.shared_settings())
    base = decode_base(s.baseline)
    paths = managed_paths(shared, set(shared_names(p, "skills")), local, base)
    r = merge(base, local, shared, paths)
    if r.shared_patch and not s.receive_only:
        write_shared_settings(p, apply_patch(shared, r.shared_patch))
        for path, value in r.shared_patch.items():
            if value is DELETE:
                base.pop(path, None)
            else:
                base[path] = value
        s.baseline = encode_base(base)
    if git(p, "status", "--porcelain").stdout.strip():
        commit_all(p, s.pc_name, summarize(p))


def _merge_settings_file(base: dict, ours: dict, theirs: dict, conflicts: List[dict], remote: str) -> dict:
    fb, fo, ft = flatten(base), flatten(ours), flatten(theirs)
    merged = json.loads(json.dumps(ours))
    for path in sorted(set(fb) | set(fo) | set(ft)):
        b, o, t = fb.get(path, MISSING), fo.get(path, MISSING), ft.get(path, MISSING)
        if t == b or t == o:
            continue
        if o == b:
            set_path(merged, path, t)
            continue
        conflicts.append({
            "id": "setting:%s" % "/".join(path), "kind": "setting", "path": list(path),
            "local": None if o is MISSING else o, "remote": None if t is MISSING else t, "remote_sha": remote,
        })
    return merged


def _stage(p: Paths, n: int, name: str) -> Optional[bytes]:
    r = git(p, "show", ":%d:%s" % (n, name), check=False, binary=True)
    return r.stdout if r.returncode == 0 else None


def _modes(p: Paths, name: str) -> Dict[int, str]:
    modes: Dict[int, str] = {}
    for line in git(p, "ls-files", "-s", "--", name).stdout.splitlines():
        meta = line.split("\t", 1)[0].split()
        modes[int(meta[2])] = meta[0]
    return modes


def set_mode(path: str, mode: Optional[str]) -> None:
    os.chmod(path, 0o755 if mode == "100755" else 0o644)


def _hold_conflicts(p: Paths, s: State, remote: str, unmerged: List[str]) -> List[dict]:
    found: List[dict] = []
    for name in unmerged:
        base_blob, ours, theirs = _stage(p, 1, name), _stage(p, 2, name), _stage(p, 3, name)
        modes = _modes(p, name)
        target = os.path.join(p.shared, name)
        if name == "settings.json" and ours is not None and theirs is not None:
            merged = _merge_settings_file(
                json.loads((base_blob or b"{}").decode("utf-8")), json.loads(ours.decode("utf-8")),
                json.loads(theirs.decode("utf-8")), found, remote)
            write_shared_settings(p, merged)
            git(p, "add", "--", name)
            continue
        os.makedirs(p.pending_dir, exist_ok=True)
        pending = os.path.join(p.pending_dir, "%s-%s" % (remote[:8], name.replace("/", "__")))
        if theirs is not None:
            write_bytes_atomic(pending, theirs)
            set_mode(pending, modes.get(3))
        elif os.path.exists(pending):
            os.remove(pending)
        if ours is None:
            git(p, "rm", "-q", "--cached", "--", name, check=False)
            if os.path.exists(target):
                os.remove(target)
        else:
            write_bytes_atomic(target, ours)
            set_mode(target, modes.get(2))
            git(p, "add", "--", name)
        found.append({
            "id": "file:%s" % name, "kind": "file", "file": name, "pending": pending if theirs is not None else None,
            "theirs_deleted": theirs is None, "theirs_mode": modes.get(3), "remote_sha": remote,
        })
    git(p, *identity(s.pc_name), "commit", "-q", "--no-edit", "-m", message(s.pc_name, "충돌 보류"))
    return found


def merge_remote(p: Paths, s: State, remote: str) -> None:
    current = head(p)
    if current is None:
        git(p, "checkout", "-q", "-B", "main", remote)
        return
    if git(p, "merge-base", "--is-ancestor", remote, current, check=False).returncode == 0:
        return
    r = git(p, *identity(s.pc_name), "merge", "-q", "--no-edit", "-m", message(s.pc_name, "합치기"), remote, check=False)
    if r.returncode == 0:
        return
    unmerged = git(p, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if not unmerged:
        git(p, "merge", "--abort", check=False)
        raise GitError(("merge", remote), r)
    try:
        found = _hold_conflicts(p, s, remote, unmerged)
    except Exception:
        git(p, "merge", "--abort", check=False)
        raise
    known = {c["id"] for c in found}
    s.conflicts = [c for c in s.conflicts if c["id"] not in known] + found


def apply_local(p: Paths, s: State) -> None:
    local = read_settings(p.settings)
    shared = read_settings(p.shared_settings())
    mods = shared_names(p, "mods")
    paths = managed_paths(shared, set(shared_names(p, "skills")), local, decode_base(s.baseline))
    held = {tuple(c["path"]) for c in s.conflicts if c.get("kind") == "setting"}
    r = merge(decode_base(s.baseline), local, shared, paths - held)
    new_conflicts = [{
        "id": "setting:%s" % "/".join(c["path"]), "kind": "setting", "path": c["path"],
        "local": c["local"], "remote": c["remote"], "remote_sha": ref(p, REMOTE),
    } for c in r.conflicts]
    known = {c["id"] for c in new_conflicts}
    s.conflicts = [c for c in s.conflicts if c["id"] not in known] + new_conflicts
    base = r.new_base
    old = decode_base(s.baseline)
    kept = set(held) | (set(r.shared_patch) if s.receive_only else set())
    for path in kept:
        if path in old:
            base[path] = old[path]
        else:
            base.pop(path, None)
    s.baseline = encode_base(base)
    if r.overwritten:
        s.overwritten = s.overwritten + r.overwritten

    def mutate(d: dict) -> dict:
        apply_patch(d, r.local_patch)
        current = get_path(d, PLUGIN_DIRS)
        joined = plugin_dirs(None if current is MISSING else current, p.home, mods)
        if joined:
            set_path(d, PLUGIN_DIRS, joined)
        return d

    write_settings(p, mutate)
    if r.shared_patch and not s.receive_only:
        write_shared_settings(p, apply_patch(shared, r.shared_patch))
    s.collisions = reconcile(p)
    ensure_import(p)


def squash(p: Paths, s: State, remote: Optional[str], current: str) -> Optional[str]:
    """One commit of the net change on top of the remote, so no intermediate commit ever leaves."""
    tree = git(p, "rev-parse", "%s^{tree}" % current).stdout.strip()
    if remote is not None and tree == git(p, "rev-parse", "%s^{tree}" % remote).stdout.strip():
        git(p, "reset", "-q", "--soft", remote)
        return remote
    names = git(p, "diff", "--name-only", remote or EMPTY_TREE, current).stdout.splitlines()
    args = ["commit-tree", tree, "-m", message(s.pc_name, areas(names))] + (["-p", remote] if remote else [])
    new = git(p, *identity(s.pc_name), *args).stdout.strip()
    git(p, "reset", "-q", "--soft", new)
    return new


def push(p: Paths, s: State, now: float) -> None:
    remote = ref(p, REMOTE)
    current = head(p)
    if current is None or current == remote:
        s.blocked = []
        return
    words = blocklist.load_words(p)
    if words:
        hits = blocklist.scan_outgoing(p, remote or EMPTY_TREE, current, words)
        if hits and s.allow_once != current:
            s.blocked = hits
            s.last_result = "blocked"
            return
        current = squash(p, s, remote, current)
        if current == remote:
            s.blocked = []
            s.allow_once = None
            return
    error = transport.push(p, s, remote, current)
    if error is not None:
        s.last_result = "offline"
        s.last_error = error
        return
    s.blocked = []
    s.allow_once = None
    s.last_push = now


def run_sync(p: Paths, *, auto: bool, now: Optional[float] = None) -> State:
    lock = Lock(p)
    if not lock.acquire(wait=not auto):
        s = load_state(p)
        if not auto:
            s.last_error = t("다른 동기화가 진행 중입니다", "Another sync is running")
        return s
    try:
        return sync_held(p, now)
    finally:
        lock.release()


def sync_held(p: Paths, now: Optional[float] = None) -> State:
    """One sync run; the caller holds this PC's lock."""
    now = time.time() if now is None else now
    s = load_state(p)
    try:
        _run(p, s, now)
    except Exception as e:  # a failed run is reported, never left half-done in silence
        s.last_result = "error"
        s.last_error = str(e)[:500]
    save_state(p, s)
    log(p, "%s %s" % (s.last_result, s.last_error or ""))
    return s


def _run(p: Paths, s: State, now: float) -> None:
    if not os.path.isdir(os.path.join(p.shared, ".git")):
        s.last_result = "error"
        s.last_error = t("공용 저장소가 없습니다. 설치를 먼저 하세요", "No shared repository. Install first")
        return
    s.last_error = None
    ensure_gitignore(p)
    capture(p, s)
    error = transport.fetch(p, s)
    if error is not None:
        s.last_result = "offline"
        s.last_error = error
        return
    remote = ref(p, REMOTE)
    if remote is not None:
        if s.require_approval:
            info = approval.incoming_requiring_approval(p, head(p), remote, s.pc_name)
            if info is not None and info["remote"] != s.approved_remote:
                s.pending_approval = [info]
                s.last_result = "needs_approval"
                return
        s.pending_approval = []
        merge_remote(p, s, remote)
    apply_local(p, s)
    if git(p, "status", "--porcelain").stdout.strip():
        commit_all(p, s.pc_name, summarize(p))
    s.last_pull = now
    s.last_result = "ok"
    if s.conflicts:
        s.last_result = "conflict"
        return
    if s.receive_only:
        return
    push(p, s, now)
