from __future__ import annotations

import os
import subprocess
import time

from helpers import TempCase, make_home, run

from claude_sync.gitio import commit_all, git
from claude_sync.lock import Lock
from claude_sync.paths import Paths
from claude_sync.state import State, load_state, save_state


class StateTest(TempCase):
    def test_state_roundtrip(self):
        p = Paths(make_home(self.root, "a"))
        s = load_state(p)
        s.pc_name = "pc-a"
        s.last_result = "ok"
        s.baseline = {'["effortLevel"]': "xhigh"}
        save_state(p, s)
        again = load_state(p)
        self.assertEqual(again.pc_name, "pc-a")
        self.assertEqual(again.last_result, "ok")
        self.assertEqual(again.baseline, {'["effortLevel"]': "xhigh"})

    def test_state_defaults_when_missing(self):
        p = Paths(make_home(self.root, "a"))
        s = load_state(p)
        self.assertIsInstance(s, State)
        self.assertEqual(s.last_result, "never")
        self.assertTrue(s.auto_enabled)
        self.assertFalse(s.require_approval)
        self.assertEqual(s.conflicts, [])


class LockTest(TempCase):
    def test_lock_second_acquire_fails_without_wait(self):
        p = Paths(make_home(self.root, "a"))
        first, second = Lock(p), Lock(p)
        self.assertTrue(first.acquire(wait=False))
        try:
            self.assertFalse(second.acquire(wait=False))
        finally:
            first.release()
        self.assertTrue(second.acquire(wait=False))
        second.release()

    def test_lock_stale_is_broken(self):
        p = Paths(make_home(self.root, "a"))
        held = Lock(p, stale_seconds=60)
        self.assertTrue(held.acquire(wait=False))
        old = time.time() - 120
        os.utime(p.lock_dir, (old, old))
        fresh = Lock(p, stale_seconds=60)
        self.assertTrue(fresh.acquire(wait=False))
        fresh.release()


class GitTimeoutTest(TempCase):
    def test_git_timeout_kills_the_whole_transport(self):
        p = Paths(make_home(self.root, "a"))
        os.makedirs(p.shared)
        run(["git", "init", "-q", "-b", "main", p.shared])
        marker = "37.%d" % os.getpid()
        started = time.time()
        r = git(p, "ls-remote", "ssh://example.invalid/x.git", check=False, timeout=1,
                env={"GIT_SSH_COMMAND": "sh -c 'sleep %s' --" % marker})
        self.assertLess(time.time() - started, 5)
        self.assertEqual(r.returncode, 124)
        time.sleep(0.3)
        left = subprocess.run(["pgrep", "-f", "sleep %s" % marker], capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "")


class GitTest(TempCase):
    def _repo(self):
        p = Paths(make_home(self.root, "a"))
        os.makedirs(p.shared)
        run(["git", "init", "-q", "-b", "main", p.shared])
        return p

    def test_commit_all_returns_none_when_clean(self):
        p = self._repo()
        with open(os.path.join(p.shared, "CLAUDE.md"), "w") as f:
            f.write("x\n")
        self.assertIsNotNone(commit_all(p, "pc-a", "first"))
        self.assertIsNone(commit_all(p, "pc-a", "nothing"))

    def test_commit_message_has_pc_trailer(self):
        p = self._repo()
        with open(os.path.join(p.shared, "CLAUDE.md"), "w") as f:
            f.write("x\n")
        sha = commit_all(p, "pc-a", "지침 수정")
        msg = git(p, "log", "-1", "--format=%B", sha).stdout
        self.assertTrue(msg.startswith("sync: pc-a: 지침 수정"))
        self.assertIn("PC: pc-a", msg)
        author = git(p, "log", "-1", "--format=%an <%ae>", sha).stdout.strip()
        self.assertEqual(author, "pc-a <pc-a@claude-sync>")
