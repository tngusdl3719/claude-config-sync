from __future__ import annotations

import json
import os

from helpers import TempCase, make_home, make_remote, read_json, run, write
from test_sync import remote_file, remote_files

from claude_sync.claudemd import IMPORT_LINE
from claude_sync.paths import Paths
from claude_sync.setup import init, setup

KEYS = "effortLevel,theme,skillOverrides,enabledPlugins,env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE,permissions.defaultMode"


class SetupTest(TempCase):
    def setUp(self):
        super().setUp()
        self.remote = make_remote(self.root)
        self.a = Paths(make_home(self.root, "a", claude_md="# 개인 지침\nCodex 리뷰\n",
                                 skills=["brand", "design", "notes"], mods=["usage-band"]))

    def _init(self):
        return init(self.a, remote=self.remote, pc="pc-a", skills=["brand", "design"], mods=["usage-band"],
                    keys=KEYS.split(","), claude_md="move", require_approval=True, now=1.0)

    def test_init_moves_and_links_and_pushes(self):
        out = self._init()
        self.assertEqual(out["result"], "ok", out)
        self.assertTrue(os.path.isdir(os.path.join(self.a.shared, "skills", "brand")))
        self.assertTrue(os.path.islink(os.path.join(self.a.skills, "brand")))
        self.assertFalse(os.path.islink(os.path.join(self.a.skills, "notes")))
        self.assertTrue(os.path.islink(os.path.join(self.a.mods, "usage-band")))
        self.assertEqual(remote_file(self.remote, "CLAUDE.md"), "# 개인 지침\nCodex 리뷰\n")
        with open(self.a.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n")
        shared = json.loads(remote_file(self.remote, "settings.json"))
        self.assertEqual(shared["effortLevel"], "xhigh")
        self.assertEqual(shared["skillOverrides"], {"design": "off"})
        self.assertEqual(shared["permissions"], {"defaultMode": "auto"})
        self.assertNotIn("hooks", shared)
        local = read_json(self.a.settings)
        self.assertEqual(local["skillOverrides"], {"ui-ux-pro-max": "on", "design": "off"})
        self.assertIn("mods/usage-band/hooks/register.ts", remote_files(self.remote))

    def test_init_ignores_python_caches_and_finder_files(self):
        with open(os.path.join(self.a.skills, "brand", "x.pyc"), "wb") as f:
            f.write(b"\x00")
        os.makedirs(os.path.join(self.a.skills, "brand", "__pycache__"))
        with open(os.path.join(self.a.skills, "brand", "__pycache__", "m.cpython-39.pyc"), "wb") as f:
            f.write(b"\x00")
        with open(os.path.join(self.a.skills, "brand", ".DS_Store"), "wb") as f:
            f.write(b"\x00")
        self._init()
        files = remote_files(self.remote)
        self.assertIn(".gitignore", files)
        self.assertFalse([f for f in files if f.endswith(".pyc") or f.endswith(".DS_Store")], files)

    def test_init_backup_written(self):
        out = self._init()
        self.assertTrue(os.path.isfile(os.path.join(out["backup"], "settings.json")))
        with open(os.path.join(out["backup"], "CLAUDE.md"), encoding="utf-8") as f:
            self.assertIn("개인 지침", f.read())
        with open(os.path.join(out["backup"], "skills.txt"), encoding="utf-8") as f:
            self.assertIn("brand", f.read())

    def test_init_refuses_when_shared_exists(self):
        self._init()
        with self.assertRaises(RuntimeError):
            self._init()

    def _company(self):
        self._init()
        b = Paths(make_home(self.root, "b", claude_md="# 회사 지침\n내부 배포 규칙\n", skills=["corp-x"]))
        data = read_json(b.settings)
        data["skillOverrides"] = {"corp-x": "off"}
        data["theme"] = "light"
        with open(b.settings, "w") as f:
            json.dump(data, f, indent=2)
        run(["git", "clone", "-q", self.remote, b.shared])
        return b

    def test_setup_on_second_home_keeps_company_items(self):
        b = self._company()
        hooks_before = read_json(b.settings)["hooks"]
        out = setup(b, pc="pc-b", words=["corp"], now=2.0)
        self.assertEqual(out["result"], "ok", out)
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n\n# 회사 지침\n내부 배포 규칙\n")
        self.assertFalse(os.path.islink(os.path.join(b.skills, "corp-x")))
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        local = read_json(b.settings)
        self.assertEqual(local["skillOverrides"]["corp-x"], "off")
        self.assertEqual(local["theme"], "dark")
        self.assertEqual(local["hooks"], hooks_before)
        self.assertIn({"path": ["theme"], "local": "light", "shared": "dark"}, out["overwritten"])
        self.assertFalse(any("corp" in f for f in remote_files(self.remote)))
        self.assertNotIn("corp", remote_file(self.remote, "settings.json"))

    def test_setup_receive_only(self):
        b = self._company()
        out = setup(b, pc="pc-b", words=[], now=2.0, receive_only=True)
        self.assertEqual(out["result"], "ok", out)
        from claude_sync.state import load_state
        self.assertTrue(load_state(b).receive_only)

    def test_setup_is_idempotent(self):
        b = self._company()
        setup(b, pc="pc-b", words=["corp"], now=2.0)
        out = setup(b, pc="pc-b", words=["corp"], now=3.0)
        self.assertEqual(out["result"], "ok", out)
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read().count(IMPORT_LINE), 1)

    def test_setup_reports_missing_plugins_and_managed_keys(self):
        b = self._company()
        managed = os.path.join(self.root, "managed-settings.json")
        write(managed, json.dumps({"allowManagedHooksOnly": True, "model": "x"}))
        out = setup(b, pc="pc-b", words=[], now=2.0, managed_settings=managed)
        self.assertEqual(out["missing_plugins"], ["superpowers@claude-plugins-official"])
        self.assertEqual(out["managed_settings_keys"], ["allowManagedHooksOnly"])
