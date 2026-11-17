"""This PC's own record (never synced): ~/.claude/sync/state.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from .paths import Paths


@dataclass
class State:
    pc_name: str = ""
    last_pull: Optional[float] = None
    last_push: Optional[float] = None
    # ok | offline | blocked | conflict | needs_approval | error | never
    last_result: str = "never"
    last_error: Optional[str] = None
    auto_enabled: bool = True
    require_approval: bool = False
    # A PC whose network refuses uploads (or that must never send): receive, never push.
    receive_only: bool = False
    # Where the shared history travels: "git" (origin), "notion" (a database of git bundles) or "folder"
    # (git bundles in a synced folder: folder_path).
    backend: str = "git"
    notion_page_id: Optional[str] = None
    notion_data_source_id: Optional[str] = None
    # How this PC reaches Notion: "secret" (an internal connection's key) or "oauth" (signed in, refreshable).
    notion_auth: str = "secret"
    folder_path: Optional[str] = None
    # JSON-encoded settings path -> value last applied on this PC
    baseline: Dict[str, Any] = field(default_factory=dict)
    conflicts: List[dict] = field(default_factory=list)
    blocked: List[dict] = field(default_factory=list)
    allow_once: Optional[str] = None
    pending_approval: List[dict] = field(default_factory=list)
    approved_remote: Optional[str] = None
    collisions: List[dict] = field(default_factory=list)
    overwritten: List[dict] = field(default_factory=list)


def write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_bytes_atomic(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def load_state(p: Paths) -> State:
    try:
        with open(p.state_file, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        return State()
    known = {f.name for f in fields(State)}
    return State(**{k: v for k, v in raw.items() if k in known})


def save_state(p: Paths, s: State) -> None:
    write_atomic(p.state_file, json.dumps(asdict(s), ensure_ascii=False, indent=2) + "\n")
