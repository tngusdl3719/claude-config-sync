from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import redirect_stdout
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

from helpers import TempCase, make_home, make_remote, read_json
from fake_notion import CLIENT_ID, CLIENT_SECRET, FakeNotion
from test_cli_backends import call as cli_call

import claude_sync
from claude_sync import cli, notion_store
from claude_sync.gitio import head
from claude_sync.notion import NotionError
from claude_sync.paths import Paths
from claude_sync.actions import ActionError
from claude_sync.setup import SetupError, init, join, use_notion
from claude_sync.state import load_state
from claude_sync.sync import run_sync

NOW = 1_800_000_000.0
OLD_KEYCHAIN = """import json, os, sys
db, action = sys.argv[1], sys.argv[2]
items = json.load(open(db)) if os.path.exists(db) else {}
fields = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
key = "|".join(fields.get(k, "") for k in ("protocol", "host", "path", "username"))
if action == "get" and key in items:
    print("password=" + items[key])
elif action == "store":
    items.setdefault(key, fields.get("password", ""))
elif action == "erase":
    items.pop(key, None)
json.dump(items, open(db, "w"))
"""


def browser(fake, pages, *, deny=False, wrong_state=False, seen=None):
    """Stands in for the person's browser on Notion's consent screen."""
    def open_url(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if seen is not None:
            seen.append(q)
        redirect, state = q["redirect_uri"], q["state"]
        if deny:
            target = "%s?error=access_denied&state=%s" % (redirect, state)
        else:
            code = fake.issue_code(pages, redirect)
            target = "%s?code=%s&state=%s" % (redirect, code, "forged" if wrong_state else state)

        def go():
            time.sleep(0.05)
            try:
                urlopen(target, timeout=5).read()
            except OSError:
                pass

        threading.Thread(target=go, daemon=True).start()
    return open_url


def edit_settings(p, **changes):
    data = read_json(p.settings)
    data.update(changes)
    with open(p.settings, "w") as f:
        json.dump(data, f, indent=2)


class OAuthTest(TempCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNotion().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.creds = os.path.join(self.root, "keychain")
        env = mock.patch.dict(os.environ, {"CLAUDE_SYNC_NOTION_API": self.fake.base,
                                           "CLAUDE_SYNC_CREDENTIAL_HELPER": "store --file=%s" % self.creds,
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.page = self.fake.add_page("Claude Sync 동기화")

    def login(self, p, timeout=10, **kw):
        return notion_store.login(p, CLIENT_ID, CLIENT_SECRET, open_url=browser(self.fake, [self.page], **kw),
                                  port=0, timeout=timeout)

    def keychain(self):
        with open(self.creds, encoding="utf-8") as f:
            return f.read()

    def first_pc(self):
        a = Paths(make_home(self.root, "a", skills=["brand"]))
        self.login(a)
        out = init(a, pc="pc-a", skills=["brand"], mods=[], keys=["theme", "effortLevel"], claude_md="move", now=NOW,
                   backend="notion", page=self.page, oauth=True)
        self.assertEqual(out["result"], "ok", out)
        return a

    def test_login_returns_the_chosen_pages_and_keeps_tokens_in_the_keychain(self):
        a = Paths(make_home(self.root, "a"))
        seen = []
        out = notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=browser(self.fake, [self.page], seen=seen),
                                 port=0, timeout=10)
        self.assertEqual(out["pages"], [{"id": self.page, "title": "Claude Sync 동기화"}])
        self.assertEqual(out["workspace"], "내 워크스페이스")
        self.assertEqual((seen[0]["client_id"], seen[0]["response_type"], seen[0]["owner"]), (CLIENT_ID, "code", "user"))
        self.assertTrue(seen[0]["redirect_uri"].startswith("http://localhost:"))
        self.assertGreaterEqual(len(seen[0]["state"]), 20)
        self.assertEqual(self.fake.token_requests[0]["grant_type"], "authorization_code")
        stored = self.keychain()
        for secret in (CLIENT_SECRET, "nrt_", "ntn_oauth_"):
            self.assertIn(secret, stored)
        text = json.dumps(out)
        self.assertNotIn(CLIENT_SECRET, text)
        self.assertNotIn("ntn_oauth_", text)
        self.assertNotIn("nrt_", text)

    def test_login_says_so_when_the_keychain_does_not_keep_the_token(self):
        a = Paths(make_home(self.root, "a"))
        # 'store' answers without keeping anything, as when the keychain cannot be reached.
        with mock.patch.dict(os.environ, {"CLAUDE_SYNC_CREDENTIAL_HELPER": "!f() { cat >/dev/null; }; f"}):
            with self.assertRaises(NotionError) as cm:
                self.login(a)
        self.assertIn("키체인", str(cm.exception))
        self.assertNotIn("ntn_oauth_", str(cm.exception))

    def old_keychain(self):
        """git's keychain helper before 2.45 (Apple's git up to Xcode 16): 'store' adds an item and leaves
        one already there as it was."""
        script = os.path.join(self.root, "old-keychain.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(OLD_KEYCHAIN)
        return "!%s %s %s" % (sys.executable, script, os.path.join(self.root, "old-keychain.json"))

    def test_signing_in_again_replaces_what_an_old_keychain_helper_keeps(self):
        a = Paths(make_home(self.root, "a"))
        with mock.patch.dict(os.environ, {"CLAUDE_SYNC_CREDENTIAL_HELPER": self.old_keychain()}):
            self.login(a)
            first = notion_store.load_secret(a, notion_store.PENDING_REFRESH)
            self.login(a)  # every item of the first sign-in is still there
            self.assertNotEqual(notion_store.load_secret(a, notion_store.PENDING_REFRESH), first)

    def test_a_refresh_is_kept_by_an_old_keychain_helper(self):
        with mock.patch.dict(os.environ, {"CLAUDE_SYNC_CREDENTIAL_HELPER": self.old_keychain()}):
            a = self.first_pc()
            before = notion_store.load_secret(a, self.page)
            self.fake.expire(before)
            s = run_sync(a, auto=False, now=NOW + 1)
            self.assertEqual(s.last_result, "ok", s.last_error)
            self.assertNotEqual(notion_store.load_secret(a, self.page), before)
            self.assertEqual(run_sync(a, auto=False, now=NOW + 2).last_result, "ok")

    def test_page_list_leaves_out_the_sync_rows(self):
        self.first_pc()
        out = self.login(Paths(make_home(self.root, "b")))
        self.assertEqual(out["pages"], [{"id": self.page, "title": "Claude Sync 동기화"}])

    def test_signed_in_git_pc_moves_to_notion(self):
        a = Paths(make_home(self.root, "a"))
        out = init(a, remote=make_remote(self.root), pc="pc-a", skills=[], mods=[], keys=["theme"], claude_md="move",
                   now=NOW)
        self.assertEqual(out["result"], "ok", out)
        self.login(a)
        code, raw, out = cli_call(a.home, "set-remote", "--backend", "notion", "--page", self.page, "--oauth")
        self.assertEqual(code, 0, out)
        self.assertNotIn("ntn_oauth_", raw)
        s = load_state(a)
        self.assertEqual((s.backend, s.notion_auth), ("notion", "oauth"))
        self.assertEqual(run_sync(a, auto=False, now=NOW + 1).last_result, "ok")
        (ds,) = self.fake.data_sources.values()
        self.assertEqual(len(ds["rows"]), 1)

    def test_a_reconnect_that_fails_halfway_keeps_the_working_sign_in(self):
        a = self.first_pc()
        keys = (self.page, notion_store.OAUTH_CLIENT, notion_store.OAUTH_REFRESH)
        before = {k: notion_store.load_secret(a, k) for k in keys}
        self.login(a)  # a new sign-in waits for its page
        real = notion_store.save_secret

        def refresh_not_kept(p, key, value):
            if key == notion_store.OAUTH_REFRESH and value != before[key]:
                raise NotionError(0, "keychain", "Notion 비밀 키를 키체인에 저장하지 못했어요")
            real(p, key, value)

        with mock.patch.object(notion_store, "save_secret", refresh_not_kept):
            with self.assertRaises(ActionError):
                use_notion(a, self.page, None, oauth=True)
        self.assertEqual({k: notion_store.load_secret(a, k) for k in keys}, before)
        self.assertEqual(run_sync(a, auto=False, now=NOW + 1).last_result, "ok")

    def test_a_keychain_that_stops_answering_mid_reconnect_keeps_the_working_sign_in(self):
        a = self.first_pc()
        keys = (self.page, notion_store.OAUTH_CLIENT, notion_store.OAUTH_REFRESH)
        before = {k: notion_store.load_secret(a, k) for k in keys}
        self.login(a)
        real = subprocess.run

        def stalls_on_the_new_refresh(argv, *args, **kw):
            data = kw.get("input") or ""
            if "path=oauth/refresh\n" in data and "password=" in data and before[notion_store.OAUTH_REFRESH] not in data:
                raise subprocess.TimeoutExpired(argv, 60)
            return real(argv, *args, **kw)

        with mock.patch.object(notion_store.subprocess, "run", stalls_on_the_new_refresh):
            with self.assertRaises(ActionError):
                use_notion(a, self.page, None, oauth=True)
        self.assertEqual({k: notion_store.load_secret(a, k) for k in keys}, before)

    def test_a_keychain_that_cannot_be_read_before_a_reconnect_changes_nothing(self):
        a = self.first_pc()
        keys = (self.page, notion_store.OAUTH_CLIENT, notion_store.OAUTH_REFRESH)
        before = {k: notion_store.load_secret(a, k) for k in keys}
        self.login(a)
        real = subprocess.run

        def page_read_stalls(argv, *args, **kw):
            if argv[-1] == "fill" and "path=%s\n" % self.page in (kw.get("input") or ""):
                raise subprocess.TimeoutExpired(argv, 60)
            return real(argv, *args, **kw)

        def new_refresh_not_kept(argv, *args, **kw):
            data = kw.get("input") or ""
            if argv[-1] == "approve" and "path=oauth/refresh\n" in data and before[notion_store.OAUTH_REFRESH] not in data:
                return subprocess.CompletedProcess(argv, 1, "", "")
            return page_read_stalls(argv, *args, **kw)

        with mock.patch.object(notion_store.subprocess, "run", new_refresh_not_kept):
            with self.assertRaises(ActionError) as cm:
                use_notion(a, self.page, None, oauth=True)
        self.assertIn("키체인", str(cm.exception))
        self.assertEqual({k: notion_store.load_secret(a, k) for k in keys}, before)

    def _holder(self, *argv_tail):
        """A process that listens on a free port, its command line ending in `argv_tail`."""
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code = ("import socket, time; s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                "s.bind(('127.0.0.1', %d)); s.listen(); print('ready', flush=True); time.sleep(60)" % port)
        p = subprocess.Popen([sys.executable, "-c", code, *argv_tail], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), "ready")
        return p, port

    def test_a_new_sign_in_takes_over_from_one_still_waiting(self):
        a = Paths(make_home(self.root, "a"))
        old, port = self._holder("main.py", "notion-login")  # an earlier 'Notion으로 연결' still waiting
        os.makedirs(a.sync_dir, exist_ok=True)
        with open(notion_store.sign_in_record(a), "w") as f:  # what that sign-in wrote when it took the port
            json.dump({"pid": old.pid, "port": port, "started": notion_store.process_started(old.pid)}, f)
        out = notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=browser(self.fake, [self.page]),
                                 port=port, timeout=10)
        self.assertEqual(out["pages"], [{"id": self.page, "title": "Claude Sync 동기화"}])
        self.assertIsNotNone(old.wait(timeout=5), "the waiting sign-in was ended")

    def test_a_left_over_record_whose_number_another_program_now_has_stops_nothing(self):
        a = Paths(make_home(self.root, "a"))
        other, port = self._holder("some-server")
        os.makedirs(a.sync_dir, exist_ok=True)
        with open(notion_store.sign_in_record(a), "w") as f:  # an old sign-in's record; the number was reused
            json.dump({"pid": other.pid, "port": port, "started": "Thu Jan  1 09:00:00 2026"}, f)
        with self.assertRaises(NotionError):
            notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=lambda url: None, port=port, timeout=1)
        self.assertIsNone(other.poll())

    def test_a_program_that_only_looks_like_a_sign_in_is_not_stopped(self):
        a = Paths(make_home(self.root, "a"))
        other, port = self._holder("/tmp/unrelated/main.py", "--label", "notion-login")
        with self.assertRaises(NotionError):
            notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=lambda url: None, port=port, timeout=1)
        self.assertIsNone(other.poll(), "only a sign-in this engine recorded is ended")

    def test_a_port_held_by_another_program_says_which_and_what_to_do(self):
        a = Paths(make_home(self.root, "a"))
        other, port = self._holder("some-server")
        with self.assertRaises(NotionError) as cm:
            notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=lambda url: None, port=port, timeout=1)
        text = str(cm.exception)
        self.assertIn(str(port), text)
        self.assertIn("Python", text)  # the program's name
        self.assertIn("비밀 키로 연결하기", text)
        self.assertIsNone(other.poll(), "another program is never stopped")

    def test_cancel_forged_state_and_no_answer_fail_readably(self):
        a = Paths(make_home(self.root, "a"))
        with self.assertRaises(NotionError) as cm:
            self.login(a, deny=True)
        self.assertIn("취소", str(cm.exception))
        with self.assertRaises(NotionError) as cm:
            self.login(a, wrong_state=True)
        self.assertIn("확인하지 못했어요", str(cm.exception))
        self.assertEqual(self.fake.token_requests, [], "no code was exchanged")
        with self.assertRaises(NotionError) as cm:
            notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=lambda url: None, port=0, timeout=1)
        self.assertIn("시간", str(cm.exception))

    def test_first_and_second_pc_connect_with_notion_login(self):
        a = self.first_pc()
        self.assertEqual(load_state(a).notion_auth, "oauth")
        b = Paths(make_home(self.root, "b"))
        self.login(b)
        out = join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)
        self.assertEqual(out["result"], "ok", out)
        self.assertEqual(head(b), head(a))
        edit_settings(b, effortLevel="high")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        self.assertEqual(run_sync(a, auto=False, now=NOW + 2).last_result, "ok")
        self.assertEqual(read_json(a.settings)["effortLevel"], "high")

    def test_expired_access_token_is_refreshed_once_and_the_new_pair_kept(self):
        a = self.first_pc()
        before = notion_store.load_secret(a, self.page)
        refresh_before = [l for l in self.keychain().splitlines() if "nrt_" in l]
        self.fake.expire(before)
        edit_settings(a, theme="light")
        s = run_sync(a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(self.fake.token_requests[-1]["grant_type"], "refresh_token")
        after = notion_store.load_secret(a, self.page)
        self.assertNotEqual(after, before)
        self.assertNotEqual([l for l in self.keychain().splitlines() if "nrt_" in l], refresh_before)
        self.assertEqual(run_sync(a, auto=False, now=NOW + 2).last_result, "ok")
        self.assertEqual(len([r for r in self.fake.token_requests if r["grant_type"] == "refresh_token"]), 1)

    def test_a_refresh_that_fails_asks_to_connect_again(self):
        a = self.first_pc()
        self.fake.expire(notion_store.load_secret(a, self.page))
        self.fake.refresh_tokens.clear()
        s = run_sync(a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("연결이 끊겼어요", s.last_error)
        self.assertIn("Notion으로 연결", s.last_error)

    def test_join_with_login_but_no_login_yet_is_refused(self):
        b = Paths(make_home(self.root, "b"))
        with self.assertRaises(SetupError) as cm:
            join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)
        self.assertIn("Notion으로 연결", str(cm.exception))

    def test_tokens_stay_out_of_files_and_output(self):
        a = self.first_pc()
        run_sync(a, auto=False, now=NOW + 1)
        for needle in (CLIENT_SECRET, "nrt_", "ntn_oauth_"):
            left = subprocess.run(["grep", "-rl", needle, a.home], capture_output=True, text=True).stdout
            self.assertEqual(left, "", needle)

    def test_unknown_oauth_error_codes_are_not_echoed(self):
        from claude_sync import notion
        # The token endpoint answers with an error field that carries the client secret.
        with mock.patch("claude_sync.notion.urlopen", side_effect=notion.HTTPError(
                self.fake.base + "/v1/oauth/token", 400, "bad", {},
                io.BytesIO(json.dumps({"error": "weird " + CLIENT_SECRET}).encode()))):
            with self.assertRaises(NotionError) as cm:
                notion.oauth_token(CLIENT_ID, CLIENT_SECRET, {"grant_type": "refresh_token", "refresh_token": "x"})
        self.assertNotIn(CLIENT_SECRET, str(cm.exception))
        self.assertNotIn("weird", str(cm.exception))

    def test_a_stalled_local_connection_does_not_hold_the_login(self):
        import socket
        a = Paths(make_home(self.root, "a"))
        seen = []
        real = browser(self.fake, [self.page], seen=seen)

        def stall_then_answer(url):
            port = urlparse(parse_qs(urlparse(url).query)["redirect_uri"][0]).port
            stuck = socket.create_connection(("127.0.0.1", port))
            stuck.sendall(b"GET /callback?co")  # never finishes the request line
            self.addCleanup(stuck.close)
            real(url)

        started = time.monotonic()
        out = notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=stall_then_answer, port=0, timeout=30)
        self.assertEqual(out["pages"][0]["id"], self.page)
        self.assertLess(time.monotonic() - started, 15)

    def test_signing_in_again_keeps_the_working_pair_until_a_page_is_connected(self):
        a = self.first_pc()
        other = self.fake.add_page("다른 워크스페이스")
        notion_store.login(a, CLIENT_ID, CLIENT_SECRET, open_url=browser(self.fake, [other]), port=0, timeout=10)
        # Not connected to the other page: the old page's token pair must still be the one in use.
        self.fake.expire(notion_store.load_secret(a, self.page))
        edit_settings(a, theme="light")
        s = run_sync(a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        used = self.fake.token_requests[-1]
        self.assertEqual(used["grant_type"], "refresh_token")
        self.assertEqual(self.fake.grants[notion_store.load_secret(a, self.page)], [self.page])

    def test_first_download_of_a_join_waits_for_the_sync_lock(self):
        from claude_sync import actions
        from claude_sync.lock import Lock
        self.first_pc()
        b = Paths(make_home(self.root, "b"))
        self.login(b)
        held = Lock(b)
        self.assertTrue(held.acquire(wait=False))
        try:
            with mock.patch.object(actions, "LOCK_WAIT_SECONDS", 0.5):
                with self.assertRaises(SetupError) as cm:
                    join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)
            self.assertIn("진행 중", str(cm.exception))
            self.assertEqual([r for r in self.fake.requests if r.startswith("POST /v1/data_sources")
                              and "pc-b" in r], [])
        finally:
            held.release()
        self.assertEqual(join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)["result"], "ok")

    def test_joining_again_swaps_the_sign_in_under_the_sync_lock(self):
        from claude_sync import actions
        from claude_sync.lock import Lock
        self.first_pc()
        b = Paths(make_home(self.root, "b"))
        self.login(b)
        self.assertEqual(join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)["result"], "ok")
        before = notion_store.load_secret(b, self.page)
        self.login(b)
        held = Lock(b)
        self.assertTrue(held.acquire(wait=False))
        try:
            with mock.patch.object(actions, "LOCK_WAIT_SECONDS", 0.5):
                with self.assertRaises(SetupError) as cm:
                    join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)
            self.assertIn("진행 중", str(cm.exception))
            self.assertEqual(notion_store.load_secret(b, self.page), before, "nothing swapped while locked")
        finally:
            held.release()
        self.assertEqual(join(b, backend="notion", page=self.page, oauth=True, pc="pc-b", now=NOW)["result"], "ok")
        self.assertNotEqual(notion_store.load_secret(b, self.page), before)

    def broker(self):
        return self.fake.base + "/broker"

    def test_signing_in_through_the_broker_never_needs_the_secret(self):
        a = Paths(make_home(self.root, "a"))
        out = notion_store.login(a, CLIENT_ID, broker=self.broker(), open_url=browser(self.fake, [self.page]),
                                 port=0, timeout=10)
        self.assertEqual(out["pages"], [{"id": self.page, "title": "Claude Sync 동기화"}])
        (sent,) = self.fake.broker_requests
        self.assertIsNone(sent["authorization"], "the app sends no client secret")
        self.assertEqual(sent["user_agent"], "claude-sync/" + claude_sync.__version__, "Cloudflare refuses Python-urllib")
        self.assertEqual(sent["body"]["grant_type"], "authorization_code")
        self.assertNotIn(CLIENT_SECRET, self.keychain())
        self.assertIn(self.broker(), unquote(self.keychain()), "the broker is what refreshes go to")

    def test_a_broker_sign_in_refreshes_through_the_broker(self):
        a = Paths(make_home(self.root, "a", skills=["brand"]))
        notion_store.login(a, CLIENT_ID, broker=self.broker(), open_url=browser(self.fake, [self.page]), port=0, timeout=10)
        out = init(a, pc="pc-a", skills=["brand"], mods=[], keys=["theme"], claude_md="move", now=NOW,
                   backend="notion", page=self.page, oauth=True)
        self.assertEqual(out["result"], "ok", out)
        self.fake.expire(notion_store.load_secret(a, self.page))
        edit_settings(a, theme="light")
        s = run_sync(a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(self.fake.broker_requests[-1]["body"]["grant_type"], "refresh_token")
        self.assertIsNone(self.fake.broker_requests[-1]["authorization"])
        self.assertNotIn(CLIENT_SECRET, self.keychain())

    def test_only_an_https_broker_is_trusted(self):
        a = Paths(make_home(self.root, "a"))
        for broker in ("http://broker.example", "ftp://x", "https://", "file:///tmp/x"):
            with self.assertRaises(notion_store.NotionError, msg=broker):
                notion_store.login(a, CLIENT_ID, broker=broker, open_url=lambda url: None, port=0, timeout=1)
        self.assertEqual(self.fake.broker_requests, [])

    def test_cli_login_takes_a_broker_on_stdin(self):
        a = Paths(make_home(self.root, "a"))
        captured = {}

        def fake_login(p, client_id, client_secret="", **kw):
            captured.update(client_id=client_id, client_secret=client_secret, **kw)
            return {"pages": [], "workspace": "w"}

        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"client_id": CLIENT_ID, "broker": "https://broker.example"}) + "\n")
        with mock.patch.object(notion_store, "login", side_effect=fake_login), mock.patch("sys.stdin", stdin), \
                redirect_stdout(out):
            code = cli.main(["--home", a.home, "notion-login"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual((captured["client_id"], captured["client_secret"], captured["broker"]),
                         (CLIENT_ID, "", "https://broker.example"))

    def test_cli_login_takes_the_client_on_stdin(self):
        a = Paths(make_home(self.root, "a"))
        captured = {}

        def fake_login(p, client_id, client_secret, **kw):
            captured.update(client_id=client_id, client_secret=client_secret, **kw)
            return {"pages": [{"id": self.page, "title": "x"}], "workspace": "w"}

        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}) + "\n")
        with mock.patch.object(notion_store, "login", side_effect=fake_login), mock.patch("sys.stdin", stdin), \
                redirect_stdout(out):
            code = cli.main(["--home", a.home, "notion-login"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["pages"][0]["id"], self.page)
        self.assertEqual((captured["client_id"], captured["client_secret"]), (CLIENT_ID, CLIENT_SECRET))
        self.assertEqual(captured["port"], notion_store.OAUTH_PORT)
        self.assertNotIn(CLIENT_SECRET, out.getvalue())
