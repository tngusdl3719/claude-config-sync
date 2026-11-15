"""What the app shows: status, history and diffs, read from disk and git only."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import unquote

from .lang import t
from .approval import commits_between
from .blocklist import load_words
from .gitio import git, head, ref
from .links import kind_dir, local_only, shared_names
from .paths import Paths
from .settings_merge import MISSING, decode_base, get_path, managed_paths, read_settings
from .state import load_state
from .sync import REMOTE


def _merge_base(p: Paths, a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a is None or b is None:
        return None
    return git(p, "merge-base", a, b, check=False).stdout.strip() or None


def _storage(p: Paths, s) -> dict:
    """Where this PC's shared history lives, as the app shows it (never a secret)."""
    if s.backend == "notion":
        page = s.notion_page_id or ""
        return {"backend": "notion", "page": page, "auth": s.notion_auth or "secret",
                "url": "https://www.notion.so/%s" % page if page else ""}
    url = git(p, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    m = re.match(r"^(https?://)([^@/]+)@(.+)$", url)
    if not m:
        return {"backend": "git", "remote": url, "username": ""}
    # Only the name before any ':' (a hand-made address could carry a password there).
    return {"backend": "git", "remote": m.group(1) + m.group(3), "username": unquote(m.group(2).split(":", 1)[0])}


BLOCK_MARKS = ("", ">", "|", ">-", "|-", ">+", "|+")


def _skill_description(path: str) -> str:
    """`description:` from a SKILL.md front matter (one line, quoted, or a folded/literal block)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read(20000).splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            value = line[len("description:"):].strip()
            if value not in BLOCK_MARKS:
                return _unquote(value).strip()
            block = []  # a block's quotes and backslashes are its content
            for more in lines[i + 1:]:
                if more.strip() == "---" or (more and not more[0].isspace()):
                    break
                block.append(more.strip())
            return " ".join(x for x in block if x)
    return ""


def _unquote(value: str) -> str:
    """A YAML scalar without its own quotes: 'single' ('' is one '), "double" (backslash escapes)."""
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)  # the escapes descriptions use (\" \\ \n \u) read the same as JSON
        except ValueError:
            return value[1:-1]
    return value


def _mod_description(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f).get("description")
    except (OSError, ValueError, AttributeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


def _descriptions(root: str, kind: str, names: List[str]) -> Dict[str, str]:
    """What each skill or mod under `root` says it is (its own SKILL.md or plugin.json), for tooltips."""
    out = {}
    for name in names:
        folder = os.path.join(root, name)
        text = (_skill_description(os.path.join(folder, "SKILL.md")) if kind == "skills"
                else _mod_description(os.path.join(folder, ".claude-plugin", "plugin.json")))
        if text:
            out[name] = text[:300]
    return out


def _files(p: Paths, start: Optional[str], end: Optional[str]) -> Set[str]:
    if end is None:
        return set()
    if start is None:
        return set(git(p, "ls-tree", "-r", "--name-only", end).stdout.splitlines())
    return set(git(p, "diff", "--name-only", start, end).stdout.splitlines())


def _dirty(p: Paths) -> Set[str]:
    out = set()
    for line in git(p, "status", "--porcelain", "-uall").stdout.splitlines():
        out.add(line[3:].split(" -> ")[-1].strip('"'))
    return out


def _count(p: Paths, span: str) -> int:
    r = git(p, "rev-list", "--count", span, check=False)
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def _label(path) -> str:
    return ".".join(path)


def status(p: Paths) -> dict:
    s = load_state(p)
    current, remote = head(p), ref(p, REMOTE)
    mb = _merge_base(p, current, remote)
    if current is None:
        outgoing_files: Set[str] = set()
    else:
        outgoing_files = _files(p, mb if remote else None, current)
    outgoing_files |= _dirty(p)
    incoming_files = _files(p, mb, remote) if remote and remote != mb else set()
    conflict_files = {c["file"] for c in s.conflicts if c.get("kind") == "file"}
    has_setting_conflict = any(c.get("kind") == "setting" for c in s.conflicts)

    def state_of(prefix: str) -> str:
        match = lambda files: any(f == prefix or f.startswith(prefix + "/") for f in files)  # noqa: E731
        if match(conflict_files):
            return "conflict"
        if match(incoming_files):
            return "incoming"
        if match(outgoing_files):
            return "local"
        return "ok"

    local = read_settings(p.settings)
    shared = read_settings(p.shared_settings())
    skills = shared_names(p, "skills")
    paths = sorted(managed_paths(shared, set(skills), local))
    base = decode_base(s.baseline)
    settings_state = state_of("settings.json")
    if has_setting_conflict:
        settings_state = "conflict"
    elif settings_state == "ok" and any(get_path(local, x) != base.get(x, MISSING) for x in paths if x in base):
        settings_state = "local"
    incoming_names: Dict[str, Set[str]] = {"skills": set(), "mods": set()}
    for f in incoming_files:
        parts = f.split("/")
        if parts[0] in incoming_names and len(parts) > 1:
            incoming_names[parts[0]].add(parts[1])

    def items(kind: str) -> List[dict]:
        names = sorted(set(shared_names(p, kind)) | incoming_names[kind])
        return [{"name": n, "state": state_of("%s/%s" % (kind, n))} for n in names]

    managed_tops = {x[0] for x in paths}
    others: Dict[str, int] = {}
    if remote or current:
        for c in commits_between(p, None, remote or current)[-300:]:
            if c.get("tagged") and c["pc"] != s.pc_name:
                others[c["pc"]] = max(others.get(c["pc"], 0), c["time"])
    return {
        "pc": s.pc_name,
        "now": time.time(),
        "last_pull": s.last_pull,
        "last_push": s.last_push,
        "last_result": s.last_result,
        "last_error": s.last_error,
        "auto_enabled": s.auto_enabled,
        "require_approval": s.require_approval,
        "receive_only": s.receive_only,
        "backend": s.backend,
        "storage": _storage(p, s),
        "others": [{"pc": k, "time": v} for k, v in sorted(others.items(), key=lambda kv: -kv[1])],
        "items": {
            "claude_md": {"state": state_of("CLAUDE.md")},
            "settings": {"count": len(paths), "keys": [_label(x) for x in paths], "state": settings_state},
            "skills": items("skills"),
            "mods": items("mods"),
        },
        # Shared and PC-only apart: a PC-only item may carry the name of a shared one.
        "descriptions": {
            "shared": {k: _descriptions(os.path.join(p.shared, k), k, shared_names(p, k)) for k in ("skills", "mods")},
            "local": {k: _descriptions(kind_dir(p, k), k, local_only(p, k)) for k in ("skills", "mods")},
        },
        "local_only": {
            "skills": local_only(p, "skills"),
            "mods": local_only(p, "mods"),
            "settings": [k for k in local if k not in managed_tops],
        },
        "incoming": _count(p, "HEAD..%s" % REMOTE) if remote and current else (1 if remote else 0),
        "outgoing": (_count(p, "%s..HEAD" % REMOTE) if remote and current else (1 if current else 0)) + (1 if _dirty(p) else 0),
        "conflicts": s.conflicts,
        "blocked": s.blocked,
        "pending_approval": s.pending_approval,
        "collisions": s.collisions,
        "overwritten": s.overwritten,
        "words": load_words(p),
    }


def log(p: Paths, n: int = 30) -> List[dict]:
    if head(p) is None:
        return []
    fmt = "%x1e%H%x1f%ct%x1f%an%x1f%(trailers:key=PC,valueonly,separator=)%x1f%s"
    out = git(p, "log", "-n", str(n), "--no-merges", "--name-only", "--format=" + fmt, "HEAD").stdout
    entries = []
    for chunk in out.split("\x1e")[1:]:
        meta, _, rest = chunk.partition("\n")
        sha, ts, author, pc, subject = meta.split("\x1f", 4)
        files = [f for f in rest.splitlines() if f.strip()]
        entries.append({"sha": sha, "time": int(ts), "pc": pc.strip() or author, "subject": subject, "files": files})
    return entries


ITEM_PATHS = {"claude_md": ["CLAUDE.md"], "settings": ["settings.json"]}


def diff(p: Paths, item: str) -> dict:
    if item in ITEM_PATHS:
        paths = ITEM_PATHS[item]
    elif item.split("/")[0] in ("skills", "mods") and len(item.split("/")) == 2:
        paths = [item]
    elif item == "approval":
        s = load_state(p)
        if not s.pending_approval:
            return {"text": ""}
        info = s.pending_approval[0]
        mb = _merge_base(p, head(p), info["remote"])
        args = ["diff", "--no-color"] + ([mb, info["remote"]] if mb else [info["remote"]]) + ["--"] + (info["files"] + ["settings.json"])
        return {"text": git(p, *args).stdout}
    else:
        return {"text": ""}
    current, remote = head(p), ref(p, REMOTE)
    mb = _merge_base(p, current, remote)
    parts = []
    if remote and mb and remote != mb:
        parts.append((t("받을 변경", "Incoming changes"), git(p, "diff", "--no-color", mb, remote, "--", *paths).stdout))
    if current and mb and current != mb:
        parts.append((t("보낼 변경", "Outgoing changes"), git(p, "diff", "--no-color", mb, current, "--", *paths).stdout))
    if current:
        parts.append((t("아직 정리 안 된 변경", "Changes not settled yet"), git(p, "diff", "--no-color", "HEAD", "--", *paths).stdout))
    text = "\n".join("## %s\n%s" % (title, body) for title, body in parts if body.strip())
    return {"text": text}
