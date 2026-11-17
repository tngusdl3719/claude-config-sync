"""The shared history kept in a Notion database: one row per upload, each carrying a git bundle
(rowstore has how rows make a history). Rows have minute-precision times; order comes from git ancestry.
"""
from __future__ import annotations

import gzip
import json
import os
import secrets
import signal
import subprocess
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import rowstore
from .lang import t
from .gitio import git, head
from .notion import (DELTA_NAME, FULL_NAME, OAUTH_ERRORS, Client, NotionError, authorize_url, broker_url, files, oauth_token,
                     page_id, plain, rich, select, text, title)
from .paths import Paths
from .remote import helper
from .state import State, write_atomic

DB_TITLE = "Claude Sync"
SECRET_HOST = "api.notion.com"
SECRET_USER = "claude-sync"
PART_BYTES = 4_500_000
FULL, DELTA = FULL_NAME, DELTA_NAME
ROW_REFS = rowstore.ROW_REFS
# Signing in with Notion (spec §7): the fixed local address registered as the connection's redirect URI.
OAUTH_PORT = 47821
OAUTH_CLIENT, OAUTH_REFRESH = "oauth/client", "oauth/refresh"
# A sign-in not yet tied to a page keeps its own trio, so the pair a working sync uses stays intact.
OAUTH_PENDING, PENDING_REFRESH, PENDING_CLIENT = "oauth/pending", "oauth/pending-refresh", "oauth/pending-client"
CALLBACK_READ_SECONDS = 5
RECONNECT = ("Notion 연결이 끊겼어요. 앱에서 'Notion으로 연결'을 다시 해 주세요",
             "The Notion connection is broken. Use 'Connect with Notion' in the app again")
PROPERTIES = {
    "요약": {"title": {}},
    "PC": {"rich_text": {}},
    "Head": {"rich_text": {}},
    "Base": {"rich_text": {}},
    "종류": {"select": {"options": [{"name": FULL}, {"name": DELTA}]}},
    "묶음": {"files": {}},
}


# --- the secret: keychain only, through git's credential helper (stdin/stdout, never argv) ---

def _credential(p: Paths, action: str, page: str, secret: Optional[str] = None) -> subprocess.CompletedProcess:
    data = "protocol=https\nhost=%s\npath=%s\nusername=%s\n" % (SECRET_HOST, page, SECRET_USER)
    if secret is not None:
        data += "password=%s\n" % secret
    argv = ["git", "-c", "credential.helper=", "-c", "credential.helper=%s" % helper(),
            "-c", "credential.useHttpPath=true", "-c", "core.askPass=", "credential", action]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="")
    cwd = p.claude if os.path.isdir(p.claude) else p.home
    try:
        return subprocess.run(argv, input=data + "\n", cwd=cwd, capture_output=True, text=True, env=env, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        # A keychain that does not answer is a failed keychain call, marked so a backup can tell.
        return subprocess.CompletedProcess(argv, 1, "", KEYCHAIN_SILENT)


def save_secret(p: Paths, page: str, secret: str) -> None:
    if not secret or "\n" in secret:
        raise NotionError(0, "bad_secret", t("Notion 비밀 키가 비어 있거나 올바르지 않아요", "The Notion secret key is empty or not valid"))
    failed = NotionError(0, "keychain", t("Notion 비밀 키를 키체인에 저장하지 못했어요", "Could not save the Notion secret key in the keychain"))
    # 'approve' succeeds even when the helper kept nothing (e.g. the keychain was out of reach): read it back.
    if _credential(p, "approve", page, secret).returncode != 0:
        raise failed
    kept = _stored(p, page)
    if kept is not None and kept != secret:
        # git's keychain helper before 2.45 (Apple's git up to Xcode 16) leaves an item already there as it was.
        forget_secret(p, page)
        if _credential(p, "approve", page, secret).returncode != 0:
            raise failed
        kept = _stored(p, page)
    if kept != secret:
        raise failed


def _stored(p: Paths, page: str) -> Optional[str]:
    try:
        return load_secret(p, page)
    except NotionError:
        return None


KEYCHAIN_SILENT = "the keychain did not answer"


def _backup(p: Paths, key: str) -> Optional[str]:
    """What the key holds now (None: nothing). Raises when the keychain cannot tell, so nothing is changed."""
    r = _credential(p, "fill", key)
    if r.stderr == KEYCHAIN_SILENT:
        raise NotionError(0, "keychain", t("키체인이 응답하지 않아 아무것도 바꾸지 않았어요. 잠시 뒤 다시 하세요", "The keychain did not answer, so nothing was changed. Try again in a moment"))
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        if line.startswith("password="):
            return line[len("password="):]
    return None


def _put_back(p: Paths, values: dict) -> None:
    for key, value in values.items():
        try:
            if value is None:
                forget_secret(p, key)
            else:
                save_secret(p, key, value)
        except NotionError:
            pass  # the keychain is out of reach: the error being raised already says so


def forget_secret(p: Paths, key: str) -> None:
    _credential(p, "reject", key)


def load_secret(p: Paths, page: str) -> str:
    r = _credential(p, "fill", page)
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        if line.startswith("password="):
            return line[len("password="):]
    raise NotionError(401, "no_secret", t("이 Mac 키체인에 Notion 비밀 키가 없어요. 설정에서 비밀 키를 다시 넣어 주세요", "This Mac's keychain has no Notion secret key. Enter the secret key again in the settings"))


# --- setting up a PC ---

def find_data_source(c: Client, page: str) -> Optional[str]:
    for block in c.children(page):
        if block.get("type") == "child_database" and (block.get("child_database") or {}).get("title") == DB_TITLE:
            return c.data_source_of(block["id"])
    return None


def pending_token(p: Paths) -> str:
    """The access token the last 'Notion으로 연결' brought, before a page was chosen."""
    try:
        return load_secret(p, OAUTH_PENDING)
    except NotionError:
        raise NotionError(401, "no_login", t("먼저 앱에서 'Notion으로 연결'을 해 주세요", "Use 'Connect with Notion' in the app first")) from None


def _open_browser(url: str) -> None:
    subprocess.run(["/usr/bin/open", url], check=False, capture_output=True, timeout=30)


CALLBACK_PAGE = ("<!doctype html><meta charset=utf-8><title>Claude Sync</title>"
                 "<p style='font:16px -apple-system'>%s</p>")
CALLBACK_TEXT = ("Notion 연결을 받았어요. 이 창을 닫고 Claude Sync로 돌아가세요.",
                 "Notion is connected. Close this window and go back to Claude Sync.")


def sign_in_record(p: Paths) -> str:
    """Where a waiting 'Notion으로 연결' notes its process and port, so a newer one may end it."""
    return os.path.join(p.sync_dir, "notion-login.json")


def process_started(pid: int) -> str:
    """When a process started (as ps tells it): with its number, it names one run of one program."""
    try:
        return subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _note_sign_in(p: Paths, port: int) -> None:
    write_atomic(sign_in_record(p), json.dumps({"pid": os.getpid(), "port": port,
                                                "started": process_started(os.getpid())}))


def _forget_sign_in(p: Paths) -> None:
    if _recorded_sign_in(p)[0] == os.getpid():  # a newer sign-in's record stays
        try:
            os.remove(sign_in_record(p))
        except OSError:
            pass


def _read_record(p: Paths) -> dict:
    try:
        with open(sign_in_record(p), encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _recorded_sign_in(p: Paths) -> Tuple[Optional[int], Optional[int], str]:
    r = _read_record(p)
    pid, port, started = r.get("pid"), r.get("port"), r.get("started")
    return ((pid if isinstance(pid, int) else None), (port if isinstance(port, int) else None),
            started if isinstance(started, str) else "")


def _listener(port: int) -> Optional[Tuple[int, int, str]]:
    """Who listens on the port: (pid, user id, program name), when lsof can tell."""
    def run(argv: List[str]) -> str:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        pid = next((int(x) for x in run(["/usr/sbin/lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"]).split()
                    if x.isdigit()), None)
        if pid is None:
            return None
        uid = run(["/bin/ps", "-o", "uid=", "-p", str(pid)])
        return pid, int(uid) if uid.isdigit() else -1, os.path.basename(run(["/bin/ps", "-o", "comm=", "-p", str(pid)]))
    except (OSError, subprocess.TimeoutExpired):
        return None


def _listen(p: Paths, port: int, make: Callable[[], HTTPServer]) -> HTTPServer:
    """The callback server. An earlier 'Notion으로 연결' of this engine still waiting on the port (closed
    window, second click) gives way to this one: only the process it recorded, of this user. Any other
    program keeps the port, and the message says what to do."""
    try:
        return make()
    except OSError:
        pass
    holder = _listener(port)
    pid, recorded_port, started = _recorded_sign_in(p)
    # The very run that recorded itself: same number, same port, same start (a number can be reused).
    if (holder and holder[0] != os.getpid() and holder[1] == os.getuid() and (pid, recorded_port) == (holder[0], port)
            and started and started == process_started(holder[0])):
        try:
            os.kill(holder[0], signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return make()
            except OSError:
                time.sleep(0.2)
    if holder:
        why = t("Notion 연결에 쓰는 %d번 포트를 다른 프로그램(%s)이 쓰고 있어요.",
                "Port %d, which the Notion connection uses, is taken by another program (%s).") % (port, holder[2] or "?")
    else:
        why = t("Notion 연결에 쓰는 %d번 포트를 다른 프로그램이 쓰고 있어요(터미널에서 lsof -iTCP:%d 로 확인).",
                "Port %d, which the Notion connection uses, is taken by another program (see it with lsof -iTCP:%d in Terminal).") % (port, port)
    raise NotionError(0, "port_busy", why + t(" 그 프로그램을 끈 뒤 다시 하거나, '비밀 키로 연결하기'로 연결하세요.",
                                                  " Quit that program and try again, or connect with 'Use a secret key instead'."))


def _client_text(client_id: str, client_secret: str, broker: str) -> str:
    """How a sign-in's client is kept in the keychain (for refreshes): the broker, or the id and secret."""
    return json.dumps({"client_id": client_id, "broker": broker}) if broker else "%s %s" % (client_id, client_secret)


def _client_from(text: str) -> Tuple[str, str, str]:
    """(client id, secret, broker) from what _client_text kept; ValueError when it is not one."""
    if text.startswith("{"):
        kept = json.loads(text)
        broker = broker_url(str(kept.get("broker") or ""))
        if not kept.get("client_id") or not broker:
            raise ValueError("no client")
        return str(kept["client_id"]), "", broker
    client_id, client_secret = text.split(" ", 1)
    return client_id, client_secret, ""


def login(p: Paths, client_id: str, client_secret: str = "", *, broker: str = "",
          open_url: Optional[Callable[[str], None]] = None, port: int = OAUTH_PORT, timeout: float = 300) -> dict:
    """Sign in with Notion: consent in the browser, the code comes back to this Mac, tokens go to
    the keychain. Returns the pages the person shared (to pick the sync page from). The code becomes
    tokens with the client secret, or through `broker` (server/notion-broker), which holds it."""
    if broker:
        broker = broker_url(broker)
        if not broker:
            raise NotionError(0, "bad_broker", t("Notion 로그인 중계 주소가 올바르지 않아요", "The Notion sign-in broker address is not valid"))
    if not client_id or not (client_secret or broker):
        raise NotionError(0, "no_client", t("Notion 공개 연결 정보가 없어요", "No Notion public connection details"))
    state = secrets.token_urlsafe(24)
    answer: dict = {}

    class Callback(BaseHTTPRequestHandler):
        timeout = CALLBACK_READ_SECONDS  # a connection that never finishes its request is dropped

        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path != "/callback" or not ("code" in query or "error" in query):
                self.send_response(404)
                self.end_headers()
                return
            answer.setdefault("query", query)
            data = (CALLBACK_PAGE % t(*CALLBACK_TEXT)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    class Server(HTTPServer):
        def handle_error(self, request, client_address):
            pass  # a broken local connection is not the login's failure

    server = _listen(p, port, lambda: Server(("127.0.0.1", port), Callback))
    _note_sign_in(p, server.server_address[1])
    try:
        redirect = "http://localhost:%d/callback" % server.server_address[1]
        (open_url or _open_browser)(authorize_url(client_id, redirect, state))
        server.timeout = 0.5
        deadline = time.monotonic() + timeout
        while "query" not in answer and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
        _forget_sign_in(p)
    query = answer.get("query")
    if query is None:
        raise NotionError(0, "timeout", t("Notion 연결을 기다리는 시간이 지났어요. 다시 시도하세요", "Waiting for the Notion connection timed out. Try again"))
    if not secrets.compare_digest(query.get("state", ""), state):
        raise NotionError(0, "bad_state", t("Notion에서 돌아온 응답을 확인하지 못했어요. 다시 시도하세요", "Could not verify Notion's answer. Try again"))
    if "error" in query:
        if query["error"] == "access_denied":
            raise NotionError(0, "access_denied", t("Notion 연결을 취소했어요", "The Notion connection was cancelled"))
        shown = query["error"] if query["error"] in OAUTH_ERRORS else "unknown"
        raise NotionError(0, "oauth_error", t("Notion 연결에 실패했어요: %s", "Could not connect to Notion: %s") % shown)
    tokens = oauth_token(client_id, client_secret, {"grant_type": "authorization_code", "code": query.get("code", ""),
                                                    "redirect_uri": redirect}, broker=broker)
    save_secret(p, PENDING_CLIENT, _client_text(client_id, client_secret, broker))
    save_secret(p, PENDING_REFRESH, tokens["refresh_token"])
    save_secret(p, OAUTH_PENDING, tokens["access_token"])
    return {"pages": Client(tokens["access_token"]).search_pages(), "workspace": tokens.get("workspace_name") or ""}


def _refresh(p: Paths, s: State) -> None:
    """A new token pair for a signed-in PC; runs under the sync lock, so one refresh token is used once."""
    try:
        client_id, client_secret, broker = _client_from(load_secret(p, OAUTH_CLIENT))
        refresh = load_secret(p, OAUTH_REFRESH)
    except (NotionError, ValueError):
        raise NotionError(401, "reconnect", t(*RECONNECT)) from None
    try:
        tokens = oauth_token(client_id, client_secret, {"grant_type": "refresh_token", "refresh_token": refresh},
                             broker=broker)
    except NotionError as e:
        if e.status in (400, 401):
            raise NotionError(401, "reconnect", t(*RECONNECT)) from None
        raise
    save_secret(p, OAUTH_REFRESH, tokens["refresh_token"])  # the old one no longer works
    save_secret(p, s.notion_page_id or "", tokens["access_token"])


def _signed_in(p: Paths, s: State, work: Callable[[], Optional[str]]) -> Optional[str]:
    """Runs `work`; a signed-in PC whose token was refused gets one refresh and one more try."""
    try:
        return work()
    except NotionError as e:
        if e.status != 401 or s.notion_auth != "oauth":
            raise
    _refresh(p, s)
    return work()


OTHER_HISTORY = ("이 페이지에는 이 PC와 이어지지 않는 다른 공용 설정이 있어요. 빈 페이지나 이 PC들이 쓰는 페이지를 고르세요",
                 "This page holds other shared settings that do not continue this PC's. Choose an empty page or the one these PCs use")


def check_move(p: Paths, page_text: str, secret: str, *, receive_only: bool) -> None:
    """Before this PC moves to the page: what the page holds must continue this PC's history. A
    receive-only PC makes nothing in Notion, so the sending PC's database must already be there."""
    page = page_id(page_text)
    c = Client(secret)
    ds = find_data_source(c, page)
    if ds is None:
        if receive_only:
            raise NotionError(404, "no_database", t("이 페이지에는 아직 공용 설정이 없어요. 올리는 PC를 먼저 이 페이지로 옮기세요", "This page has no shared settings yet. Move a PC that uploads to this page first"))
        return
    store = NotionRows(c, ds)
    rows = store.window()
    if not rows:
        return
    try:
        rowstore.absorb(p, store, rows)
        theirs, current = rowstore.tip(p, rows), head(p)
        if theirs is None:
            raise NotionError(409, "unreadable", t("이 페이지의 공용 설정을 받지 못했어요. 잠시 뒤 다시 하세요", "Could not fetch this page's shared settings. Try again in a moment"))
        if current and not git(p, "merge-base", theirs, current, check=False).stdout.strip():
            raise NotionError(409, "other_history", t(*OTHER_HISTORY))
    finally:
        rowstore.forget_rows(p)


def connect(p: Paths, s: State, page_text: str, secret: str, *, create: bool, require_empty: bool = False,
            auth: str = "secret") -> None:
    """Point this PC at the page's "Claude Sync" database (made when `create`); store the secret."""
    page = page_id(page_text)
    c = Client(secret)
    ds = find_data_source(c, page)
    if ds is not None and require_empty and c.query(ds, page_size=1, limit=1):
        raise NotionError(409, "not_empty", t("이 페이지에는 이미 공용 설정이 있어요. '다른 PC 연결'로 설정하세요", "This page already holds shared settings. Set this PC up with 'Receive another PC's settings'"))
    if ds is None:
        if not create:
            raise NotionError(404, "no_database", t("아직 첫 PC 설정이 없어요. 첫 PC에서 먼저 설정하세요", "No first PC yet. Set up the first PC first"))
        _, ds = c.create_database(page, DB_TITLE, PROPERTIES)
    # The sign-in now belongs to this page: it becomes the pair refreshes use.
    new = {page: secret}
    if auth == "oauth":
        new[OAUTH_CLIENT], new[OAUTH_REFRESH] = load_secret(p, PENDING_CLIENT), load_secret(p, PENDING_REFRESH)
    old = {key: _backup(p, key) for key in new}
    tried: List[str] = []
    try:
        for key, value in new.items():
            tried.append(key)
            save_secret(p, key, value)
    except NotionError:
        _put_back(p, {key: old[key] for key in tried})  # never half of one sign-in and half of another
        raise
    if auth == "oauth":
        for key in (OAUTH_PENDING, PENDING_REFRESH, PENDING_CLIENT):
            forget_secret(p, key)
    s.backend, s.notion_page_id, s.notion_data_source_id = "notion", page, ds
    s.notion_auth = auth


# --- rows ---

def _client(p: Paths, s: State) -> Tuple[Client, str]:
    if not s.notion_page_id or not s.notion_data_source_id:
        raise NotionError(0, "not_set", t("Notion 저장소 설정이 없어요. 설정을 다시 하세요", "No Notion storage settings. Set up again"))
    return Client(load_secret(p, s.notion_page_id)), s.notion_data_source_id


def _key(row: dict) -> Tuple[str, str]:
    return row.get("created_time", ""), plain(row.get("id", ""))


def window(c: Client, ds: str) -> List[dict]:
    """Rows from the latest full row on (with every row of its minute), oldest first."""
    def reached(rows: List[dict]) -> bool:
        full = next((r for r in rows if select(r, "종류") == FULL), None)
        return full is not None and any(r["created_time"] < full["created_time"] for r in rows)

    rows = c.query(ds, descending=True, stop=reached)
    full = next((r for r in rows if select(r, "종류") == FULL), None)
    if full is not None:
        rows = [r for r in rows if r["created_time"] >= full["created_time"]]
    return sorted(rows, key=_key)


def _part_data(name: str, data: bytes) -> bytes:
    """'.gz' parts are gzip files of their own; any other name holds the bundle bytes as they are."""
    return gzip.decompress(data) if name.endswith(".gz") else data


class NotionRows(rowstore.Store):
    """The rows of a page's "Claude Sync" database."""

    def __init__(self, c: Client, ds: str):
        self.c, self.ds = c, ds
        self.raw: dict = {}

    def window(self) -> List[rowstore.Row]:
        rows = []
        for r in window(self.c, self.ds):
            row = rowstore.Row(id=plain(r["id"]), time=r.get("created_time", ""), full=select(r, "종류") == FULL,
                               head=text(r, "Head").strip(), base=text(r, "Base").strip())
            self.raw[row.id] = r
            rows.append(row)
        return rows

    def read(self, row: rowstore.Row, path: str) -> bool:
        parts = sorted(files(self.raw[row.id], "묶음"), key=lambda f: f["name"])
        with open(path, "wb") as f:
            for part in parts:
                data = self.c.download(part["url"])
                try:
                    f.write(_part_data(part["name"], data))
                except (OSError, EOFError, zlib.error):
                    return False
        return True

    def add(self, *, full: bool, head: str, base: str, summary: str, pc: str, data: bytes) -> None:
        attached = []
        for i in range(0, max(len(data), 1), PART_BYTES):
            # The File Upload API only takes names with a known extension: each part is a gzip file of its own.
            name = "part-%03d.gz" % (i // PART_BYTES + 1)
            part = gzip.compress(data[i:i + PART_BYTES], mtime=0)
            attached.append({"type": "file_upload", "file_upload": {"id": self.c.upload(name, part)}, "name": name})
        self.c.create_row(self.ds, {
            "요약": title(summary),
            "PC": rich(pc),
            "Head": rich(head),
            "Base": rich(base),
            "종류": {"select": {"name": FULL if full else DELTA}},
            "묶음": {"files": attached},
        })


def fetch(p: Paths, s: State) -> Optional[str]:
    """Bring in new rows and point origin/main at the remote tip; an error text, or None."""
    try:
        return _signed_in(p, s, lambda: rowstore.fetch(p, NotionRows(*_client(p, s))))
    except NotionError as e:
        return str(e)


def push(p: Paths, s: State, remote: Optional[str], current: str) -> Optional[str]:
    """Upload HEAD (== current) as one row; an error text, or None."""
    try:
        return _signed_in(p, s, lambda: rowstore.push(p, s.pc_name, NotionRows(*_client(p, s)), remote, current))
    except NotionError as e:
        return str(e)
