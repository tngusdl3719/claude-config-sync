"""~/.claude/CLAUDE.md starts with one import line; the rest stays this PC's."""
from __future__ import annotations

from .paths import Paths
from .state import write_atomic

IMPORT_LINE = "@~/.claude/shared/CLAUDE.md"


def ensure_import(p: Paths) -> bool:
    try:
        with open(p.claude_md, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        write_atomic(p.claude_md, IMPORT_LINE + "\n")
        return True
    if any(line.strip() == IMPORT_LINE for line in text.splitlines()):
        return False
    write_atomic(p.claude_md, IMPORT_LINE + "\n\n" + text)
    return True
