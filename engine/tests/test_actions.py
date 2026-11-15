from __future__ import annotations

import json
import os
import subprocess
import sys

from helpers import HERE, make_home, read_json, write
from test_sync import NOW, World, edit_settings, join, remote_file, remote_files

from claude_sync import actions, report
from claude_sync.blocklist import save_words
from claude_sync.gitio import git, head
from claude_sync.lock import Lock
from claude_sync.state import load_state, save_state
from claude_sync.sync import run_sync

MAIN = os.path.join(os.path.dirname(HERE), "main.py")
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHk3x7v1u0kq company-mac"


class ActionsTest(World):
    def _b(self, **kw):
        b = join(make_home(self.root, "b", **kw), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        return b

    def test_status_reports_states_without_network(self):
        b = self._b()
        write(b.shared_claude_md(), "공용 v1\nb 편집\n")
        edit_settings(self.a, theme="light")
        run_sync(self.a, auto=False, now=NOW + 1)
        git(b, "fetch", "-q", "origin")
        git(b, "remote", "set-url", "origin", os.path.join(self.root, "missing.git"))
        st = report.status(b)
        self.assertEqual(st["pc"], "pc-b")
        self.assertEqual(st["items"]["claude_md"]["state"], "local")
        self.assertEqual(st["items"]["settings"]["state"], "incoming")
        self.assertEqual(st["incoming"], 1)
        self.assertEqual([s["name"] for s in st["items"]["skills"]], ["brand"])
        self.assertIn("hooks", st["local_only"]["settings"])
        self.assertEqual(st["others"][0]["pc"], "pc-a")

    def test_status_describes_skills_and_mods_from_their_own_files(self):
        write(os.path.join(self.a.shared, "skills", "brand", "SKILL.md"),
              "---\nname: brand\ndescription: >\n  회사 브랜드 색과\n  글꼴 지침\n---\nbody\n")
        write(os.path.join(self.a.skills, "corp-x", "SKILL.md"), '---\nname: corp-x\ndescription: "회사 전용 메모"\n---\n')
        write(os.path.join(self.a.skills, "plain", "SKILL.md"), "no front matter\ndescription: not this\n")
        write(os.path.join(self.a.mods, "notes", ".claude-plugin", "plugin.json"),
              json.dumps({"name": "notes", "description": "메모 띠"}))
        d = report.status(self.a)["descriptions"]
        self.assertEqual(d["shared"]["skills"].get("brand"), "회사 브랜드 색과 글꼴 지침")
        self.assertEqual(d["local"]["skills"].get("corp-x"), "회사 전용 메모")
        self.assertNotIn("plain", d["local"]["skills"])
        self.assertEqual(d["local"]["mods"].get("notes"), "메모 띠")

    def test_quoted_descriptions_keep_their_own_quotes(self):
        for name, line in (("inner", 'Use "foo"'), ("single", "'It''s a helper'"), ("double", '"Say \\"hi\\""')):
            write(os.path.join(self.a.skills, name, "SKILL.md"), "---\nname: %s\ndescription: %s\n---\n" % (name, line))
        write(os.path.join(self.a.skills, "block", "SKILL.md"), '---\nname: block\ndescription: |\n  "quoted text"\n---\n')
        d = report.status(self.a)["descriptions"]["local"]["skills"]
        self.assertEqual((d["inner"], d["single"], d["double"]), ('Use "foo"', "It's a helper", 'Say "hi"'))
        self.assertEqual(d["block"], '"quoted text"', "a block's quotes are its content")

    def test_a_pc_only_item_named_like_a_shared_one_keeps_its_own_description(self):
        link = os.path.join(self.a.skills, "brand")
        os.remove(link)  # this PC has its own 'brand' (a name collision keeps the local one)
        write(os.path.join(link, "SKILL.md"), "---\nname: brand\ndescription: 이 PC 것\n---\n")
        write(os.path.join(self.a.shared, "skills", "brand", "SKILL.md"), "---\nname: brand\ndescription: 공용 것\n---\n")
        d = report.status(self.a)["descriptions"]
        self.assertEqual(d["shared"]["skills"].get("brand"), "공용 것")
        self.assertEqual(d["local"]["skills"].get("brand"), "이 PC 것")

    def test_others_ignore_commits_without_pc_tag(self):
        b = self._b()
        write(os.path.join(b.shared, "README.md"), "web edit\n")
        git(b, "add", "-A")
        git(b, "-c", "user.name=홍길동", "-c", "user.email=me@example.com", "commit", "-qm", "Add new file")
        git(b, "push", "-q", "origin", "HEAD:main")
        git(self.a, "fetch", "-q", "origin")
        pcs = [o["pc"] for o in report.status(self.a)["others"]]
        self.assertNotIn("홍길동", pcs)

    def test_share_moves_dir_and_links(self):
        b = self._b(skills=["notes"])
        actions.share(b, "skills", "notes")
        self.assertTrue(os.path.isfile(os.path.join(b.shared, "skills", "notes", "SKILL.md")))
        self.assertTrue(os.path.islink(os.path.join(b.skills, "notes")))

    def test_share_blocked_by_word(self):
        b = self._b(skills=["notes"])
        save_words(b, ["body"])
        with self.assertRaises(actions.ActionError) as ctx:
            actions.share(b, "skills", "notes")
        self.assertEqual(ctx.exception.data["hits"][0]["word"], "body")
        self.assertFalse(os.path.islink(os.path.join(b.skills, "notes")))

    def test_share_blocked_by_word_in_folder_name(self):
        b = self._b(skills=["notes-acme"])
        write(os.path.join(b.skills, "notes-acme", "SKILL.md"), "harmless\n")
        save_words(b, ["acme"])
        with self.assertRaises(actions.ActionError) as ctx:
            actions.share(b, "skills", "notes-acme")
        self.assertEqual(ctx.exception.data["hits"][0]["word"], "acme")

    def test_resolve_refuses_while_another_sync_holds_the_lock(self):
        self._file_conflict()
        before = load_state(self.a)
        pending = before.conflicts[0]["pending"]
        held = Lock(self.a)
        self.assertTrue(held.acquire(wait=False))
        old_wait = actions.LOCK_WAIT_SECONDS
        actions.LOCK_WAIT_SECONDS = 0.3
        try:
            with self.assertRaises(actions.ActionError):
                actions.resolve(self.a, "file:CLAUDE.md", "take")
        finally:
            actions.LOCK_WAIT_SECONDS = old_wait
            held.release()
        self.assertTrue(os.path.exists(pending))
        self.assertEqual(load_state(self.a).conflicts, before.conflicts)
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 A\n")

    def test_unshare_restores_real_dir(self):
        b = self._b(skills=["notes"])
        actions.share(b, "skills", "notes")
        actions.unshare(b, "skills", "notes")
        self.assertFalse(os.path.islink(os.path.join(b.skills, "notes")))
        self.assertTrue(os.path.isfile(os.path.join(b.skills, "notes", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(b.shared, "skills", "notes")))

    def _file_conflict(self):
        b = self._b()
        write(b.shared_claude_md(), "공용 B\n")
        run_sync(b, auto=False, now=NOW + 1)
        write(self.a.shared_claude_md(), "공용 A\n")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 2).last_result, "conflict")
        return b

    def test_resolve_take_applies_pending(self):
        self._file_conflict()
        s = actions.resolve(self.a, "file:CLAUDE.md", "take")
        self.assertEqual(s.last_result, "ok", s.last_error)
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 B\n")
        self.assertEqual(s.conflicts, [])

    def test_resolve_keep_pushes_ours(self):
        self._file_conflict()
        s = actions.resolve(self.a, "file:CLAUDE.md", "keep")
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(remote_file(self.remote, "CLAUDE.md"), "공용 A\n")

    def test_resolve_take_setting(self):
        b = self._b()
        edit_settings(b, theme="light")
        run_sync(b, auto=False, now=NOW + 1)
        edit_settings(self.a, theme="solarized")
        run_sync(self.a, auto=False, now=NOW + 2)
        s = actions.resolve(self.a, "setting:theme", "take")
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(self.a.settings)["theme"], "light")

    def test_reject_reverts_held_commits(self):
        s = load_state(self.a)
        s.require_approval = True
        save_state(self.a, s)
        b = self._b()
        write(os.path.join(b.shared, "mods", "x", "hooks", "register.ts"), "export {}\n")
        write(b.shared_claude_md(), "공용 v1\nb 편집\n")
        run_sync(b, auto=False, now=NOW + 1)
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 2).last_result, "needs_approval")
        s = actions.reject(self.a)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertFalse(os.path.exists(os.path.join(self.a.shared, "mods", "x")))
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertIn("b 편집", f.read())
        self.assertFalse(any(f.startswith("mods/x") for f in remote_files(self.remote)))
        run_sync(b, auto=False, now=NOW + 3)
        self.assertFalse(os.path.lexists(os.path.join(b.mods, "x")))

    def test_discard_blocked_keeps_unapproved_remote_out(self):
        s = load_state(self.a)
        s.require_approval = True
        save_state(self.a, s)
        save_words(self.a, ["corp-host"])
        b = self._b()
        write(self.a.shared_claude_md(), "공용 v1\ncorp-host\n")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "blocked")
        write(os.path.join(b.shared, "mods", "x", "hooks", "register.ts"), "export {}\n")
        run_sync(b, auto=False, now=NOW + 2)
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 3).last_result, "needs_approval")
        s = actions.discard_blocked(self.a)
        self.assertFalse(os.path.exists(os.path.join(self.a.shared, "mods", "x")))
        self.assertEqual(s.last_result, "needs_approval")
        with open(self.a.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 v1\n")

    def test_reject_builds_commit_without_touching_worktree(self):
        s = load_state(self.a)
        s.require_approval = True
        save_state(self.a, s)
        b = self._b()
        write(os.path.join(b.shared, "mods", "x", "hooks", "register.ts"), "export {}\n")
        write(b.shared_claude_md(), "공용 v1\nb 편집\n")
        run_sync(b, auto=False, now=NOW + 1)
        s = run_sync(self.a, auto=False, now=NOW + 2)
        before = head(self.a)
        sha = actions.build_rejection(self.a, s.pending_approval[0])
        self.assertEqual(head(self.a), before)
        self.assertEqual(git(self.a, "status", "--porcelain").stdout.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.a.shared, "mods", "x")))
        files = git(self.a, "ls-tree", "-r", "--name-only", sha).stdout.split()
        self.assertNotIn("mods/x/hooks/register.ts", files)
        self.assertIn("b 편집", git(self.a, "show", "%s:CLAUDE.md" % sha).stdout)

    def test_approve_merges(self):
        s = load_state(self.a)
        s.require_approval = True
        save_state(self.a, s)
        b = self._b()
        write(os.path.join(b.shared, "mods", "x", "hooks", "register.ts"), "export {}\n")
        run_sync(b, auto=False, now=NOW + 1)
        run_sync(self.a, auto=False, now=NOW + 2)
        s = actions.approve(self.a)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertTrue(os.path.islink(os.path.join(self.a.mods, "x")))

    def test_revert_creates_new_commit(self):
        write(self.a.shared_claude_md(), "공용 v2\n")
        run_sync(self.a, auto=False, now=NOW + 1)
        sha = head(self.a)
        s = actions.revert(self.a, sha)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(remote_file(self.remote, "CLAUDE.md"), "공용 v1\n")
        self.assertNotEqual(head(self.a), sha)

    def test_discard_blocked_resets_unpushed(self):
        b = self._b()
        save_words(b, ["corp-host"])
        write(b.shared_claude_md(), "공용 v1\ncorp-host\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "blocked")
        s = actions.discard_blocked(b)
        self.assertEqual(s.last_result, "ok", s.last_error)
        with open(b.shared_claude_md(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "공용 v1\n")
        self.assertEqual(s.blocked, [])

    def test_log_lists_pc_and_files(self):
        b = self._b()
        write(b.shared_claude_md(), "공용 v1\nb\n")
        run_sync(b, auto=False, now=NOW + 1)
        entries = report.log(b, 5)
        self.assertEqual(entries[0]["pc"], "pc-b")
        self.assertEqual(entries[0]["files"], ["CLAUDE.md"])

    def test_diff_shows_incoming(self):
        b = self._b()
        write(self.a.shared_claude_md(), "공용 v1\na 추가\n")
        run_sync(self.a, auto=False, now=NOW + 1)
        git(b, "fetch", "-q", "origin")
        self.assertIn("+a 추가", report.diff(b, "claude_md")["text"])


class ModeTest(World):
    def test_mode_receive_only_round_trip_and_status(self):
        self.assertFalse(report.status(self.a)["receive_only"])
        actions.set_sync_mode(self.a, "receive-only")
        self.assertTrue(load_state(self.a).receive_only)
        self.assertTrue(report.status(self.a)["receive_only"])
        actions.set_sync_mode(self.a, "normal")
        self.assertFalse(load_state(self.a).receive_only)
        with self.assertRaises(actions.ActionError):
            actions.set_sync_mode(self.a, "sideways")

    def test_receive_only_keep_does_not_touch_shared_settings(self):
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        actions.set_sync_mode(b, "receive-only")
        edit_settings(b, theme="light")
        run_sync(b, auto=False, now=NOW + 1)
        edit_settings(self.a, theme="solarized")
        run_sync(self.a, auto=False, now=NOW + 2)
        s = run_sync(b, auto=False, now=NOW + 3)
        self.assertEqual(s.last_result, "conflict")
        s = actions.resolve(b, "setting:theme", "keep")
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(b.settings)["theme"], "light")
        self.assertEqual(read_json(b.shared_settings())["theme"], "solarized")
        s = run_sync(b, auto=False, now=NOW + 4)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(b.settings)["theme"], "light")

    def test_receive_only_keep_after_remote_deletion_then_readd_conflicts(self):
        def overrides(p, **changes):
            data = read_json(p.settings)
            data.setdefault("skillOverrides", {}).update(changes)
            for name in [k for k, v in changes.items() if v is None]:
                data["skillOverrides"].pop(name)
            with open(p.settings, "w") as f:
                json.dump(data, f)

        overrides(self.a, brand="on")
        run_sync(self.a, auto=False, now=NOW + 1)
        b = join(make_home(self.root, "b"), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW + 2)
        actions.set_sync_mode(b, "receive-only")
        overrides(b, brand="off")
        run_sync(b, auto=False, now=NOW + 3)
        overrides(self.a, brand=None)
        run_sync(self.a, auto=False, now=NOW + 4)
        self.assertNotIn("brand", read_json(self.a.shared_settings()).get("skillOverrides", {}))
        s = run_sync(b, auto=False, now=NOW + 5)
        self.assertEqual(s.last_result, "conflict")
        self.assertIsNone(s.conflicts[0]["remote"])
        s = actions.resolve(b, "setting:skillOverrides/brand", "keep")
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(run_sync(b, auto=False, now=NOW + 6).last_result, "ok")
        self.assertEqual(read_json(b.settings)["skillOverrides"]["brand"], "off")
        overrides(self.a, brand="on")
        run_sync(self.a, auto=False, now=NOW + 7)
        s = run_sync(b, auto=False, now=NOW + 8)
        self.assertEqual(s.last_result, "conflict", "a re-added remote value must not overwrite the kept one")
        self.assertEqual(read_json(b.settings)["skillOverrides"]["brand"], "off")

    def test_receive_only_refuses_sharing(self):
        b = join(make_home(self.root, "b", skills=["notes"]), self.remote, "pc-b")
        run_sync(b, auto=False, now=NOW)
        actions.set_sync_mode(b, "receive-only")
        with self.assertRaises(actions.ActionError):
            actions.share(b, "skills", "notes")


class RegisterTest(World):
    def test_register_pc_rejects_bad_key(self):
        for bad in ["ssh-rsa AAAA x", "ssh-ed25519 AAAA\nrm -rf /", "", "ssh-ed25519 AA$A"]:
            with self.assertRaises(actions.ActionError):
                actions.register_pc(bad, runner=lambda *a, **k: self.fail("ran"))

    def test_register_pc_runs_admin_helper(self):
        calls = []

        def runner(argv, **kw):
            calls.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, "added\n", "")

        actions.register_pc(KEY, admin_host="split", runner=runner)
        argv, stdin = calls[0]
        self.assertEqual(argv[-3:], ["split", "sudo", "/usr/local/sbin/claudesync-add-key"])
        self.assertEqual(stdin, KEY + "\n")


class CliTest(World):
    def _cli(self, *args):
        return subprocess.run([sys.executable, "-I", MAIN, "--home", self.a.home] + list(args),
                              capture_output=True, text=True)

    def test_cli_status_is_json(self):
        r = self._cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["pc"], "pc-a")

    def test_cli_errors_are_json(self):
        r = self._cli("share", "skills", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))
