from __future__ import annotations

import json
import os

from helpers import TempCase, make_home, make_remote, read_json, run, write

from claude_sync.blocklist import save_words
from claude_sync.claudemd import IMPORT_LINE
from claude_sync.gitio import git, head
from claude_sync.lock import Lock
from claude_sync.paths import Paths
from claude_sync.state import load_state, save_state
from claude_sync.sync import run_sync

NOW = 1_800_000_000.0


def join(home, remote, pc, require_approval=False):
    p = Paths(home)
    run(["git", "clone", "-q", remote, p.shared])
    s = load_state(p)
    s.pc_name = pc
    s.require_approval = require_approval
    save_state(p, s)
    return p


def remote_file(remote, path):
    r = run(["git", "--git-dir", remote, "show", "main:%s" % path])
    return r.stdout


def remote_files(remote):
    return run(["git", "--git-dir", remote, "ls-tree", "-r", "--name-only", "main"]).stdout.split()


def edit_settings(p, **changes):
    data = read_json(p.settings)
    data.update(changes)
    with open(p.settings, "w") as f:
        json.dump(data, f, indent=2)


class World(TempCase):
    def setUp(self):
        super().setUp()
        self.remote = make_remote(self.root)
        self.a = join(make_home(self.root, "a", skills=[]), self.remote, "pc-a")
        write(self.a.shared_claude_md(), "공용 v1\n")
        write(self.a.shared_settings(), json.dumps({"effortLevel": "xhigh", "theme": "dark"}, indent=2) + "\n")
        write(os.path.join(self.a.shared, "skills", "brand", "SKILL.md"), "---\nname: brand\n---\n")
        s = run_sync(self.a, auto=False, now=NOW)
        self.assertEqual(s.last_result, "ok", s.last_error)


class SyncTest(World):
    def test_two_pc_roundtrip_settings_skill_claudemd(self):
        b_home = make_home(self.root, "b", claude_md="# 회사 지침\n")
        edit_settings(Paths(b_home), theme="light")
        b = join(b_home, self.remote, "pc-b")
        self.assertEqual(run_sync(b, auto=False, now=NOW).last_result, "ok")
        self.assertEqual(read_json(b.settings)["theme"], "dark")
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n\n# 회사 지침\n")
        edit_settings(b, effortLevel="high")
        write(b.shared_claude_md(), "공용 v2\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "ok")
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 v2\n")
        self.assertEqual(s.last_pull, NOW + 2)

    def test_company_skill_never_leaves(self):
        b_home = make_home(self.root, "b", skills=["corp-x"])
        data = read_json(Paths(b_home).settings)
        data["skillOverrides"]["corp-x"] = "off"
        with open(Paths(b_home).settings, "w") as f:
            json.dump(data, f)
        b = join(b_home, self.remote, "pc-b")
        write(b.shared_claude_md(), "공용 v1\nb 수정\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW).last_result, "ok")
        self.assertIn("b 수정", remote_file(self.remote, "CLAUDE.md"))
        self.assertFalse(any("corp-x" in f for f in remote_files(self.remote)))
        self.assertNotIn("corp-x", remote_file(self.remote, "settings.json"))
        self.assertTrue(os.path.isdir(os.path.join(b.skills, "corp-x")))

    def test_orca_hooks_preserved_after_sync(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        before = read_json(b.settings)
        run_sync(b, auto=False, now=NOW)
        after = read_json(b.settings)
        for key in ("hooks", "statusLine", "autoMode"):
            self.assertEqual(after[key], before[key])

    def test_file_conflict_keeps_ours_and_records_pending(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        write(b.shared_claude_md(), "공용 B\n")
        run_sync(b, auto=False, now=NOW + 1)
        write(self.a.shared_claude_md(), "공용 A\n")
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "conflict")
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 A\n")
        self.assertEqual([c["file"] for c in s.conflicts], ["CLAUDE.md"])
        with open(s.conflicts[0]["pending"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 B\n")
        self.assertEqual(remote_file(self.remote, "CLAUDE.md"), "공용 B\n")

    def test_setting_conflict_holds_push(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        edit_settings(b, theme="light")
        run_sync(b, auto=False, now=NOW + 1)
        edit_settings(self.a, theme="solarized")
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "conflict")
        self.assertEqual(s.conflicts[0]["path"], ["theme"])
        self.assertEqual((s.conflicts[0]["local"], s.conflicts[0]["remote"]), ("solarized", "light"))
        self.assertEqual(read_json(self.a.settings)["theme"], "solarized")
        self.assertIn('"light"', remote_file(self.remote, "settings.json"))

    def test_blocked_push_still_pulls(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        save_words(b, ["corp-host"])
        run_sync(b, auto=False, now=NOW)
        edit_settings(self.a, theme="light")
        run_sync(self.a, auto=False, now=NOW + 1)
        write(b.shared_claude_md(), "공용 v1\ninternal.corp-host 배포\n")
        s = run_sync(b, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "blocked")
        self.assertEqual(s.blocked[0]["word"], "corp-host")
        self.assertEqual(read_json(b.settings)["theme"], "light")
        self.assertNotIn("corp-host", remote_file(self.remote, "CLAUDE.md"))

    def test_allow_once_pushes_that_head_only(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        save_words(b, ["corp-host"])
        run_sync(b, auto=False, now=NOW)
        write(b.shared_claude_md(), "공용 v1\ncorp-host 1\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "blocked")
        s = load_state(b)
        s.allow_once = head(b)
        save_state(b, s)
        self.assertEqual(run_sync(b, auto=False, now=NOW + 2).last_result, "ok")
        self.assertIn("corp-host 1", remote_file(self.remote, "CLAUDE.md"))
        write(b.shared_claude_md(), "공용 v1\ncorp-host 1\ncorp-host 2\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 3).last_result, "blocked")

    def test_blocked_then_fixed_never_pushes_secret_in_history(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        save_words(b, ["corp-host"])
        run_sync(b, auto=False, now=NOW)
        write(b.shared_claude_md(), "공용 v1\ninternal.corp-host 배포\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "blocked")
        write(b.shared_claude_md(), "공용 v1\n일반 배포 서버\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 2).last_result, "ok")
        history = run(["git", "--git-dir", self.remote, "log", "-p", "--all"]).stdout
        self.assertIn("일반 배포 서버", history)
        self.assertNotIn("corp-host", history)

    def test_binary_conflict_recorded_as_bytes(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        logo = os.path.join("skills", "brand", "logo.bin")
        with open(os.path.join(b.shared, logo), "wb") as f:
            f.write(b"\xff\xfeB-version\x00")
        run_sync(b, auto=False, now=NOW + 1)
        with open(os.path.join(self.a.shared, logo), "wb") as f:
            f.write(b"\xff\xfeA-version\x00")
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "conflict", s.last_error)
        self.assertEqual([c["file"] for c in s.conflicts], ["skills/brand/logo.bin"])
        with open(s.conflicts[0]["pending"], "rb") as f:
            self.assertEqual(f.read(), b"\xff\xfeB-version\x00")
        with open(os.path.join(self.a.shared, logo), "rb") as f:
            self.assertEqual(f.read(), b"\xff\xfeA-version\x00")
        self.assertEqual(git(self.a, "status", "--porcelain").stdout.strip(), "")

    def test_conflicted_script_keeps_exec_bit(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        script = os.path.join("mods", "x", "bin", "run")
        write(os.path.join(self.a.shared, script), "#!/bin/sh\necho v1\n")
        os.chmod(os.path.join(self.a.shared, script), 0o755)
        run_sync(self.a, auto=False, now=NOW + 1)
        run_sync(b, auto=False, now=NOW + 2)
        write(os.path.join(b.shared, script), "#!/bin/sh\necho B\n")
        run_sync(b, auto=False, now=NOW + 3)
        write(os.path.join(self.a.shared, script), "#!/bin/sh\necho A\n")
        s = run_sync(self.a, auto=False, now=NOW + 4)
        self.assertEqual(s.last_result, "conflict")
        self.assertTrue(os.access(os.path.join(self.a.shared, script), os.X_OK))
        self.assertTrue(git(self.a, "ls-files", "-s", script).stdout.startswith("100755"))
        from claude_sync import actions
        actions.resolve(self.a, "file:%s" % script, "take")
        self.assertTrue(os.access(os.path.join(self.a.shared, script), os.X_OK))
        with open(os.path.join(self.a.shared, script)) as f:
            self.assertIn("echo B", f.read())

    def test_sync_untracks_caches_already_pushed(self):
        pyc = os.path.join(self.a.shared, "skills", "brand", "__pycache__", "core.cpython-314.pyc")
        os.makedirs(os.path.dirname(pyc))
        with open(pyc, "wb") as f:
            f.write(b"\x00cache")
        git(self.a, "add", "-f", "--", "skills/brand/__pycache__/core.cpython-314.pyc")
        git(self.a, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "old cache")
        git(self.a, "push", "-q", "origin", "HEAD:main")
        self.assertIn("skills/brand/__pycache__/core.cpython-314.pyc", remote_files(self.remote))
        s = run_sync(self.a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        files = remote_files(self.remote)
        self.assertIn(".gitignore", files)
        self.assertNotIn("skills/brand/__pycache__/core.cpython-314.pyc", files)
        self.assertTrue(os.path.exists(pyc))

    def test_existing_gitignore_gets_cache_patterns(self):
        write(os.path.join(self.a.shared, ".gitignore"), "notes.tmp\n")
        run_sync(self.a, auto=False, now=NOW + 1)
        text = remote_file(self.remote, ".gitignore")
        self.assertIn("notes.tmp", text)
        for pattern in ("__pycache__/", "*.pyc", ".DS_Store"):
            self.assertIn(pattern, text)

    def test_cache_exclusion_wins_over_existing_negation(self):
        write(os.path.join(self.a.shared, ".gitignore"), "*.pyc\n!keep.pyc\n")
        keep = os.path.join(self.a.shared, "skills", "brand", "keep.pyc")
        with open(keep, "wb") as f:
            f.write(b"\x00")
        git(self.a, "add", "-A")
        git(self.a, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "negation")
        git(self.a, "push", "-q", "origin", "HEAD:main")
        self.assertIn("skills/brand/keep.pyc", remote_files(self.remote))
        run_sync(self.a, auto=False, now=NOW + 1)
        run_sync(self.a, auto=False, now=NOW + 2)
        self.assertNotIn("skills/brand/keep.pyc", remote_files(self.remote))
        text = remote_file(self.remote, ".gitignore")
        self.assertEqual(text.count("*.pyc"), 2)

    def test_nested_negation_cannot_bring_caches_back(self):
        write(os.path.join(self.a.shared, "skills", "brand", ".gitignore"), "!*.pyc\n")
        with open(os.path.join(self.a.shared, "skills", "brand", "cache.pyc"), "wb") as f:
            f.write(b"\x00")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
        self.assertNotIn("skills/brand/cache.pyc", remote_files(self.remote))
        self.assertIn("skills/brand/.gitignore", remote_files(self.remote))

    def test_personal_ignore_rules_never_untrack_shared_files(self):
        excludes = os.path.join(self.root, "my-global-ignore")
        write(excludes, "*.md\n")
        git(self.a, "config", "core.excludesFile", excludes)
        s = run_sync(self.a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertIn("CLAUDE.md", remote_files(self.remote))
        self.assertIn("skills/brand/SKILL.md", remote_files(self.remote))

    def test_receive_only_pc_never_pushes_but_receives(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        s = load_state(b)
        s.receive_only = True
        save_state(b, s)
        self.assertEqual(run_sync(b, auto=False, now=NOW).last_result, "ok")
        before = run(["git", "--git-dir", self.remote, "rev-parse", "main"]).stdout
        write(b.shared_claude_md(), "공용 v1\n회사 Mac 수정\n")
        edit_settings(b, theme="light")
        s = run_sync(b, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertIsNone(s.last_push)
        self.assertEqual(run(["git", "--git-dir", self.remote, "rev-parse", "main"]).stdout, before)
        with open(b.shared_settings(), encoding="utf-8") as f:
            self.assertNotIn("light", f.read())
        edit_settings(self.a, effortLevel="high")
        run_sync(self.a, auto=False, now=NOW + 2)
        s = run_sync(b, auto=False, now=NOW + 3)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(b.settings)["effortLevel"], "high")
        self.assertEqual(read_json(b.settings)["theme"], "light")
        with open(b.shared_claude_md(), encoding="utf-8") as f:
            self.assertIn("회사 Mac 수정", f.read())

    def test_needs_approval_holds_merge_on_a(self):
        s = load_state(self.a)
        s.require_approval = True
        save_state(self.a, s)
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        write(os.path.join(b.shared, "mods", "x", "hooks", "register.ts"), "export {}\n")
        run_sync(b, auto=False, now=NOW)
        s = run_sync(self.a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "needs_approval")
        self.assertFalse(os.path.exists(os.path.join(self.a.shared, "mods", "x")))
        self.assertEqual(s.pending_approval[0]["files"], ["mods/x/hooks/register.ts"])
        s.approved_remote = s.pending_approval[0]["remote"]
        save_state(self.a, s)
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "ok")
        self.assertTrue(os.path.islink(os.path.join(self.a.mods, "x")))
        self.assertEqual(s.pending_approval, [])

    def test_sync_offline_keeps_local_commit(self):
        git(self.a, "remote", "set-url", "origin", os.path.join(self.root, "missing.git"))
        write(self.a.shared_claude_md(), "공용 오프라인\n")
        s = run_sync(self.a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("오프라인", git(self.a, "show", "HEAD:CLAUDE.md").stdout)
        self.assertEqual(remote_file(self.remote, "CLAUDE.md"), "공용 v1\n")

    def test_lock_second_sync_skips(self):
        before = head(self.a)
        held = Lock(self.a)
        self.assertTrue(held.acquire(wait=False))
        try:
            write(self.a.shared_claude_md(), "바뀜\n")
            s = run_sync(self.a, auto=True, now=NOW + 1)
        finally:
            held.release()
        self.assertEqual(s.last_pull, NOW)
        self.assertEqual(head(self.a), before)

    def test_plugin_dirs_include_shared_mods(self):
        write(os.path.join(self.a.shared, "mods", "usage-band", "hooks", "hooks.json"), "{}")
        run_sync(self.a, auto=False, now=NOW + 1)
        dirs = read_json(self.a.settings)["env"]["CLAUDE_CODE_PLUGIN_DIRS"]
        self.assertEqual(dirs, os.path.join(self.a.mods, "usage-band"))
