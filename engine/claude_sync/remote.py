"""An https remote (GitLab with a per-PC deploy token): the token lives in the keychain, never in git config."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional, Tuple
from urllib.parse import quote, unquote

from .lang import t
from .gitio import git
from .paths import Paths

URL_RE = re.compile(r"^https://([A-Za-z0-9.-]+)/([A-Za-z0-9._/-]+\.git)$")
USER_RE = re.compile(r"^[A-Za-z0-9._+@-]{1,100}$")
WITH_USER_RE = re.compile(r"^https://([^@/]+)@([A-Za-z0-9.-]+)/([A-Za-z0-9._/-]+\.git)$")
FILE_RE = re.compile(r"^file:///([^\s]+)$")
TEST_HOST = "test.invalid"


class RemoteError(ValueError):
    pass


def _test_mode() -> bool:
    return os.environ.get("CLAUDE_SYNC_TEST_HTTPS_LIKE") == "1"


def helper() -> str:
    return os.environ.get("CLAUDE_SYNC_CREDENTIAL_HELPER") or "osxkeychain"


def guarded_helper() -> str:
    """The keychain helper for get and store only. Git erases a stored token whenever a
    request is refused, and a company proxy refusing uploads is not a wrong token."""
    base = helper()
    if base.startswith("!"):
        cmd = base[1:]
    elif base.startswith("/"):
        cmd = base
    else:
        cmd = "git credential-%s" % base
    return '!f() { case "$1" in get|store) %s "$1" ;; *) cat >/dev/null ;; esac; }; f' % cmd


def _validate(url: str, username: str) -> str:
    if not USER_RE.match(username or ""):
        raise RemoteError(t("배포 토큰 사용자 이름이 올바르지 않아요: %r", "Not a valid token user name: %r") % username)
    m = URL_RE.match(url or "")
    if m:
        return "https://%s@%s/%s" % (quote(username, safe="+._-"), m.group(1), m.group(2))
    if _test_mode() and FILE_RE.match(url or ""):
        return url
    raise RemoteError(t("https://호스트/경로.git 형태의 주소가 필요해요: %s", "An address like https://host/path.git is needed: %s") % url)


def normalized(url: str, username: str) -> str:
    """The origin address configure_https would set (https://user@host/path.git)."""
    return _validate(url, username)


def configure_https(p: Paths, url: str, username: str) -> str:
    target = _validate(url, username)
    old = git(p, "remote", "get-url", "origin", check=False)
    if old.returncode != 0:
        git(p, "remote", "add", "origin", target)
    else:
        # One fetch and push address only: every other url and pushurl of origin goes.
        git(p, "config", "--replace-all", "remote.origin.url", target)
        git(p, "config", "--unset-all", "remote.origin.pushurl", check=False)
    if old.returncode != 0 or old.stdout.strip() != target:
        # Tracking refs from the previous remote would make the new one look up to date.
        for ref in git(p, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").stdout.split():
            git(p, "update-ref", "-d", ref)
    git(p, "config", "--local", "--unset-all", "credential.helper", check=False)
    git(p, "config", "--local", "--add", "credential.helper", "")
    git(p, "config", "--local", "--add", "credential.helper", guarded_helper())
    git(p, "config", "--local", "credential.useHttpPath", "true")
    return target


def _fields(url_with_user: str, username: Optional[str]) -> Tuple[str, str, str]:
    m = WITH_USER_RE.match(url_with_user)
    if m:
        return m.group(2), m.group(3), unquote(m.group(1))
    f = FILE_RE.match(url_with_user)
    if f and _test_mode() and username:
        return TEST_HOST, f.group(1), username
    raise RemoteError(t("토큰을 저장할 주소가 올바르지 않아요", "Not a valid address to keep the token for"))


def store_token(p: Paths, url_with_user: str, token: str, runner: Callable = subprocess.run,
                username: Optional[str] = None) -> dict:
    if not token or "\n" in token:
        raise RemoteError(t("토큰이 비어 있거나 올바르지 않아요", "The token is empty or not valid"))
    host, path, user = _fields(url_with_user, username)
    data = "protocol=https\nhost=%s\npath=%s\nusername=%s\npassword=%s\n\n" % (host, path, user, token)
    argv = ["git", "-c", "credential.helper=", "-c", "credential.helper=%s" % helper(),
            "-c", "credential.useHttpPath=true", "credential", "approve"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = runner(argv, input=data, cwd=p.shared, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RemoteError(t("토큰을 키체인에 저장하지 못했어요", "Could not save the token in the keychain"))
    return {"stored": True, "host": host, "username": user}


def load_token(p: Paths, url_with_user: str, username: Optional[str] = None) -> Optional[str]:
    """The token the keychain holds for this address, if any (kept in memory, to put it back)."""
    try:
        host, path, user = _fields(url_with_user, username)
    except RemoteError:
        return None
    data = "protocol=https\nhost=%s\npath=%s\nusername=%s\n\n" % (host, path, user)
    argv = ["git", "-c", "credential.helper=", "-c", "credential.helper=%s" % helper(),
            "-c", "credential.useHttpPath=true", "-c", "core.askPass=", "credential", "fill"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="")
    r = subprocess.run(argv, input=data, cwd=p.shared, capture_output=True, text=True, env=env, timeout=60)
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        if line.startswith("password="):
            return line[len("password="):]
    return None


NETWORK_SECONDS = 20


def set_remote(p: Paths, url: str, username: str, token: str) -> dict:
    target = configure_https(p, url, username)
    store_token(p, target, token, username=username)
    r = git(p, "ls-remote", "origin", check=False, timeout=NETWORK_SECONDS)
    out = {"remote": target, "ok": r.returncode == 0}
    if r.returncode != 0:
        lines = (r.stderr or "").strip().splitlines()
        out["error"] = lines[-1][:300] if lines else t("저장소에 연결하지 못했어요", "Could not reach the repository")
    return out
