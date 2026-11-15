"""The language of the engine's messages: Korean, unless CLAUDE_SYNC_LANG asks for English.

The app passes its own choice; Claude Code's /sync and the command line keep Korean."""
from __future__ import annotations

import os


def english() -> bool:
    return os.environ.get("CLAUDE_SYNC_LANG", "").strip().lower().startswith("en")


def t(ko: str, en: str) -> str:
    """The message in the language asked for: `t("한국어", "English")`."""
    return en if english() else ko
