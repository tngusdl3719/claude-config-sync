"""Words that must never leave this PC, checked against what is about to be pushed."""
from __future__ import annotations

import re
from typing import List

from .gitio import git
from .paths import Paths
from .state import write_atomic

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def load_words(p: Paths) -> List[str]:
    try:
        with open(p.blocklist_file, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]


def save_words(p: Paths, words: List[str]) -> None:
    seen: List[str] = []
    for w in words:
        w = w.strip()
        if w and w not in seen:
            seen.append(w)
    write_atomic(p.blocklist_file, "".join(w + "\n" for w in seen))


def scan_outgoing(p: Paths, base: str, head: str, words: List[str]) -> List[dict]:
    hits: List[dict] = []
    for line in git(p, "diff", "--numstat", base, head).stdout.splitlines():
        added, removed, name = line.split("\t", 2)
        if added == "-" and removed == "-":
            hits.append({"file": name, "line": 0, "text": "", "word": "(binary)"})
    lowered = [(w, w.lower()) for w in words]
    for line in git(p, "diff", "--name-status", "-M", "--diff-filter=ACR", base, head).stdout.splitlines():
        name = line.split("\t")[-1]
        low = name.lower()
        for word, lw in lowered:
            if lw in low:
                hits.append({"file": name, "line": 0, "text": name, "word": word})
                break
    current, number, in_header = None, 0, False
    for line in git(p, "diff", "-U0", "--no-color", "--no-ext-diff", base, head).stdout.splitlines():
        if line.startswith("diff --git "):
            current, in_header = None, True
            continue
        if in_header:
            if line.startswith("+++ "):
                current = None if line[4:] == "/dev/null" else line[6:]
            m = HUNK.match(line)
            if m:
                number, in_header = int(m.group(1)), False
            continue
        m = HUNK.match(line)
        if m:
            number = int(m.group(1))
            continue
        if line.startswith("+") and current is not None:
            text = line[1:]
            low = text.lower()
            for word, lw in lowered:
                if lw in low:
                    hits.append({"file": current, "line": number, "text": text, "word": word})
                    break
            number += 1
    return hits
