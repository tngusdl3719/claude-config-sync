"""What the person asks for from the app; each ends with a sync where it should."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional

from .lang import t
from . import blocklist
from .gitio import git, head, identity, message, ref
from .links import KINDS, kind_dir
from .lock import Lock
from .paths import Paths
from .settings_merge import (
    DELETE,
    MISSING,
    apply_patch,
    decode_base,
    encode_base,
    get_path,
    read_settings,
    set_path,
    write_settings,
)
from .state import State, load_state, save_state
from .sync import REMOTE, set_mode, sync_held, write_shared_settings

KEY_LINE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,3}( [A-Za-z0-9._@-]{1,64})?$")
ADD_KEY_HELPER = "/usr/local/sbin/claudesync-add-key"


LOCK_WAIT_SECONDS = 20.0


class ActionError(RuntimeError):
    def __init__(self, text: str, **data):
        super().__init__(text)
        self.data = data


@contextmanager
def holding(p: Paths, wait_seconds: float = LOCK_WAIT_SECONDS) -> Iterator[None]:
    """Every change the person asks for runs whole under this PC's sync lock."""
    lock = Lock(p, wait_seconds=wait_seconds)
    if not lock.acquire(wait=True):
        raise ActionError(t("다른 동기화가 진행 중이에요. 잠시 뒤 다시 해 주세요", "Another sync is running. Try again in a moment"))
    try:
        yield
    finally:
        lock.release()


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ActionError(t("종류는 skills 또는 mods 입니다: %s", "The kind is skills or mods: %s") % kind)
    return kind


def _name(name: str) -> str:
    if not name or name.startswith(".") or "/" in name or name == "synced":
        raise ActionError(t("이름이 올바르지 않습니다: %s", "Not a valid name: %s") % name)
    return name


def scan_tree(root: str, words: List[str], prefix: str) -> List[dict]:
    hits: List[dict] = []
    lowered = [(w, w.lower()) for w in words]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = prefix + os.path.relpath(full, root)
            named = [w for w, lw in lowered if lw in rel.lower()]
            if named:
                hits.append({"file": rel, "line": 0, "text": rel, "word": named[0]})
                continue
            with open(full, "rb") as f:
                data = f.read()
            if b"\0" in data:
                hits.append({"file": rel, "line": 0, "text": "", "word": "(binary)"})
                continue
            text = data.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                for word, lw in lowered:
                    if lw in low:
                        hits.append({"file": rel, "line": number, "text": line, "word": word})
                        break
    return hits


def _refuse_on_receive_only(p: Paths) -> None:
    if load_state(p).receive_only:
        raise ActionError(t("받기 전용 PC에서는 공용 항목을 바꿀 수 없어요", "A receive-only PC cannot change shared items"))


def _share(p: Paths, kind: str, name: str, force: bool = False) -> dict:
    kind, name = _kind(kind), _name(name)
    _refuse_on_receive_only(p)
    src = os.path.join(kind_dir(p, kind), name)
    dst = os.path.join(p.shared, kind, name)
    if os.path.islink(src) or not os.path.isdir(src):
        raise ActionError(t("이 PC 전용 %s 폴더가 아닙니다: %s", "Not a PC-only %s folder: %s") % (kind, name))
    if os.path.exists(dst):
        raise ActionError(t("공용에 이미 같은 이름이 있습니다: %s", "Shared already has that name: %s") % name)
    words = blocklist.load_words(p)
    if words and not force:
        hits = scan_tree(src, words, "%s/%s/" % (kind, name))
        if hits:
            raise ActionError(t("금지어가 있어 공용으로 보내지 않았습니다", "Not shared: it contains blocked words"), hits=hits)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    os.symlink(dst, src)
    return {"shared": "%s/%s" % (kind, name)}


def _unshare(p: Paths, kind: str, name: str) -> dict:
    kind, name = _kind(kind), _name(name)
    _refuse_on_receive_only(p)
    link = os.path.join(kind_dir(p, kind), name)
    src = os.path.join(p.shared, kind, name)
    if not os.path.isdir(src):
        raise ActionError(t("공용에 없는 이름입니다: %s", "Not in shared: %s") % name)
    if os.path.islink(link):
        os.remove(link)
    elif os.path.lexists(link):
        raise ActionError(t("이 PC에 같은 이름의 폴더가 있어 되돌릴 수 없습니다: %s", "Cannot bring it back: this PC has a folder with the same name: %s") % name)
    shutil.move(src, link)
    if kind == "skills":
        shared = read_settings(p.shared_settings())
        if get_path(shared, ("skillOverrides", name)) is not MISSING:
            write_shared_settings(p, apply_patch(shared, {("skillOverrides", name): DELETE}))
        s = load_state(p)
        base = decode_base(s.baseline)
        base.pop(("skillOverrides", name), None)
        s.baseline = encode_base(base)
        save_state(p, s)
    return {"unshared": "%s/%s" % (kind, name)}


def _find_conflict(s: State, conflict_id: str) -> dict:
    for c in s.conflicts:
        if c["id"] == conflict_id:
            return c
    raise ActionError(t("그런 충돌이 없습니다: %s", "No such conflict: %s") % conflict_id)


def _resolve(p: Paths, conflict_id: str, choice: str) -> State:
    if choice not in ("keep", "take"):
        raise ActionError(t("keep 또는 take 를 고르세요", "Choose keep or take"))
    s = load_state(p)
    c = _find_conflict(s, conflict_id)
    if c["kind"] == "file":
        target = os.path.join(p.shared, c["file"])
        if choice == "take":
            if c.get("theirs_deleted"):
                if os.path.lexists(target):
                    os.remove(target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(c["pending"], target)
                set_mode(target, c.get("theirs_mode"))
        if c.get("pending") and os.path.exists(c["pending"]):
            os.remove(c["pending"])
    else:
        path = tuple(c["path"])
        value = c["local"] if choice == "keep" else c["remote"]
        value = DELETE if value is None else value
        # The new base is what the shared file will hold. A receive-only PC keeping its own
        # value leaves the shared file alone, so its base is the remote value.
        # A remote deletion kept this way stays in the base as a remembered deletion, so a later
        # re-add by another PC shows up as a conflict instead of overwriting the kept value.
        keep_local_only = s.receive_only and choice == "keep"
        remote = DELETE if c["remote"] is None else c["remote"]
        settled = remote if keep_local_only else value
        if not keep_local_only:
            shared = read_settings(p.shared_settings())
            write_shared_settings(p, apply_patch(shared, {path: value}))
        write_settings(p, lambda d: apply_patch(d, {path: value}))
        base = decode_base(s.baseline)
        if settled is DELETE and not keep_local_only:
            base.pop(path, None)
        else:
            base[path] = settled
        s.baseline = encode_base(base)
    s.conflicts = [x for x in s.conflicts if x["id"] != conflict_id]
    save_state(p, s)
    return sync_held(p)


def _approve(p: Paths) -> State:
    s = load_state(p)
    if not s.pending_approval:
        raise ActionError(t("확인할 변경이 없습니다", "Nothing to review"))
    s.approved_remote = s.pending_approval[0]["remote"]
    save_state(p, s)
    return sync_held(p)


def build_rejection(p: Paths, info: dict) -> str:
    """A merge of HEAD and the remote whose tree keeps this PC's version of every change
    waiting for approval, built off to the side: nothing is checked out."""
    s = load_state(p)
    current = head(p)
    if current is None:
        raise ActionError(t("받은 기록이 없어 거절할 수 없습니다", "Nothing received to reject"))
    r = git(p, "merge-tree", "--write-tree", current, info["remote"], check=False)
    if r.returncode != 0:
        raise ActionError(t("다른 변경과 충돌해서 거절할 수 없습니다. 승인한 뒤 되돌리기를 쓰세요", "Cannot reject: it conflicts with other changes. Approve it, then revert"))
    merged_tree = r.stdout.split()[0]
    fd, index = tempfile.mkstemp(prefix="claude-sync-index-")
    os.close(fd)
    os.remove(index)
    env = {"GIT_INDEX_FILE": index}
    try:
        git(p, "read-tree", merged_tree, env=env)
        for name in info["files"]:
            entry = git(p, "ls-tree", current, "--", name).stdout.strip()
            if entry:
                mode, _, rest = entry.partition(" ")
                blob = rest.split()[1]
                git(p, "update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, blob, name), env=env)
            else:
                git(p, "update-index", "--force-remove", "--", name, env=env)
        if info["settings"]:
            shown = git(p, "show", "%s:settings.json" % merged_tree, check=False)
            merged = json.loads(shown.stdout) if shown.returncode == 0 else {}
            ours_shown = git(p, "show", "%s:settings.json" % current, check=False)
            ours = json.loads(ours_shown.stdout) if ours_shown.returncode == 0 else {}
            for path in info["settings"]:
                set_path(merged, tuple(path), get_path(ours, tuple(path)))
            text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
            blob = git(p, "hash-object", "-w", "--stdin", input_text=text).stdout.strip()
            git(p, "update-index", "--add", "--cacheinfo", "100644,%s,settings.json" % blob, env=env)
        tree = git(p, "write-tree", env=env).stdout.strip()
    finally:
        if os.path.exists(index):
            os.remove(index)
    return git(p, *identity(s.pc_name), "commit-tree", tree, "-p", current, "-p", info["remote"],
               "-m", message(s.pc_name, "실행 파일 변경 거절")).stdout.strip()


def _reject(p: Paths) -> State:
    s = load_state(p)
    if not s.pending_approval:
        raise ActionError(t("확인할 변경이 없습니다", "Nothing to review"))
    info = s.pending_approval[0]
    sha = build_rejection(p, info)
    git(p, "merge", "-q", "--ff-only", sha)
    s = load_state(p)
    s.pending_approval = []
    s.approved_remote = info["remote"]
    save_state(p, s)
    return sync_held(p)


def _allow_once(p: Paths) -> State:
    s = load_state(p)
    s.allow_once = head(p)
    save_state(p, s)
    return sync_held(p)


def _discard_blocked(p: Paths) -> State:
    remote = ref(p, REMOTE)
    current = head(p)
    if remote is None or current is None:
        raise ActionError(t("서버에 받은 기록이 없어 되돌릴 수 없습니다", "Cannot revert: nothing was received from the server"))
    # Back to the last state this PC accepted, never straight to an unreviewed remote.
    base = git(p, "merge-base", current, remote, check=False).stdout.strip()
    if not base:
        raise ActionError(t("서버 기록과 이어지지 않아 되돌릴 수 없습니다", "Cannot revert: it does not follow the server's history"))
    git(p, "reset", "-q", "--hard", base)
    git(p, "clean", "-q", "-fd")
    s = load_state(p)
    s.blocked = []
    s.allow_once = None
    save_state(p, s)
    return sync_held(p)


def _revert(p: Paths, sha: str) -> State:
    if not re.match(r"^[0-9a-f]{7,40}$", sha):
        raise ActionError(t("커밋 이름이 올바르지 않습니다", "Not a valid commit name"))
    s = load_state(p)
    r = git(p, *identity(s.pc_name), "revert", "--no-edit", "--no-commit", sha, check=False)
    if r.returncode != 0:
        git(p, "revert", "--abort", check=False)
        raise ActionError(t("되돌리기가 다른 변경과 충돌합니다", "The revert conflicts with other changes"))
    subject = git(p, "log", "-1", "--format=%s", sha).stdout.strip()
    git(p, *identity(s.pc_name), "commit", "-q", "-m", message(s.pc_name, "되돌림: %s" % subject))
    return sync_held(p)


def _set_auto(p: Paths, enabled: bool) -> State:
    s = load_state(p)
    s.auto_enabled = enabled
    save_state(p, s)
    return s


def block(p: Paths, op: str, word: Optional[str]) -> List[str]:
    words = blocklist.load_words(p)
    if op == "add" and word:
        words.append(word)
    elif op == "remove" and word:
        words = [w for w in words if w != word]
    elif op != "list":
        raise ActionError(t("block list|add|remove <단어>", "block list|add|remove <word>"))
    if op != "list":
        blocklist.save_words(p, words)
    return blocklist.load_words(p)


def register_pc(line: str, admin_host: str = "split", runner: Callable = subprocess.run) -> dict:
    line = (line or "").strip()
    if "\n" in line or not KEY_LINE.match(line):
        raise ActionError(t("공개키 한 줄(ssh-ed25519 ...)을 그대로 붙여 넣으세요", "Paste the public key line (ssh-ed25519 ...) as it is"))
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", admin_host, "sudo", ADD_KEY_HELPER]
    r = runner(argv, input=line + "\n", capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise ActionError(t("서버 등록에 실패했습니다: %s", "Could not register with the server: %s") % (r.stderr or r.stdout).strip()[:300])
    return {"registered": line.split()[-1] if len(line.split()) > 2 else "key", "result": r.stdout.strip()}


def share(p: Paths, kind: str, name: str, force: bool = False) -> dict:
    with holding(p):
        return _share(p, kind, name, force)


def unshare(p: Paths, kind: str, name: str) -> dict:
    with holding(p):
        return _unshare(p, kind, name)


def resolve(p: Paths, conflict_id: str, choice: str) -> State:
    with holding(p):
        return _resolve(p, conflict_id, choice)


def approve(p: Paths) -> State:
    with holding(p):
        return _approve(p)


def reject(p: Paths) -> State:
    with holding(p):
        return _reject(p)


def allow_once(p: Paths) -> State:
    with holding(p):
        return _allow_once(p)


def discard_blocked(p: Paths) -> State:
    with holding(p):
        return _discard_blocked(p)


def revert(p: Paths, sha: str) -> State:
    with holding(p):
        return _revert(p, sha)


def set_auto(p: Paths, enabled: bool) -> State:
    with holding(p):
        return _set_auto(p, enabled)


def set_sync_mode(p: Paths, mode: str) -> State:
    if mode not in ("receive-only", "normal"):
        raise ActionError(t("mode 는 receive-only 또는 normal 이에요", "mode is receive-only or normal"))
    with holding(p):
        s = load_state(p)
        s.receive_only = mode == "receive-only"
        save_state(p, s)
        return s


def engine_version(root: str) -> Optional[str]:
    """The version an engine folder (laid out like mods/config-sync) says it is; None for one too old to say."""
    try:
        with open(os.path.join(root, "engine", "claude_sync", "__init__.py"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'^__version__ = "([0-9]+(?:\.[0-9]+)*)"', text, re.M)
    return m.group(1) if m else None


def _version_key(version: Optional[str]) -> tuple:
    return tuple(int(x) for x in version.split(".")) if version else ()


def _made_here(rel: str) -> bool:
    """What each PC makes for itself in the mod folder (Claude Code's types, Python's caches)."""
    return rel.startswith(os.path.join(".claude-plugin", "types") + os.sep) or "__pycache__" in rel.split(os.sep)


VERSION_FILE = os.path.join("engine", "claude_sync", "__init__.py")


def _mirror(src: str, target: str) -> None:
    """Make `target` hold what `src` holds, file by file, keeping what each PC makes for itself. The version
    file goes in last, after every other file and every removal, so a run cut short still says the old
    version and is tried again."""
    wanted = []
    for d, dirs, names in os.walk(src):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        wanted += [rel for rel in (os.path.relpath(os.path.join(d, n), src) for n in names) if not _made_here(rel)]

    def put(rel: str) -> None:
        dst = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".adopt-tmp"
        shutil.copy2(os.path.join(src, rel), tmp)
        os.replace(tmp, dst)

    for rel in wanted:
        if rel != VERSION_FILE:
            put(rel)
    keep = set(wanted)
    for d, _, names in os.walk(target, topdown=False):
        for name in names:
            rel = os.path.relpath(os.path.join(d, name), target)
            if rel not in keep and not _made_here(rel):
                os.remove(os.path.join(d, name))
        if d != target and not os.listdir(d):
            os.rmdir(d)
    put(VERSION_FILE)


def adopt_engine(p: Paths, src: str, wait_seconds: float = LOCK_WAIT_SECONDS) -> dict:
    """Puts the engine the app carries (`src`) into the shared repo when it is newer than the shared one,
    so the next sync hands it to the other PCs. A receive-only PC leaves the shared repo alone."""
    src = os.path.abspath(src)
    new = engine_version(src)
    if new is None or not os.path.isfile(os.path.join(src, "bin", "claude-sync")):
        raise ActionError(t("동기화 엔진 폴더가 아니에요: %s", "Not a sync engine folder: %s") % src)
    target = os.path.join(p.shared, "mods", "config-sync")
    with holding(p, wait_seconds):
        old = engine_version(target)
        out = {"adopted": False, "from": old, "to": new}
        if load_state(p).receive_only:
            out["reason"] = "receive_only"
            return out
        if _version_key(new) <= _version_key(old):
            return out
        _mirror(src, target)
        out["adopted"] = True
        return out
