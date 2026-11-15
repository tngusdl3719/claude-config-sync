from __future__ import annotations

import json
import os
import subprocess

from helpers import TempCase, make_home, make_remote, write

from claude_sync.paths import Paths
from claude_sync.setup import init
from claude_sync.sync import run_sync

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECOVER = os.path.join(ROOT, "scripts", "receive-only-recover.sh")
DEPLOY = os.path.join(ROOT, "scripts", "deploy.sh")
TOKEN = "glpat-RECOVER-secret-4410"
HTTPS = "https://pc-b@example.test/team/config.git"


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


class RecoverTest(TempCase):
    def setUp(self):
        super().setUp()
        self.remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a"))
        init(a, remote=self.remote, pc="pc-a", skills=[], mods=[], keys=["theme"], claude_md="move",
             require_approval=False, now=1.0)
        subprocess.run(["bash", DEPLOY], check=True, capture_output=True,
                       env=dict(os.environ, DEPLOY_TARGET=os.path.join(a.shared, "mods", "config-sync")))
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        b = Paths(make_home(self.root, "b"))
        subprocess.run(["git", "clone", "-q", self.remote, b.shared], check=True)
        write(os.path.join(b.sync_dir, "state.json"), json.dumps({"pc_name": "pc-b"}))
        self.assertEqual(run_sync(b, auto=False, now=3.0).last_result, "ok")
        write(a.shared_claude_md(), "공용 v2\n")
        self.assertEqual(run_sync(a, auto=False, now=4.0).last_result, "ok")
        # As on the company Mac: an https origin and the plain (erasing) keychain helper.
        _git("-C", b.shared, "remote", "set-url", "origin", HTTPS)
        _git("-C", b.shared, "config", "--local", "--unset-all", "credential.helper")
        _git("-C", b.shared, "config", "--local", "credential.helper", "osxkeychain")
        self.b = b
        self.log = os.path.join(self.root, "helper.log")
        helper = os.path.join(self.root, "fake-helper")
        write(helper, '#!/bin/sh\necho "ARGS:$*" >> "%s"\ncat >> "%s"\n' % (self.log, self.log))
        os.chmod(helper, 0o755)
        self.helper = "!" + helper

    def _run(self, served):
        env = dict(os.environ, HOME=self.b.home, CLAUDE_SYNC_TOKEN=TOKEN, CLAUDE_SYNC_CREDENTIAL_HELPER=self.helper,
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="1",
                   GIT_CONFIG_KEY_0="url.file://%s.insteadOf" % served, GIT_CONFIG_VALUE_0=HTTPS)
        return subprocess.run(["bash", RECOVER], capture_output=True, text=True, env=env, timeout=120)

    def _assert_protected(self, out):
        self.assertNotIn(TOKEN, out)
        helpers = _git("-C", self.b.shared, "config", "--local", "--get-all", "credential.helper").stdout.split("\n")
        self.assertEqual(helpers[0], "")
        self.assertIn("get|store", helpers[1])
        self.assertNotIn(TOKEN, _read(os.path.join(self.b.shared, ".git", "config")))
        self.assertTrue(json.loads(_read(os.path.join(self.b.sync_dir, "state.json")))["receive_only"])
        self.assertFalse(os.path.exists(os.path.join(self.b.sync_dir, "lock")))
        # The token reached the keychain only through the guarded helper the script installed first.
        self.assertIn("ARGS:store", _read(self.log))
        self.assertIn("password=%s" % TOKEN, _read(self.log).splitlines())

    def test_recovers_receive_only_without_pushing(self):
        before = _git("--git-dir", self.remote, "rev-parse", "main").stdout
        r = self._run(self.remote)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self._assert_protected(r.stdout + r.stderr)
        self.assertEqual(_read(self.b.shared_claude_md()), "공용 v2\n")
        self.assertEqual(_git("--git-dir", self.remote, "rev-parse", "main").stdout, before)

    def test_failed_fetch_leaves_token_protected_and_lock_released(self):
        r = self._run(os.path.join(self.root, "missing.git"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("받지 못했어요", r.stderr)
        self._assert_protected(r.stdout + r.stderr)
