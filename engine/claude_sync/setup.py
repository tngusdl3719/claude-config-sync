"""init: the first PC moves its shared items into a new repo. join + setup: a new PC joins."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

from . import actions, notion_store, remote as remote_mod
from .blocklist import save_words
from .claudemd import IMPORT_LINE
from .gitio import git, head, identity, message, ref
from .notion import NotionError, page_id
from .links import KINDS, kind_dir
from .paths import Paths
from .settings_merge import CONTAINERS, NEVER, encode_base, flatten, get_path, MISSING, read_settings, set_path
from .state import State, load_state, save_state, write_atomic
from .sync import REMOTE, ensure_gitignore, run_sync, write_shared_settings

REMOTE_REF = "refs/remotes/" + REMOTE

MANAGED_SETTINGS = "/Library/Application Support/ClaudeCode/managed-settings.json"
MANAGED_KEYS_OF_INTEREST = ("env", "enabledPlugins", "allowManagedHooksOnly", "strictKnownMarketplaces",
                            "disableAllHooks", "allowedMcpServers")


def backup(p: Paths, now: float) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    dest = os.path.join(p.claude, "backups", "config-sync-%s" % stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(p.claude, "backups", "config-sync-%s-%d" % (stamp, n))
    os.makedirs(dest)
    for src in (p.settings, p.claude_md):
        if os.path.exists(src):
            shutil.copy2(src, dest)
    for kind in KINDS:
        root = kind_dir(p, kind)
        lines = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                full = os.path.join(root, name)
                lines.append("%s -> %s" % (name, os.readlink(full)) if os.path.islink(full) else name)
        write_atomic(os.path.join(dest, "%s.txt" % kind), "".join(l + "\n" for l in lines))
    return dest


def _key_paths(local: dict, keys: List[str], shared_skills: List[str]) -> List[tuple]:
    out = []
    for key in keys:
        parts = tuple(key.split(".", 1))
        if len(parts) == 1 and parts[0] in CONTAINERS:
            entries = local.get(parts[0])
            if isinstance(entries, dict):
                for sub in entries:
                    if parts[0] == "skillOverrides" and sub not in shared_skills:
                        continue
                    out.append((parts[0], sub))
            continue
        out.append(parts)
    return [x for x in out if x not in NEVER]


class SetupError(RuntimeError):
    pass


OWN_MOD = "config-sync"
OTHER_REPO = "이 PC는 다른 저장소에 연결돼 있어요. 같은 저장소 정보로 다시 하거나, 저장소를 바꾸려면 set-remote 를 쓰세요"


def _packed_mod() -> Optional[str]:
    """The mod folder this engine runs from (the app's or the installer's copy), if it is one."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.isfile(os.path.join(root, ".claude-plugin", "plugin.json")) and \
            os.path.isfile(os.path.join(root, "bin", "claude-sync")):
        return root
    return None


def _share_own_mod(p: Paths) -> None:
    """A first PC set up from a packed engine shares that engine as the config-sync mod, so engine
    updates travel with syncs and /sync runs the same engine."""
    dst = os.path.join(p.shared, "mods", OWN_MOD)
    src = _packed_mod()
    if src is None or os.path.lexists(dst):
        return

    def skip(folder: str, names: List[str]) -> List[str]:
        out = [n for n in names if n in ("__pycache__", ".DS_Store") or n.endswith(".pyc")]
        return out + (["types"] if os.path.basename(folder) == ".claude-plugin" and "types" in names else [])

    shutil.copytree(src, dst, ignore=skip)


def _git_init(p: Paths) -> None:
    os.makedirs(os.path.dirname(p.shared), exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", p.shared], check=True, capture_output=True, text=True)


def stdin_secret() -> str:
    """A secret (Notion key or access token) arrives as one stdin line: never argv, env or files."""
    secret = sys.stdin.readline().strip()
    if not secret:
        raise actions.ActionError("비밀 키(토큰)를 표준 입력으로 넘겨 주세요")
    return secret


def _receive_git(p: Paths, url: str, username: str, token: str) -> Optional[str]:
    """An empty repo pointed at an https remote with the token in the keychain; fetched. Error text or None."""
    _git_init(p)
    target = remote_mod.configure_https(p, url, username)
    remote_mod.store_token(p, target, token, username=username)
    r = git(p, "fetch", "-q", "origin", check=False, timeout=remote_mod.NETWORK_SECONDS * 6)
    if r.returncode != 0:
        return ((r.stderr or "").strip().splitlines() or ["저장소에서 받지 못했어요"])[-1][:300]
    return None


def init(p: Paths, *, pc: str, skills: List[str], mods: List[str], keys: List[str], remote: Optional[str] = None,
         claude_md: str = "move", require_approval: bool = False, now: Optional[float] = None,
         backend: str = "git", page: Optional[str] = None, secret: Optional[str] = None,
         username: Optional[str] = None, token: Optional[str] = None, words: List[str] = (),
         oauth: bool = False) -> dict:
    now = time.time() if now is None else now
    if os.path.exists(p.shared):
        raise RuntimeError("이미 %s 가 있습니다" % p.shared)
    where = State()
    if backend == "notion":
        try:
            key = notion_store.pending_token(p) if oauth else secret or ""
            notion_store.connect(p, where, page or "", key, create=True, require_empty=True,
                                 auth="oauth" if oauth else "secret")
        except NotionError as e:
            raise SetupError(str(e)) from None
    dest = backup(p, now)
    if backend == "notion":
        _git_init(p)
    elif token:
        error = _receive_git(p, remote or "", username or "", token)
        if error is not None:
            shutil.rmtree(p.shared, ignore_errors=True)
            raise SetupError(error)
        if ref(p, REMOTE) is not None:  # e.g. a README the hosting site made
            git(p, "checkout", "-q", "-B", "main", REMOTE)
    else:
        subprocess.run(["git", "clone", "-q", remote, p.shared], check=True, capture_output=True, text=True)
    ensure_gitignore(p)
    for name in skills:
        actions.share(p, "skills", name, force=True)
    for name in mods:
        actions.share(p, "mods", name, force=True)
    _share_own_mod(p)
    if claude_md == "move":
        try:
            with open(p.claude_md, encoding="utf-8") as f:
                body = f.read()
        except FileNotFoundError:
            body = ""
        lines = [l for l in body.splitlines(True) if l.strip() != IMPORT_LINE]
        write_atomic(p.shared_claude_md(), "".join(lines).lstrip("\n"))
        write_atomic(p.claude_md, IMPORT_LINE + "\n")
    local = read_settings(p.settings)
    shared: dict = {}
    for path in _key_paths(local, keys, skills):
        value = get_path(local, path)
        if value is not MISSING:
            set_path(shared, path, value)
    write_shared_settings(p, shared)
    s = load_state(p)
    s.pc_name = pc
    s.require_approval = require_approval
    s.baseline = encode_base(flatten(shared))
    s.backend, s.notion_page_id, s.notion_data_source_id = where.backend, where.notion_page_id, where.notion_data_source_id
    s.notion_auth = where.notion_auth
    save_state(p, s)
    if words:  # in place before the first upload, which they check
        save_words(p, list(words))
    s = run_sync(p, auto=False, now=now)
    return {"result": s.last_result, "error": s.last_error, "backup": dest, "collisions": s.collisions}


def _installed_plugins(p: Paths) -> List[str]:
    path = os.path.join(p.claude, "plugins", "installed_plugins.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return []
    plugins = data.get("plugins", data) if isinstance(data, dict) else {}
    return list(plugins.keys()) if isinstance(plugins, dict) else []


def _managed_keys(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, PermissionError):
        return []
    return [k for k in MANAGED_KEYS_OF_INTEREST if isinstance(data, dict) and k in data]


def join(p: Paths, *, pc: str, words: List[str] = (), receive_only: bool = False, require_approval: bool = False,
         backend: str = "git", page: Optional[str] = None, secret: Optional[str] = None,
         remote: Optional[str] = None, username: Optional[str] = None, token: Optional[str] = None,
         now: Optional[float] = None, managed_settings: str = MANAGED_SETTINGS, oauth: bool = False) -> dict:
    """A new PC: receive the shared repo (git remote or Notion), then set up as `setup` does.
    Again on the same repo: store the secret anew and set up again (finishing a half-done setup).
    `oauth`: the Notion key is the token the last 'Notion으로 연결' brought."""
    setup_args = dict(pc=pc, words=list(words), require_approval=require_approval, now=now,
                      managed_settings=managed_settings, receive_only=receive_only)
    auth = "secret"
    if backend == "notion" and oauth:
        try:
            secret, auth = notion_store.pending_token(p), "oauth"
        except NotionError as e:
            raise SetupError(str(e)) from None
    if os.path.isdir(os.path.join(p.shared, ".git")) and head(p) is None:
        shutil.rmtree(p.shared)  # stopped before anything arrived: start over
    if os.path.exists(p.shared):
        return _join_again(p, backend, page, secret, remote, username, token, setup_args, auth)
    try:
        # Under the sync lock, like every sync: a token refresh during the first download is one too.
        with actions.holding(p):
            _receive_first(p, backend, page, secret, remote, username, token, auth)
    except actions.ActionError as e:
        raise SetupError(str(e)) from None
    return setup(p, **setup_args)


def _receive_first(p: Paths, backend: str, page: Optional[str], secret: Optional[str], remote: Optional[str],
                   username: Optional[str], token: Optional[str], auth: str) -> None:
    s = load_state(p)
    try:
        if backend == "notion":
            notion_store.connect(p, s, page or "", secret or "", create=False, auth=auth)
            _git_init(p)
            error = notion_store.fetch(p, s)
        else:
            error = _receive_git(p, remote or "", username or "", token or "")
        if error is not None:
            raise SetupError(error)
        if ref(p, REMOTE) is None:
            raise SetupError("아직 첫 PC 설정이 없어요. 첫 PC에서 먼저 설정하세요")
        git(p, "checkout", "-q", "-B", "main", REMOTE)
        save_state(p, s)
    except (NotionError, remote_mod.RemoteError) as e:
        shutil.rmtree(p.shared, ignore_errors=True)
        raise SetupError(str(e)) from None
    except BaseException:
        shutil.rmtree(p.shared, ignore_errors=True)
        raise


def _join_again(p: Paths, backend: str, page: Optional[str], secret: Optional[str], remote: Optional[str],
                username: Optional[str], token: Optional[str], setup_args: dict, auth: str = "secret") -> dict:
    if not os.path.isdir(os.path.join(p.shared, ".git")):
        raise SetupError("이미 %s 가 있어요" % p.shared)
    try:
        # Under the sync lock: the credentials a running sync (or its token refresh) uses change here.
        with actions.holding(p):
            s = load_state(p)
            if backend == "notion":
                if s.backend != "notion" or s.notion_page_id != page_id(page or ""):
                    raise SetupError(OTHER_REPO)
                notion_store.connect(p, s, page or "", secret or "", create=False, auth=auth)
            else:
                target = remote_mod.normalized(remote or "", username or "")
                current = git(p, "config", "--get", "remote.origin.url", check=False).stdout.strip()
                if s.backend != "git" or current != target:
                    raise SetupError(OTHER_REPO)
                remote_mod.store_token(p, target, token or "", username=username)
            save_state(p, s)
    except (NotionError, remote_mod.RemoteError, actions.ActionError) as e:
        raise SetupError(str(e)) from None
    return setup(p, **setup_args)


def setup(p: Paths, *, pc: str, words: List[str], require_approval: bool = False, now: Optional[float] = None,
          managed_settings: str = MANAGED_SETTINGS, receive_only: bool = False) -> dict:
    now = time.time() if now is None else now
    if not os.path.isdir(os.path.join(p.shared, ".git")):
        raise RuntimeError("먼저 공용 저장소를 %s 에 받아야 합니다" % p.shared)
    dest = backup(p, now)
    s = load_state(p)
    s.pc_name = pc
    s.require_approval = require_approval
    s.receive_only = receive_only
    save_state(p, s)
    if words:
        save_words(p, words)
    s = run_sync(p, auto=False, now=now)
    shared = read_settings(p.shared_settings())
    enabled = [k for k, v in (shared.get("enabledPlugins") or {}).items() if v]
    installed = set(_installed_plugins(p))
    return {
        "result": s.last_result,
        "error": s.last_error,
        "backup": dest,
        "overwritten": s.overwritten,
        "collisions": s.collisions,
        "missing_plugins": [k for k in enabled if k not in installed],
        "managed_settings_keys": _managed_keys(managed_settings),
    }


def use_notion(p: Paths, page: str, secret: Optional[str], *, oauth: bool = False) -> dict:
    """Move this PC to a Notion page's database (made if missing, never by a receive-only PC), with a
    pasted secret or the last 'Notion으로 연결'. Refused, leaving the PC as it was, when the page holds
    shared settings this PC's history does not continue. Its next upload is a full row."""
    with actions.holding(p):
        s = load_state(p)
        try:
            key = notion_store.pending_token(p) if oauth else secret or ""
            notion_store.check_move(p, page, key, receive_only=s.receive_only)
            notion_store.connect(p, s, page, key, create=not s.receive_only, auth="oauth" if oauth else "secret")
        except NotionError as e:
            raise actions.ActionError(str(e)) from None
        git(p, "update-ref", "-d", REMOTE_REF, check=False)
        for name in git(p, "for-each-ref", "--format=%(refname)", notion_store.ROW_REFS).stdout.split():
            git(p, "update-ref", "-d", name, check=False)
        save_state(p, s)
    return {"backend": "notion", "page": s.notion_page_id, "ok": True}


OTHER_HISTORY_GIT = "이 저장소에는 이 PC와 이어지지 않는 다른 공용 설정이 있어요. 빈 프로젝트나 이 PC들이 쓰는 저장소를 고르세요"
PROBE_REF = "refs/claude-sync/probe"


def _hosting_readme(p: Paths, sha: str) -> bool:
    """The one README commit a hosting site makes in a new project."""
    if git(p, "rev-list", "--count", sha).stdout.strip() != "1":
        return False
    names = git(p, "ls-tree", "-r", "--name-only", sha).stdout.split()
    return bool(names) and all(n.lower().startswith("readme") for n in names)


def _continue_remote_history(p: Paths) -> None:
    """The remote's main must continue this PC's history; a hosting README is joined in."""
    listed = git(p, "ls-remote", "origin", "refs/heads/main", check=False, timeout=remote_mod.NETWORK_SECONDS)
    if listed.returncode != 0:
        raise actions.ActionError("저장소에 연결하지 못했어요")
    if not listed.stdout.strip():
        return  # an empty project: the next sync sends this PC's history
    try:
        if git(p, "fetch", "-q", "origin", "+refs/heads/main:%s" % PROBE_REF, check=False,
               timeout=remote_mod.NETWORK_SECONDS).returncode != 0:
            raise actions.ActionError("저장소의 공용 설정을 받지 못했어요")
        theirs, current = ref(p, PROBE_REF), head(p)
        if not current or git(p, "merge-base", theirs, current, check=False).stdout.strip():
            return
        if not _hosting_readme(p, theirs):
            raise actions.ActionError(OTHER_HISTORY_GIT)
        name = load_state(p).pc_name
        r = git(p, *identity(name), "merge", "-q", "--allow-unrelated-histories", "--no-edit",
                "-m", message(name, "저장소 README 합치기"), theirs, check=False)
        if r.returncode != 0:
            git(p, "merge", "--abort", check=False)
            raise actions.ActionError(OTHER_HISTORY_GIT)
    finally:
        git(p, "update-ref", "-d", PROBE_REF, check=False)


def _put_back(p: Paths, url: str, tip: Optional[str], target: str, username: str, token: Optional[str]) -> None:
    """Undo a refused move: the old address, its tracking ref, and (same address) the old token."""
    if url:
        git(p, "config", "--replace-all", "remote.origin.url", url)
    else:
        git(p, "remote", "remove", "origin", check=False)
    if tip:
        git(p, "update-ref", REMOTE_REF, tip)
    else:
        git(p, "update-ref", "-d", REMOTE_REF, check=False)
    if token and url == target:
        remote_mod.store_token(p, target, token, username=username)


def use_git(p: Paths, url: str, username: str, token: str) -> dict:
    """Move this PC to a git remote, or give it a new token. Refused, leaving the PC as it was (address,
    tracking ref and token), when the remote cannot be reached or holds another history."""
    with actions.holding(p):
        old_url = git(p, "config", "--get", "remote.origin.url", check=False).stdout.strip()
        old_tip = ref(p, REMOTE_REF)
        try:
            target = remote_mod.normalized(url, username)
        except remote_mod.RemoteError as e:
            raise actions.ActionError(str(e)) from None
        old_token = remote_mod.load_token(p, target, username) if old_url == target else None
        try:
            out = remote_mod.set_remote(p, url, username, token)
            if not out.get("ok"):
                raise actions.ActionError(out.get("error") or "저장소에 연결하지 못했어요")
            _continue_remote_history(p)
        except (actions.ActionError, remote_mod.RemoteError) as e:
            _put_back(p, old_url, old_tip, target, username, old_token)
            raise actions.ActionError(str(e)) from None
        s = load_state(p)
        if s.backend != "git":
            s.backend = "git"
            for name in git(p, "for-each-ref", "--format=%(refname)", notion_store.ROW_REFS).stdout.split():
                git(p, "update-ref", "-d", name, check=False)
            save_state(p, s)
    return dict(out, backend="git")


def _preview(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def candidates(p: Paths) -> dict:
    """What a first PC could share: its own skills and mods, its settings keys, its CLAUDE.md."""
    def own(root: str) -> List[str]:
        if not os.path.isdir(root):
            return []
        return sorted(n for n in os.listdir(root) if not n.startswith(".")
                      and os.path.isdir(os.path.join(root, n)) and not os.path.islink(os.path.join(root, n)))

    local = read_settings(p.settings)
    keys = []
    for key in sorted(local):
        value = local[key]
        if (key,) in NEVER or key == "skillOverrides":  # skill switches follow their skills
            continue
        if key in CONTAINERS and isinstance(value, dict):
            keys += [{"key": "%s.%s" % (key, sub), "value_preview": _preview(value[sub])}
                     for sub in sorted(value) if (key, sub) not in NEVER]
        else:
            keys.append({"key": key, "value_preview": _preview(value)})
    try:
        with open(p.claude_md, encoding="utf-8") as f:
            body = [l for l in f.read().splitlines() if l.strip() != IMPORT_LINE]
        claude_md = {"exists": True, "lines": len(body)}
    except FileNotFoundError:
        claude_md = {"exists": False, "lines": 0}
    return {"skills": own(p.skills), "mods": own(p.mods), "keys": keys, "claude_md": claude_md}


def add_commands(sub) -> None:
    ini = sub.add_parser("init")
    ini.add_argument("--remote")
    ini.add_argument("--username")
    ini.add_argument("--token-stdin", action="store_true")
    ini.add_argument("--backend", choices=["git", "notion"], default="git")
    ini.add_argument("--page")
    ini.add_argument("--pc", required=True)
    ini.add_argument("--skills", default="")
    ini.add_argument("--mods", default="")
    ini.add_argument("--keys", default="")
    ini.add_argument("--claude-md", default="move", choices=["move", "keep"])
    ini.add_argument("--require-approval", action="store_true")
    ini.add_argument("--words", default="")
    ini.add_argument("--oauth", action="store_true")
    jn = sub.add_parser("join")
    jn.add_argument("--backend", choices=["git", "notion"], default="git")
    jn.add_argument("--page")
    jn.add_argument("--remote")
    jn.add_argument("--username")
    jn.add_argument("--pc", required=True)
    jn.add_argument("--words", default="")
    jn.add_argument("--require-approval", action="store_true")
    jn.add_argument("--receive-only", action="store_true")
    jn.add_argument("--oauth", action="store_true")
    sub.add_parser("candidates")
    nl = sub.add_parser("notion-login")
    nl.add_argument("--port", type=int, default=notion_store.OAUTH_PORT)
    nl.add_argument("--timeout", type=float, default=300)
    st = sub.add_parser("setup")
    st.add_argument("--pc", required=True)
    st.add_argument("--words", default="")
    st.add_argument("--require-approval", action="store_true")
    st.add_argument("--receive-only", action="store_true")


def _split(text: str) -> List[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def run(p: Paths, args) -> dict:
    if args.cmd == "init":
        notion = args.backend == "notion"
        if not notion and not args.remote:
            raise actions.ActionError("--remote 가 필요해요")
        secret = stdin_secret() if (notion and not args.oauth) or args.token_stdin else None
        return init(p, remote=args.remote, pc=args.pc, skills=_split(args.skills), mods=_split(args.mods),
                    keys=_split(args.keys), claude_md=args.claude_md, require_approval=args.require_approval,
                    backend=args.backend, page=args.page, secret=secret if notion else None,
                    username=args.username, token=None if notion else secret, words=_split(args.words),
                    oauth=notion and args.oauth)
    if args.cmd == "join":
        notion = args.backend == "notion"
        if not notion and (not args.remote or not args.username):
            raise actions.ActionError("--remote 와 --username 이 필요해요")
        secret = None if notion and args.oauth else stdin_secret()
        return join(p, pc=args.pc, words=_split(args.words), receive_only=args.receive_only,
                    require_approval=args.require_approval, backend=args.backend, page=args.page,
                    secret=secret if notion else None, remote=args.remote, username=args.username,
                    token=None if notion else secret,
                    managed_settings=os.environ.get("CLAUDE_SYNC_MANAGED_SETTINGS", MANAGED_SETTINGS),
                    oauth=notion and args.oauth)
    if args.cmd == "candidates":
        return candidates(p)
    if args.cmd == "notion-login":
        # The public connection's id and secret arrive as one JSON line on stdin, never in argv.
        try:
            client = json.loads(sys.stdin.readline() or "{}")
        except ValueError:
            client = {}
        if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
            raise actions.ActionError("Notion 공개 연결 정보를 표준 입력으로 넘겨 주세요")
        try:
            return notion_store.login(p, str(client["client_id"]), str(client["client_secret"]),
                                      port=args.port, timeout=args.timeout)
        except NotionError as e:
            raise actions.ActionError(str(e)) from None
    if args.cmd == "setup":
        return setup(p, pc=args.pc, words=_split(args.words), require_approval=args.require_approval,
                     managed_settings=os.environ.get("CLAUDE_SYNC_MANAGED_SETTINGS", MANAGED_SETTINGS),
                     receive_only=args.receive_only)
    raise actions.ActionError("알 수 없는 명령: %s" % args.cmd)
