from __future__ import annotations

import io
import json
import os
import subprocess
from contextlib import redirect_stdout
from unittest import mock

from helpers import TempCase, make_home, make_remote, read_json, write
from fake_notion import TOKEN, FakeNotion

from claude_sync import cli
from claude_sync.gitio import git, head
from claude_sync.paths import Paths
from claude_sync.setup import init
from claude_sync.state import load_state
from claude_sync.sync import run_sync

GL_TOKEN = "glpat-CLI-secret-5521"
DEPLOY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "deploy.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def call(home, *argv, stdin=""):
    out = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
        code = cli.main(["--home", home, *argv])
    return code, out.getvalue(), json.loads(out.getvalue())


class BackendCliTest(TempCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNotion().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.log = os.path.join(self.root, "helper.log")
        self.creds = os.path.join(self.root, "keychain")
        env = mock.patch.dict(os.environ, {"CLAUDE_SYNC_NOTION_API": self.fake.base,
                                           "CLAUDE_SYNC_CREDENTIAL_HELPER": "store --file=%s" % self.creds,
                                           "CLAUDE_SYNC_TEST_HTTPS_LIKE": "1", "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.page = self.fake.add_page()

    def test_notion_init_then_join_receive_only(self):
        a = make_home(self.root, "a", skills=["brand"])
        code, raw, out = call(a, "init", "--backend", "notion", "--page", self.page, "--pc", "pc-a",
                              "--skills", "brand", "--keys", "theme", stdin=TOKEN + "\n")
        self.assertEqual((code, out["result"]), (0, "ok"), out)
        self.assertNotIn(TOKEN, raw)
        b = make_home(self.root, "b")
        code, raw, out = call(b, "join", "--backend", "notion", "--page", "https://www.notion.so/x-%s" % self.page,
                              "--pc", "pc-b", "--words", "corp", "--receive-only", stdin=TOKEN + "\n")
        self.assertEqual((code, out["result"]), (0, "ok"), out)
        self.assertNotIn(TOKEN, raw)
        self.assertIn("missing_plugins", out)
        self.assertTrue(load_state(Paths(b)).receive_only)
        code, _, status = call(b, "status")
        self.assertEqual(status["backend"], "notion")
        self.assertTrue(os.path.islink(os.path.join(Paths(b).skills, "brand")))

    def test_join_on_an_empty_page_is_an_error(self):
        b = make_home(self.root, "b")
        code, _, out = call(b, "join", "--backend", "notion", "--page", self.page, "--pc", "pc-b", stdin=TOKEN + "\n")
        self.assertEqual(code, 1)
        self.assertIn("아직 첫 PC", out["error"])
        self.assertFalse(os.path.exists(Paths(b).shared))

    def test_secret_missing_on_stdin_is_refused(self):
        b = make_home(self.root, "b")
        code, _, out = call(b, "join", "--backend", "notion", "--page", self.page, "--pc", "pc-b", stdin="")
        self.assertEqual(code, 1)
        self.assertIn("표준 입력", out["error"])

    def test_malformed_secret_never_reaches_the_output(self):
        a = make_home(self.root, "a")
        bad = "ntn_example_secret\rbroken"
        code, raw, out = call(a, "init", "--backend", "notion", "--page", self.page, "--pc", "pc-a",
                              stdin=bad + "\n")
        self.assertEqual(code, 1)
        self.assertNotIn("ntn_example_secret", raw)
        self.assertIn("비밀 키", out["error"])

    def test_git_pc_switches_to_notion_and_back(self):
        remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a"))
        init(a, remote=remote, pc="pc-a", skills=[], mods=[], keys=["theme"], claude_md="move", now=1.0)
        code, raw, out = call(a.home, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual((code, out.get("ok")), (0, True), out)
        self.assertNotIn(TOKEN, raw)
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        (ds,) = self.fake.data_sources.values()
        self.assertEqual(len(ds["rows"]), 1, "the first Notion upload is a full row")
        b = make_home(self.root, "b")
        code, _, out = call(b, "join", "--backend", "notion", "--page", self.page, "--pc", "pc-b", stdin=TOKEN + "\n")
        self.assertEqual(out["result"], "ok", out)
        self.assertEqual(head(Paths(b)), head(a))
        code, _, out = call(a.home, "set-remote", "file://%s" % remote, "--username", "pc-a", stdin=GL_TOKEN + "\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(load_state(a).backend, "git")
        edit = read_json(a.settings)
        edit["theme"] = "light"
        with open(a.settings, "w") as f:
            json.dump(edit, f)
        self.assertEqual(run_sync(a, auto=False, now=3.0).last_result, "ok")
        self.assertEqual(git(a, "rev-parse", "origin/main").stdout.strip(), head(a))

    def git_first_pc(self, name="a", remote=None):
        remote = remote or make_remote(self.root)
        home = make_home(self.root, name)
        code, _, out = call(home, "init", "--remote", "file://%s" % remote, "--username", "pc-" + name,
                            "--pc", "pc-" + name, "--keys", "theme", "--token-stdin", stdin=GL_TOKEN + "\n")
        self.assertEqual(out["result"], "ok", out)
        return Paths(home), remote

    def notion_rows(self):
        return [r for ds in self.fake.data_sources.values() for r in ds["rows"]]

    def test_status_names_the_storage_without_secrets(self):
        a, remote = self.git_first_pc()
        self.assertEqual(call(a.home, "status")[2]["storage"],
                         {"backend": "git", "remote": "file://%s" % remote, "username": ""})
        git(a, "config", "remote.origin.url", "https://pc-a@gitlab.com/me/claude-config.git")
        self.assertEqual(call(a.home, "status")[2]["storage"],
                         {"backend": "git", "remote": "https://gitlab.com/me/claude-config.git", "username": "pc-a"})
        code, _, out = call(a.home, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual(code, 0, out)
        _, raw, out = call(a.home, "status")
        self.assertEqual(out["storage"], {"backend": "notion", "page": self.page, "auth": "secret",
                                          "url": "https://www.notion.so/%s" % self.page})
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(GL_TOKEN, raw)

    def test_moving_to_a_notion_page_with_another_history_is_refused_and_nothing_changes(self):
        other = Paths(make_home(self.root, "other"))
        out = init(other, pc="pc-x", skills=[], mods=[], keys=["theme"], claude_md="move", now=1.0,
                   backend="notion", page=self.page, secret=TOKEN)
        self.assertEqual(out["result"], "ok", out)
        a, _ = self.git_first_pc()
        before = (head(a), git(a, "config", "--get", "remote.origin.url").stdout, git(a, "rev-parse", "origin/main").stdout)
        code, _, out = call(a.home, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual(code, 1, out)
        self.assertIn("이어지지", out["error"])
        self.assertEqual(load_state(a).backend, "git")
        after = (head(a), git(a, "config", "--get", "remote.origin.url").stdout, git(a, "rev-parse", "origin/main").stdout)
        self.assertEqual(after, before)
        self.assertEqual(git(a, "for-each-ref", "refs/notion/").stdout, "", "nothing of the other history is kept")
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        self.assertEqual(len(self.notion_rows()), 1, "the page still holds only the other group's row")

    def test_receive_only_pc_does_not_create_a_notion_database_when_moving(self):
        a, _ = self.git_first_pc()
        call(a.home, "mode", "receive-only")
        code, _, out = call(a.home, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual(code, 1, out)
        self.assertIn("올리는 PC", out["error"])
        self.assertEqual(self.fake.databases, {})
        self.assertEqual(load_state(a).backend, "git")

    def test_receive_only_pc_follows_the_sending_pc_to_notion(self):
        a, remote = self.git_first_pc()
        b = make_home(self.root, "b")
        code, _, out = call(b, "join", "--remote", "file://%s" % remote, "--username", "pc-b", "--pc", "pc-b",
                            "--receive-only", stdin=GL_TOKEN + "\n")
        self.assertEqual(out["result"], "ok", out)
        self.assertEqual(call(a.home, "set-remote", "--backend", "notion", "--page", self.page,
                              stdin=TOKEN + "\n")[0], 0)
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        edit = read_json(a.settings)
        edit["theme"] = "light"
        with open(a.settings, "w") as f:
            json.dump(edit, f)
        self.assertEqual(run_sync(a, auto=False, now=3.0).last_result, "ok")
        rows = len(self.notion_rows())
        code, _, out = call(b, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(run_sync(Paths(b), auto=False, now=4.0).last_result, "ok")
        self.assertEqual(read_json(Paths(b).settings)["theme"], "light")
        self.assertEqual(len(self.notion_rows()), rows, "a receive-only PC adds no rows")

    def test_moving_to_a_git_remote_with_another_history_is_refused_and_nothing_changes(self):
        a, remote = self.git_first_pc("a")
        other_remote = make_remote(os.path.join(self.root, "other"))
        x, _ = self.git_first_pc("x", remote=other_remote)
        before = (git(a, "config", "--get", "remote.origin.url").stdout, git(a, "rev-parse", "origin/main").stdout)
        code, _, out = call(a.home, "set-remote", "file://%s" % other_remote, "--username", "pc-a",
                            stdin=GL_TOKEN + "\n")
        self.assertEqual(code, 1, out)
        self.assertIn("이어지지", out["error"])
        self.assertEqual((git(a, "config", "--get", "remote.origin.url").stdout,
                          git(a, "rev-parse", "origin/main").stdout), before)
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        tip = subprocess.run(["git", "--git-dir", other_remote, "rev-parse", "main"], capture_output=True, text=True)
        self.assertEqual(tip.stdout.strip(), head(x))

    def test_moving_to_a_new_git_project_that_only_has_a_readme(self):
        a, _ = self.git_first_pc()
        fresh = make_remote(os.path.join(self.root, "fresh"))
        seed = os.path.join(self.root, "seed")
        subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
        write(os.path.join(seed, "README.md"), "# claude-config\n")
        for argv in (["add", "README.md"], ["-c", "user.name=GitLab", "-c", "user.email=gl@example.com", "commit", "-qm",
                                            "Initial commit"], ["push", "-q", fresh, "HEAD:main"]):
            subprocess.run(["git", "-C", seed] + argv, check=True, capture_output=True)
        code, _, out = call(a.home, "set-remote", "file://%s" % fresh, "--username", "pc-a", stdin=GL_TOKEN + "\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        tip = subprocess.run(["git", "--git-dir", fresh, "rev-parse", "main"], capture_output=True, text=True)
        self.assertEqual(tip.stdout.strip(), head(a))

    def test_reconnecting_to_the_same_notion_page_is_not_refused(self):
        a = make_home(self.root, "a")
        call(a, "init", "--backend", "notion", "--page", self.page, "--pc", "pc-a", "--keys", "theme",
             stdin=TOKEN + "\n")
        rows = len(self.notion_rows())
        code, _, out = call(a, "set-remote", "--backend", "notion", "--page", self.page, stdin=TOKEN + "\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(run_sync(Paths(a), auto=False, now=5.0).last_result, "ok")
        self.assertEqual(len(self.notion_rows()), rows, "nothing new to send")

    def test_a_failed_reconnect_keeps_the_old_token(self):
        a, remote = self.git_first_pc()
        self.assertIn(GL_TOKEN, _read(self.creds))
        os.rename(remote, remote + ".away")  # unreachable, as when the new token is refused
        code, _, out = call(a.home, "set-remote", "file://%s" % remote, "--username", "pc-a",
                            stdin="glpat-mistyped-0000\n")
        os.rename(remote + ".away", remote)
        self.assertEqual(code, 1, out)
        self.assertIn(GL_TOKEN, _read(self.creds))
        self.assertNotIn("glpat-mistyped-0000", _read(self.creds))
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")

    def test_git_join_and_init_with_token_on_stdin(self):
        remote = make_remote(self.root)
        a = make_home(self.root, "a")
        code, raw, out = call(a, "init", "--remote", "file://%s" % remote, "--username", "pc-a", "--pc", "pc-a",
                              "--keys", "theme", "--token-stdin", stdin=GL_TOKEN + "\n")
        self.assertEqual((code, out["result"]), (0, "ok"), out)
        self.assertNotIn(GL_TOKEN, raw)
        self.assertIn(GL_TOKEN, _read(self.creds))
        b = make_home(self.root, "b")
        code, raw, out = call(b, "join", "--remote", "file://%s" % remote, "--username", "pc-b", "--pc", "pc-b",
                              stdin=GL_TOKEN + "\n")
        self.assertEqual((code, out["result"]), (0, "ok"), out)
        self.assertEqual(head(Paths(b)), head(Paths(a)))
        self.assertNotIn(GL_TOKEN, _read(os.path.join(Paths(b).shared, ".git", "config")))

    def test_first_pc_words_apply_before_the_first_upload(self):
        remote = make_remote(self.root)
        a = make_home(self.root, "a", claude_md="# 지침\ncorp 내부 주소\n")
        code, _, out = call(a, "init", "--remote", "file://%s" % remote, "--pc", "pc-a", "--keys", "theme",
                            "--words", "corp, internal-host")
        self.assertEqual(out["result"], "blocked", out)
        with open(Paths(a).blocklist_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "corp\ninternal-host\n")
        pushed = subprocess.run(["git", "--git-dir", remote, "rev-parse", "--verify", "-q", "main"],
                                capture_output=True, text=True)
        self.assertNotEqual(pushed.returncode, 0, "nothing reached the remote")

    def test_first_pc_from_a_packed_engine_shares_its_own_mod(self):
        remote = make_remote(self.root)
        packed = os.path.join(self.root, "App", "engine")
        subprocess.run(["bash", DEPLOY], check=True, capture_output=True, env=dict(os.environ, DEPLOY_TARGET=packed))
        a = Paths(make_home(self.root, "a"))
        r = subprocess.run([os.path.join(packed, "bin", "claude-sync"), "--home", a.home, "init", "--remote",
                            "file://%s" % remote, "--pc", "pc-a", "--keys", "theme"],
                           capture_output=True, text=True, env=dict(os.environ, HOME=a.home), timeout=120)
        self.assertEqual(json.loads(r.stdout)["result"], "ok", r.stdout + r.stderr)
        mod = os.path.join(a.shared, "mods", "config-sync")
        self.assertTrue(os.access(os.path.join(mod, "bin", "claude-sync"), os.X_OK))
        self.assertTrue(os.path.isfile(os.path.join(mod, "engine", "claude_sync", "sync.py")))
        self.assertTrue(os.path.islink(os.path.join(a.mods, "config-sync")))
        self.assertEqual(read_json(a.settings)["env"]["CLAUDE_CODE_PLUGIN_DIRS"].split(":")[0],
                         os.path.join(a.mods, "config-sync"))
        tree = subprocess.run(["git", "--git-dir", remote, "ls-tree", "-r", "--name-only", "main"],
                              capture_output=True, text=True).stdout
        self.assertIn("mods/config-sync/bin/claude-sync", tree)
        self.assertNotIn("__pycache__", tree)

    def test_join_again_on_the_same_notion_page_refreshes_the_secret_and_reapplies(self):
        a = make_home(self.root, "a", skills=["brand"])
        call(a, "init", "--backend", "notion", "--page", self.page, "--pc", "pc-a", "--skills", "brand",
             "--keys", "theme", stdin=TOKEN + "\n")
        b = make_home(self.root, "b")
        args = ("join", "--backend", "notion", "--page", self.page, "--pc", "pc-b")
        self.assertEqual(call(b, *args, stdin=TOKEN + "\n")[2]["result"], "ok")
        os.remove(self.creds)  # as if the keychain item were lost
        code, raw, out = call(b, *args, stdin=TOKEN + "\n")
        self.assertEqual((code, out.get("result")), (0, "ok"), out)
        self.assertIn(TOKEN, _read(self.creds))
        other = self.fake.add_page()
        code, _, out = call(b, "join", "--backend", "notion", "--page", other, "--pc", "pc-b", stdin=TOKEN + "\n")
        self.assertEqual(code, 1)
        self.assertIn("다른 저장소", out["error"])
        self.assertEqual(self.fake.pages[other]["children"], [])

    def test_join_again_on_the_same_git_remote_and_after_a_half_done_join(self):
        remote = make_remote(self.root)
        a = make_home(self.root, "a")
        call(a, "init", "--remote", "file://%s" % remote, "--pc", "pc-a", "--keys", "theme")
        b = make_home(self.root, "b")
        os.makedirs(Paths(b).shared)
        subprocess.run(["git", "init", "-q", "-b", "main", Paths(b).shared], check=True)  # stopped before receiving
        args = ("join", "--remote", "file://%s" % remote, "--username", "pc-b", "--pc", "pc-b")
        code, _, out = call(b, *args, stdin=GL_TOKEN + "\n")
        self.assertEqual((code, out.get("result")), (0, "ok"), out)
        code, _, out = call(b, *args, stdin=GL_TOKEN + "\n")
        self.assertEqual((code, out.get("result")), (0, "ok"), out)
        self.assertEqual(head(Paths(b)), head(Paths(a)))
        other = make_remote(os.path.join(self.root, "other"))
        code, _, out = call(b, "join", "--remote", "file://%s" % other, "--username", "pc-b", "--pc", "pc-b",
                            stdin=GL_TOKEN + "\n")
        self.assertEqual(code, 1)
        self.assertIn("다른 저장소", out["error"])

    def test_candidates_lists_local_items(self):
        a = make_home(self.root, "a", skills=["brand", "design"], mods=["usage-band"])
        os.symlink(os.path.join(self.root, "elsewhere"), os.path.join(Paths(a).skills, "linked"))
        code, _, out = call(a, "candidates")
        self.assertEqual(code, 0)
        self.assertEqual(out["skills"], ["brand", "design", "synced"], "own folders only, not links")
        self.assertEqual(out["mods"], ["usage-band"])
        keys = {k["key"]: k["value_preview"] for k in out["keys"]}
        self.assertEqual(keys["theme"], '"dark"')
        self.assertIn("env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", keys)
        self.assertIn("enabledPlugins.superpowers@claude-plugins-official", keys)
        for never in ("hooks", "statusLine", "autoMode"):
            self.assertNotIn(never, keys)
        self.assertFalse(any(k.startswith("skillOverrides") for k in keys))
        self.assertEqual(out["claude_md"], {"exists": True, "lines": 1})
