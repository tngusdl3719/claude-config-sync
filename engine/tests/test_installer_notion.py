from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager

from helpers import TempCase, make_home
from fake_notion import TOKEN, FakeNotion

from claude_sync.gitio import head
from claude_sync.paths import Paths
from claude_sync.setup import init
from claude_sync.state import load_state
from claude_sync.sync import run_sync

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALLER = os.path.join(ROOT, "installer", "claude-sync-install.command")
BUILD = os.path.join(ROOT, "scripts", "build-installer.sh")
DEPLOY = os.path.join(ROOT, "scripts", "deploy.sh")


class NotionInstallerTest(TempCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNotion().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.creds = os.path.join(self.root, "keychain")
        self.base_env = dict(os.environ, CLAUDE_SYNC_NOTION_API=self.fake.base, GIT_CONFIG_NOSYSTEM="1",
                             CLAUDE_SYNC_CREDENTIAL_HELPER="store --file=%s" % self.creds)
        self.page_id = self.fake.add_page()
        self.page = "https://www.notion.so/me/Claude-Sync-%s" % self.page_id
        self.a = Paths(make_home(self.root, "a", skills=["brand"]))
        with self._engine_env():
            out = init(self.a, pc="pc-a", skills=["brand"], mods=[], keys=["theme"], claude_md="move", now=1.0,
                       backend="notion", page=self.page, secret=TOKEN)
        self.assertEqual(out["result"], "ok", out)
        self.built = os.path.join(self.root, "dist", "install.command")
        subprocess.run(["bash", BUILD], check=True, capture_output=True, env=dict(os.environ, OUT=self.built))

    @contextmanager
    def _engine_env(self):
        old = dict(os.environ)
        os.environ.update(self.base_env)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(old)

    def _share_engine(self, now=2.0):
        """The first PC sends the config-sync mod (engine inside) to the shared repo."""
        with self._engine_env():
            subprocess.run(["bash", DEPLOY], check=True, capture_output=True,
                           env=dict(os.environ, DEPLOY_TARGET=os.path.join(self.a.shared, "mods", "config-sync")))
            self.assertEqual(run_sync(self.a, auto=False, now=now).last_result, "ok")

    def _env(self, home, **extra):
        env = dict(self.base_env, HOME=home, CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_BACKEND="notion",
                   CLAUDE_SYNC_NOTION_PAGE=self.page, CLAUDE_SYNC_NOTION_SECRET=TOKEN, CLAUDE_SYNC_PC="pc-b",
                   CLAUDE_SYNC_WORDS="corp", CLAUDE_SYNC_RECEIVE_ONLY="1")
        env.update(extra)
        return env

    def test_new_pc_joins_from_notion_with_the_packed_engine(self):
        self._share_engine()
        home = make_home(self.root, "b", claude_md="# 회사\n")
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertIn("적용 결과: ok", r.stdout)
        b = Paths(home)
        self.assertEqual(head(b), head(self.a))
        s = load_state(b)
        self.assertEqual((s.backend, s.receive_only, s.pc_name), ("notion", True, "pc-b"))
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        self.assertTrue(os.path.exists(os.path.join(b.shared, "mods", "config-sync", "bin", "claude-sync")))
        with open(os.path.join(b.sync_dir, "state.json"), encoding="utf-8") as f:
            self.assertNotIn(TOKEN, f.read())
        # A second run on the same PC re-applies instead of failing.
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_rerun_with_another_page_is_refused(self):
        self._share_engine()
        home = make_home(self.root, "b")
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        other = self.fake.add_page()
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, timeout=180,
                           env=self._env(home, CLAUDE_SYNC_NOTION_PAGE="https://www.notion.so/x-%s" % other))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("다른 저장소", r.stderr)
        self.assertEqual(load_state(Paths(home)).notion_page_id, self.page_id)
        self.assertEqual(self.fake.pages[other]["children"], [], "nothing was made on the other page")

    def test_a_join_stopped_before_anything_arrived_is_finished_by_running_again(self):
        self._share_engine()
        home = make_home(self.root, "b")
        shared = Paths(home).shared
        os.makedirs(shared)
        subprocess.run(["git", "init", "-q", "-b", "main", shared], check=True)  # no HEAD, no state file
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(head(Paths(home)), head(self.a))
        self.assertEqual(load_state(Paths(home)).backend, "notion")

    def test_shared_repo_without_the_sync_mod_is_not_reported_as_done(self):
        home = make_home(self.root, "b")
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config-sync", r.stderr)
        self.assertNotIn("끝났어요", r.stdout)
        # Once the first PC shares the mod, running the installer again finishes the job.
        self._share_engine(now=3.0)
        r = subprocess.run(["bash", self.built], capture_output=True, text=True, env=self._env(home), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(Paths(home).shared, "mods", "config-sync", "bin", "claude-sync")))

    def test_unbuilt_installer_says_it_has_no_engine(self):
        home = make_home(self.root, "b")
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=self._env(home), timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("엔진이 들어 있지 않아요", r.stderr)
        self.assertFalse(os.path.exists(Paths(home).shared))

    def test_wrong_secret_fails_readably_and_leaves_nothing(self):
        home = make_home(self.root, "b")
        r = subprocess.run(["bash", self.built], capture_output=True, text=True,
                           env=dict(self._env(home), CLAUDE_SYNC_NOTION_SECRET="ntn_wrong_value"), timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("비밀 키", r.stderr)
        self.assertNotIn("ntn_wrong_value", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(Paths(home).shared))

    def test_bad_page_address_is_refused_before_anything(self):
        home = make_home(self.root, "b")
        r = subprocess.run(["bash", self.built], capture_output=True, text=True,
                           env=dict(self._env(home), CLAUDE_SYNC_NOTION_PAGE="https://www.notion.so/no-id"), timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("페이지 id", r.stderr)
