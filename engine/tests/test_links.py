from __future__ import annotations

import os

from helpers import TempCase, make_home, write

from claude_sync.claudemd import IMPORT_LINE, ensure_import
from claude_sync.links import local_only, reconcile, shared_names
from claude_sync.paths import Paths


def shared_skill(p, name):
    write(os.path.join(p.shared, "skills", name, "SKILL.md"), "---\nname: %s\n---\n" % name)


class LinksTest(TempCase):
    def test_creates_links_for_shared(self):
        p = Paths(make_home(self.root, "b"))
        shared_skill(p, "ui-ux-pro-max")
        write(os.path.join(p.shared, "mods", "usage-band", "hooks", "hooks.json"), "{}")
        self.assertEqual(reconcile(p), [])
        link = os.path.join(p.skills, "ui-ux-pro-max")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), os.path.join(p.shared, "skills", "ui-ux-pro-max"))
        self.assertTrue(os.path.islink(os.path.join(p.mods, "usage-band")))
        self.assertEqual(reconcile(p), [])

    def test_link_collision_skips_real_dir(self):
        p = Paths(make_home(self.root, "b", skills=["brand"]))
        shared_skill(p, "brand")
        collisions = reconcile(p)
        self.assertEqual(collisions, [{"kind": "skills", "name": "brand", "reason": "exists"}])
        self.assertFalse(os.path.islink(os.path.join(p.skills, "brand")))
        with open(os.path.join(p.skills, "brand", "SKILL.md")) as f:
            self.assertIn("body", f.read())

    def test_foreign_link_is_a_collision(self):
        p = Paths(make_home(self.root, "b"))
        shared_skill(p, "brand")
        os.symlink("/opt/elsewhere/brand", os.path.join(p.skills, "brand"))
        self.assertEqual(reconcile(p), [{"kind": "skills", "name": "brand", "reason": "exists"}])
        self.assertEqual(os.readlink(os.path.join(p.skills, "brand")), "/opt/elsewhere/brand")

    def test_removes_dangling_shared_links_only(self):
        p = Paths(make_home(self.root, "b"))
        os.makedirs(os.path.join(p.shared, "skills"))
        os.symlink(os.path.join(p.shared, "skills", "gone"), os.path.join(p.skills, "gone"))
        os.symlink("/opt/elsewhere/also-gone", os.path.join(p.skills, "foreign"))
        reconcile(p)
        self.assertFalse(os.path.lexists(os.path.join(p.skills, "gone")))
        self.assertTrue(os.path.lexists(os.path.join(p.skills, "foreign")))

    def test_shared_names_sorted_dirs_only(self):
        p = Paths(make_home(self.root, "b"))
        shared_skill(p, "b-skill")
        shared_skill(p, "a-skill")
        write(os.path.join(p.shared, "skills", "README"), "x")
        self.assertEqual(shared_names(p, "skills"), ["a-skill", "b-skill"])
        self.assertEqual(shared_names(p, "mods"), [])

    def test_local_only_excludes_synced_and_links(self):
        p = Paths(make_home(self.root, "b", skills=["corp-x"]))
        shared_skill(p, "brand")
        reconcile(p)
        os.makedirs(os.path.join(p.skills, ".hidden"))
        self.assertEqual(local_only(p, "skills"), ["corp-x"])


class ClaudeMdTest(TempCase):
    def test_ensure_import_idempotent_and_keeps_body(self):
        p = Paths(make_home(self.root, "b", claude_md="# 회사 지침\n내부 규칙\n"))
        self.assertTrue(ensure_import(p))
        self.assertFalse(ensure_import(p))
        with open(p.claude_md, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text, IMPORT_LINE + "\n\n# 회사 지침\n내부 규칙\n")

    def test_ensure_import_creates_missing_file(self):
        p = Paths(make_home(self.root, "b"))
        os.remove(p.claude_md)
        self.assertTrue(ensure_import(p))
        with open(p.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n")
