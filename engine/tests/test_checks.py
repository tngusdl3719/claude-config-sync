from __future__ import annotations

import json
import os

from helpers import TempCase, make_home, make_remote, run, write

from claude_sync.approval import incoming_requiring_approval, needs_approval_paths, settings_needs_approval
from claude_sync.blocklist import load_words, save_words, scan_outgoing
from claude_sync.gitio import EMPTY_TREE, commit_all, git, head
from claude_sync.paths import Paths


class BlocklistTest(TempCase):
    def setUp(self):
        super().setUp()
        self.p = Paths(make_home(self.root, "b"))
        os.makedirs(self.p.shared)
        run(["git", "init", "-q", "-b", "main", self.p.shared])
        write(os.path.join(self.p.shared, "CLAUDE.md"), "공용 지침\n")
        self.base = commit_all(self.p, "pc-b", "first")

    def test_words_roundtrip_skips_comments(self):
        save_words(self.p, ["corp-host", "Project-Z"])
        with open(self.p.blocklist_file, "a") as f:
            f.write("# comment\n\n")
        self.assertEqual(load_words(self.p), ["corp-host", "Project-Z"])

    def test_scan_finds_word_case_insensitive(self):
        write(os.path.join(self.p.shared, "CLAUDE.md"), "공용 지침\n배포는 internal.CORP-HOST 로\n")
        sha = commit_all(self.p, "pc-b", "edit")
        hits = scan_outgoing(self.p, self.base, sha, ["corp-host"])
        self.assertEqual(hits, [{"file": "CLAUDE.md", "line": 2, "text": "배포는 internal.CORP-HOST 로", "word": "corp-host"}])

    def test_scan_added_line_starting_with_plus_plus(self):
        write(os.path.join(self.p.shared, "CLAUDE.md"), "공용 지침\n++ corp-host\n")
        sha = commit_all(self.p, "pc-b", "edit")
        hits = scan_outgoing(self.p, self.base, sha, ["corp-host"])
        self.assertEqual([h["text"] for h in hits], ["++ corp-host"])

    def test_scan_ignores_removed_lines(self):
        write(os.path.join(self.p.shared, "CLAUDE.md"), "corp-host\n")
        mid = commit_all(self.p, "pc-b", "add")
        write(os.path.join(self.p.shared, "CLAUDE.md"), "clean\n")
        sha = commit_all(self.p, "pc-b", "remove")
        self.assertEqual(scan_outgoing(self.p, mid, sha, ["corp-host"]), [])

    def test_scan_from_empty_tree(self):
        hits = scan_outgoing(self.p, EMPTY_TREE, self.base, ["공용"])
        self.assertEqual([h["file"] for h in hits], ["CLAUDE.md"])

    def test_scan_flags_blocked_word_in_new_filename(self):
        write(os.path.join(self.p.shared, "skills", "internal-company", "SKILL.md"), "harmless\n")
        sha = commit_all(self.p, "pc-b", "new skill")
        hits = scan_outgoing(self.p, self.base, sha, ["Internal-Company"])
        self.assertEqual(hits[0]["file"], "skills/internal-company/SKILL.md")
        self.assertEqual(hits[0]["word"], "Internal-Company")

    def test_binary_file_blocks_push(self):
        os.makedirs(os.path.join(self.p.shared, "skills", "x"))
        with open(os.path.join(self.p.shared, "skills", "x", "logo.png"), "wb") as f:
            f.write(b"\x89PNG\x00\x01\x02")
        sha = commit_all(self.p, "pc-b", "bin")
        hits = scan_outgoing(self.p, self.base, sha, ["anything"])
        self.assertEqual(hits, [{"file": "skills/x/logo.png", "line": 0, "text": "", "word": "(binary)"}])


class ApprovalTest(TempCase):
    def test_md_in_skill_needs_no_approval(self):
        self.assertEqual(needs_approval_paths(["skills/a/SKILL.md", "CLAUDE.md", "settings.json"]), [])

    def test_script_in_skill_and_any_mod_file_need_approval(self):
        files = ["skills/a/scripts/run.py", "mods/usage-band/hooks/register.tsx", "mods/x/README.md"]
        self.assertEqual(needs_approval_paths(files), files)

    def test_enabledplugins_and_defaultmode_need_approval(self):
        old = {"enabledPlugins": {"a@m": True}, "permissions": {"defaultMode": "auto"}, "theme": "dark"}
        new = {"enabledPlugins": {"a@m": False, "b@m": True}, "permissions": {"defaultMode": "bypassPermissions"}, "theme": "light"}
        self.assertEqual(
            settings_needs_approval(old, new),
            [["enabledPlugins", "b@m"], ["permissions", "defaultMode"]],
        )

    def _two_clones(self):
        remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a"))
        b = Paths(make_home(self.root, "b"))
        run(["git", "clone", "-q", remote, a.shared])
        write(os.path.join(a.shared, "CLAUDE.md"), "x\n")
        write(os.path.join(a.shared, "settings.json"), json.dumps({"theme": "dark"}))
        commit_all(a, "pc-a", "first")
        git(a, "push", "-q", "origin", "HEAD:main")
        run(["git", "clone", "-q", remote, b.shared])
        return a, b

    def test_incoming_mod_change_from_other_pc_needs_approval(self):
        a, b = self._two_clones()
        write(os.path.join(b.shared, "mods", "evil", "hooks", "register.ts"), "x")
        write(os.path.join(b.shared, "CLAUDE.md"), "x\ny\n")
        commit_all(b, "pc-b", "mod")
        git(b, "push", "-q", "origin", "HEAD:main")
        git(a, "fetch", "-q", "origin")
        got = incoming_requiring_approval(a, head(a), git(a, "rev-parse", "origin/main").stdout.strip(), "pc-a")
        self.assertEqual(got["files"], ["mods/evil/hooks/register.ts"])
        self.assertEqual([c["pc"] for c in got["commits"]], ["pc-b"])

    def test_spoofed_pc_trailer_still_needs_approval(self):
        a, b = self._two_clones()
        write(os.path.join(b.shared, "mods", "evil", "hooks", "register.ts"), "x")
        commit_all(b, "pc-a", "pretending to be pc-a")
        git(b, "push", "-q", "origin", "HEAD:main")
        git(a, "fetch", "-q", "origin")
        got = incoming_requiring_approval(a, head(a), git(a, "rev-parse", "origin/main").stdout.strip(), "pc-a")
        self.assertIsNotNone(got)
        self.assertEqual(got["files"], ["mods/evil/hooks/register.ts"])

    def test_own_commits_never_need_approval(self):
        a, b = self._two_clones()
        write(os.path.join(a.shared, "mods", "mine", "hooks", "register.ts"), "x")
        commit_all(a, "pc-a", "mod")
        git(a, "push", "-q", "origin", "HEAD:main")
        git(a, "fetch", "-q", "origin")
        self.assertIsNone(incoming_requiring_approval(a, head(a), git(a, "rev-parse", "origin/main").stdout.strip(), "pc-a"))

    def test_md_only_incoming_needs_no_approval(self):
        a, b = self._two_clones()
        write(os.path.join(b.shared, "skills", "s", "SKILL.md"), "doc")
        commit_all(b, "pc-b", "doc")
        git(b, "push", "-q", "origin", "HEAD:main")
        git(a, "fetch", "-q", "origin")
        self.assertIsNone(incoming_requiring_approval(a, head(a), git(a, "rev-parse", "origin/main").stdout.strip(), "pc-a"))
