from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

from helpers import HERE, TempCase, make_home, make_remote, run, write

from claude_sync.gitio import git
from claude_sync.paths import Paths
from claude_sync.remote import RemoteError, configure_https, store_token


def guarded_helper_for(base):
    """What configure_https writes: the base helper for get/store only, erase ignored."""
    cmd = base[1:] if base.startswith("!") else "git credential-%s" % base
    return '!f() { case "$1" in get|store) %s "$1" ;; *) cat >/dev/null ;; esac; }; f' % cmd

MAIN = os.path.join(os.path.dirname(HERE), "main.py")
URL = "https://gitlab.com/me/claude-config.git"
USER = "gitlab+deploy-token-123"
TOKEN = "glpat-SECRET-token-0042"


def fake_helper(root):
    """A credential helper that records its arguments and everything it reads on stdin."""
    log = os.path.join(root, "helper.log")
    script = os.path.join(root, "fake-helper")
    write(script, '#!/bin/sh\necho "ARGS:$*" >> "%s"\ncat >> "%s"\n' % (log, log))
    os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    return "!" + script, log


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


class RemoteTest(TempCase):
    def setUp(self):
        super().setUp()
        self.helper, self.log = fake_helper(self.root)
        self.old_env = dict(os.environ)
        os.environ["CLAUDE_SYNC_CREDENTIAL_HELPER"] = self.helper
        self.remote = make_remote(self.root)
        self.p = Paths(make_home(self.root, "a"))
        run(["git", "clone", "-q", self.remote, self.p.shared])

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        super().tearDown()

    def helpers(self):
        return git(self.p, "config", "--local", "--get-all", "credential.helper").stdout.split("\n")[:-1]

    def test_configure_https_embeds_username_and_sets_helper(self):
        url = configure_https(self.p, URL, USER)
        self.assertEqual(url, "https://gitlab+deploy-token-123@gitlab.com/me/claude-config.git")
        self.assertEqual(git(self.p, "remote", "get-url", "origin").stdout.strip(), url)
        self.assertEqual(self.helpers(), ["", guarded_helper_for(self.helper)])
        self.assertEqual(git(self.p, "config", "--local", "credential.useHttpPath").stdout.strip(), "true")

    def test_default_helper_is_osxkeychain(self):
        del os.environ["CLAUDE_SYNC_CREDENTIAL_HELPER"]
        configure_https(self.p, URL, USER)
        self.assertEqual(self.helpers(), ["", guarded_helper_for("osxkeychain")])

    def test_configure_again_does_not_stack_helpers(self):
        configure_https(self.p, URL, USER)
        configure_https(self.p, URL, USER)
        self.assertEqual(self.helpers(), ["", guarded_helper_for(self.helper)])

    def test_username_with_at_sign_is_escaped(self):
        url = configure_https(self.p, URL, "me@example.com")
        self.assertEqual(url, "https://me%40example.com@gitlab.com/me/claude-config.git")

    def test_rejects_bad_url_and_username(self):
        for bad in ["http://gitlab.com/me/claude-config.git", "https://gitlab.com/me/claude-config",
                    "https://u:pw@gitlab.com/me/claude-config.git", "https://gitlab.com/me/x y.git",
                    "file://%s" % self.remote, "gitlab.com:me/claude-config.git"]:
            with self.assertRaises(RemoteError, msg=bad):
                configure_https(self.p, bad, USER)
        for bad in ["", "a b", "x;rm", "u/v", "x" * 101]:
            with self.assertRaises(RemoteError, msg=bad):
                configure_https(self.p, URL, bad)

    def test_switching_url_drops_stale_tracking_refs(self):
        write(os.path.join(self.p.shared, "CLAUDE.md"), "x\n")
        run(["git", "-C", self.p.shared, "add", "-A"])
        run(["git", "-C", self.p.shared, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "x"])
        run(["git", "-C", self.p.shared, "push", "-q", "origin", "HEAD:main"])
        run(["git", "-C", self.p.shared, "fetch", "-q", "origin"])
        self.assertTrue(git(self.p, "rev-parse", "--verify", "-q", "origin/main", check=False).stdout.strip())
        configure_https(self.p, URL, USER)
        self.assertEqual(git(self.p, "rev-parse", "--verify", "-q", "origin/main", check=False).stdout.strip(), "")

    def test_store_token_passes_token_only_via_stdin(self):
        url = configure_https(self.p, URL, USER)
        calls = []

        def runner(argv, **kw):
            calls.append((list(argv), kw.get("input")))
            return subprocess.run(argv, **kw)

        out = store_token(self.p, url, TOKEN, runner=runner)
        self.assertNotIn(TOKEN, json.dumps(out))
        for argv, _ in calls:
            self.assertFalse(any(TOKEN in a for a in argv))
        self.assertIn("password=%s" % TOKEN, calls[0][1])
        log = read(self.log)
        self.assertIn("ARGS:store", log)
        for line in ["protocol=https", "host=gitlab.com", "path=me/claude-config.git", "username=%s" % USER,
                     "password=%s" % TOKEN]:
            self.assertIn(line, log.splitlines())
        self.assertNotIn(TOKEN, read(os.path.join(self.p.shared, ".git", "config")))

    def _cli(self, *args, token=TOKEN, extra=None):
        env = dict(os.environ)
        env.update(extra or {})
        return subprocess.run([sys.executable, "-I", MAIN, "--home", self.p.home] + list(args),
                              input=token + "\n", capture_output=True, text=True, env=env)

    def test_cli_set_remote_reads_token_from_stdin(self):
        target = make_remote(os.path.join(self.root, "new"))
        r = self._cli("set-remote", "file://%s" % target, "--username", USER,
                      extra={"CLAUDE_SYNC_TEST_HTTPS_LIKE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["remote"], "file://%s" % target)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, read(os.path.join(self.p.shared, ".git", "config")))
        self.assertIn("password=%s" % TOKEN, read(self.log).splitlines())

    def test_cli_set_remote_refuses_file_urls_outside_tests(self):
        target = make_remote(os.path.join(self.root, "new"))
        r = self._cli("set-remote", "file://%s" % target, "--username", USER)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))

    def test_cli_set_remote_reports_unreachable(self):
        r = self._cli("set-remote", "file://%s" % os.path.join(self.root, "missing.git"), "--username", USER,
                      extra={"CLAUDE_SYNC_TEST_HTTPS_LIKE": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))  # refused, with the PC left as it was

    def test_cli_set_remote_needs_a_token(self):
        r = self._cli("set-remote", URL, "--username", USER, token="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))


class SetRemoteTimeoutTest(TempCase):
    def test_connectivity_check_has_a_deadline(self):
        from claude_sync import remote as remote_mod
        seen = []
        real = remote_mod.git

        def spy(p, *args, **kw):
            if "ls-remote" in args:
                seen.append(kw.get("timeout"))
                return subprocess.CompletedProcess(args, 0, "", "")
            return real(p, *args, **kw)

        remote_mod.git = spy
        try:
            p = Paths(make_home(self.root, "a"))
            os.makedirs(p.shared)
            run(["git", "init", "-q", "-b", "main", p.shared])
            os.environ["CLAUDE_SYNC_CREDENTIAL_HELPER"] = "!true"
            remote_mod.set_remote(p, "https://gitlab.com/me/claude-config.git", "pc-a", "tok")
        finally:
            remote_mod.git = real
            os.environ.pop("CLAUDE_SYNC_CREDENTIAL_HELPER", None)
        self.assertTrue(seen and seen[0] is not None and seen[0] <= 30, seen)


class PushUrlTest(TempCase):
    def test_switch_clears_old_push_url(self):
        p = Paths(make_home(self.root, "a"))
        os.makedirs(p.shared)
        run(["git", "init", "-q", "-b", "main", p.shared])
        run(["git", "-C", p.shared, "remote", "add", "origin", "claude-sync-server:claude-config.git"])
        run(["git", "-C", p.shared, "config", "remote.origin.pushurl", "claude-sync-server:claude-config.git"])
        run(["git", "-C", p.shared, "config", "--add", "remote.origin.url", "split:old-mirror.git"])
        configure_https(p, "https://gitlab.com/me/claude-config.git", "pc-a")
        urls = subprocess.run(["git", "-C", p.shared, "config", "--get-all", "remote.origin.url"],
                              capture_output=True, text=True).stdout.split()
        self.assertEqual(urls, ["https://pc-a@gitlab.com/me/claude-config.git"])
        r = subprocess.run(["git", "-C", p.shared, "config", "--get-all", "remote.origin.pushurl"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")
        url = subprocess.run(["git", "-C", p.shared, "remote", "get-url", "--push", "origin"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(url, "https://pc-a@gitlab.com/me/claude-config.git")


class EraseGuardTest(TempCase):
    def test_refused_request_cannot_erase_the_token(self):
        log = os.path.join(self.root, "helper.log")
        fake = os.path.join(self.root, "fake-helper")
        write(fake, '#!/bin/sh\necho "CALL:$1" >> "%s"\ncat >/dev/null\n' % log)
        os.chmod(fake, 0o755)
        os.environ["CLAUDE_SYNC_CREDENTIAL_HELPER"] = "!" + fake
        try:
            p = Paths(make_home(self.root, "a"))
            os.makedirs(p.shared)
            run(["git", "init", "-q", "-b", "main", p.shared])
            configure_https(p, "https://gitlab.com/me/claude-config.git", "pc-a")
        finally:
            os.environ.pop("CLAUDE_SYNC_CREDENTIAL_HELPER", None)
        req = "protocol=https\nhost=gitlab.com\npath=me/claude-config.git\nusername=pc-a\npassword=x\n\n"
        for op in ("approve", "reject"):
            subprocess.run(["git", "-C", p.shared, "credential", op], input=req, text=True, capture_output=True)
        subprocess.run(["git", "-C", p.shared, "credential", "fill"], input=req.replace("password=x\n", ""),
                       text=True, capture_output=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        with open(log) as f:
            calls = f.read().split()
        self.assertIn("CALL:store", calls)
        self.assertIn("CALL:get", calls)
        self.assertNotIn("CALL:erase", calls)
