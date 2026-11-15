"""git in the shared working tree."""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Dict, Optional

from .paths import Paths

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SSH = "ssh -o ConnectTimeout=5 -o BatchMode=yes"


class GitError(RuntimeError):
    def __init__(self, args, result):
        detail = result.stderr or result.stdout or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        super().__init__("git %s failed: %s" % (" ".join(args), detail.strip()))
        self.result = result


def git(p: Paths, *args: str, check: bool = True, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None,
        binary: bool = False, input_text: Optional[str] = None, timeout: Optional[float] = None):
    """Runs git in the shared tree. With `timeout`, git and every process it started
    (ssh, the https helper) are killed at the deadline and the result's code is 124."""
    full_env = dict(os.environ)
    full_env.update({"GIT_SSH_COMMAND": SSH, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if env:
        full_env.update(env)
    argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.quotePath=false"]
    argv += list(args)
    proc = subprocess.Popen(argv, cwd=cwd or p.shared, env=full_env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    data = input_text.encode("utf-8") if input_text is not None else None
    try:
        out, err = proc.communicate(data, timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        code = 124
        err = (err or b"") + b"\ntimed out"
    if not binary:
        out = out.decode("utf-8", "replace")
        err = err.decode("utf-8", "replace")
    result = subprocess.CompletedProcess(argv, code, out, err)
    if check and code != 0:
        raise GitError(args, result)
    return result


def identity(pc: str):
    return ("-c", "user.name=%s" % pc, "-c", "user.email=%s@claude-sync" % pc)


def message(pc: str, summary: str) -> str:
    return "sync: %s: %s\n\nPC: %s\n" % (pc, summary, pc)


def is_cache(name: str) -> bool:
    parts = name.split("/")
    return "__pycache__" in parts[:-1] or name.endswith(".pyc") or parts[-1] == ".DS_Store"


def commit_all(p: Paths, pc: str, summary: str) -> Optional[str]:
    git(p, "add", "-A")
    # Caches never travel, whatever a nested .gitignore says.
    staged = [n for n in git(p, "ls-files", "-z").stdout.split("\0") if n and is_cache(n)]
    if staged:
        git(p, "rm", "-q", "--cached", "--", *staged)
    if git(p, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    git(p, *identity(pc), "commit", "-q", "-m", message(pc, summary))
    return head(p)


def head(p: Paths) -> Optional[str]:
    r = git(p, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.strip() or None


def ref(p: Paths, name: str) -> Optional[str]:
    r = git(p, "rev-parse", "--verify", "-q", name, check=False)
    return r.stdout.strip() or None
