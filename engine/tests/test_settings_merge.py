from __future__ import annotations

import json
import os

from helpers import PERSONAL_SETTINGS, TempCase, make_home, read_json

from claude_sync.paths import Paths
from claude_sync.settings_merge import (
    apply_patch,
    flatten,
    managed_paths,
    merge,
    plugin_dirs,
    write_settings,
)

SHARED = {
    "effortLevel": "xhigh",
    "theme": "dark",
    "env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "85"},
    "skillOverrides": {"design": "off"},
}
SHARED_SKILLS = {"design", "ui-ux-pro-max"}


def base_of(shared):
    return dict(flatten(shared))


class MergeTest(TempCase):
    def test_local_change_flows_to_shared(self):
        local = dict(PERSONAL_SETTINGS, effortLevel="high")
        paths = managed_paths(SHARED, SHARED_SKILLS, local)
        r = merge(base_of(SHARED), local, SHARED, paths)
        self.assertEqual(r.new_shared["effortLevel"], "high")
        self.assertEqual(r.new_local["effortLevel"], "high")
        self.assertEqual(r.new_base[("effortLevel",)], "high")
        self.assertEqual(r.conflicts, [])

    def test_shared_change_flows_to_local(self):
        shared = dict(SHARED, theme="light")
        paths = managed_paths(shared, SHARED_SKILLS, PERSONAL_SETTINGS)
        r = merge(base_of(SHARED), PERSONAL_SETTINGS, shared, paths)
        self.assertEqual(r.new_local["theme"], "light")
        self.assertEqual(r.local_patch, {("theme",): "light"})

    def test_both_changed_is_conflict_and_nothing_written(self):
        local = dict(PERSONAL_SETTINGS, effortLevel="high")
        shared = dict(SHARED, effortLevel="low")
        paths = managed_paths(shared, SHARED_SKILLS, local)
        r = merge(base_of(SHARED), local, shared, paths)
        self.assertEqual(r.conflicts, [{"path": ["effortLevel"], "local": "high", "remote": "low"}])
        self.assertEqual(r.new_local["effortLevel"], "high")
        self.assertEqual(r.new_shared["effortLevel"], "low")
        self.assertEqual(r.new_base[("effortLevel",)], "xhigh")

    def test_known_deletion_in_base_is_not_a_first_install(self):
        from claude_sync.settings_merge import DELETE, decode_base, encode_base
        stored = json.loads(json.dumps(encode_base({("skillOverrides", "design"): DELETE})))
        base = decode_base(stored)
        self.assertIn(("skillOverrides", "design"), base)
        local = dict(PERSONAL_SETTINGS, skillOverrides={"design": "off"})
        shared = {"skillOverrides": {"design": "on"}}
        r = merge(base, local, shared, {("skillOverrides", "design")})
        self.assertEqual(r.conflicts, [{"path": ["skillOverrides", "design"], "local": "off", "remote": "on"}])
        self.assertEqual(r.local_patch, {})

    def test_remembered_deletion_stays_managed_after_key_leaves_shared(self):
        from claude_sync.settings_merge import DELETE
        base = {("theme",): DELETE, ("skillOverrides", "gone"): DELETE}
        local = dict(PERSONAL_SETTINGS, theme="light", skillOverrides={"gone": "off"})
        paths = managed_paths({}, SHARED_SKILLS, local, base)
        self.assertIn(("theme",), paths)
        self.assertNotIn(("skillOverrides", "gone"), paths, "only shared skills' overrides are managed")
        r = merge(base, local, {"theme": "dark"}, paths)
        self.assertEqual(r.conflicts, [{"path": ["theme"], "local": "light", "remote": "dark"}])
        self.assertEqual(r.local_patch, {})

    def test_first_install_shared_wins_and_reports_overwritten(self):
        local = dict(PERSONAL_SETTINGS, theme="light")
        paths = managed_paths(SHARED, SHARED_SKILLS, local)
        r = merge({}, local, SHARED, paths)
        self.assertEqual(r.new_local["theme"], "dark")
        self.assertIn({"path": ["theme"], "local": "light", "shared": "dark"}, r.overwritten)
        self.assertEqual(r.new_base[("theme",)], "dark")

    def test_company_skilloverride_entry_never_managed(self):
        local = json.loads(json.dumps(PERSONAL_SETTINGS))
        local["skillOverrides"]["corp-x"] = "off"
        paths = managed_paths(SHARED, SHARED_SKILLS, local)
        self.assertNotIn(("skillOverrides", "corp-x"), paths)
        r = merge(base_of(SHARED), local, SHARED, paths)
        self.assertNotIn("corp-x", r.new_shared.get("skillOverrides", {}))
        self.assertEqual(r.new_local["skillOverrides"]["corp-x"], "off")

    def test_shared_skill_override_present_only_locally_is_captured(self):
        paths = managed_paths(SHARED, SHARED_SKILLS, PERSONAL_SETTINGS)
        self.assertIn(("skillOverrides", "ui-ux-pro-max"), paths)
        r = merge(base_of(SHARED), PERSONAL_SETTINGS, SHARED, paths)
        self.assertEqual(r.new_shared["skillOverrides"]["ui-ux-pro-max"], "on")

    def test_unmanaged_keys_untouched(self):
        local = dict(PERSONAL_SETTINGS, effortLevel="high")
        shared = dict(SHARED, hooks={"Stop": []}, statusLine={"type": "x"})
        paths = managed_paths(shared, SHARED_SKILLS, local)
        for never in [("hooks",), ("statusLine",), ("autoMode",), ("skipDangerousModePermissionPrompt",)]:
            self.assertNotIn(never, paths)
        r = merge(base_of(SHARED), local, shared, paths)
        for key in ("hooks", "statusLine", "autoMode"):
            self.assertEqual(r.new_local[key], PERSONAL_SETTINGS[key])

    def test_plugin_dirs_never_managed_as_shared_key(self):
        shared = dict(SHARED, env={"CLAUDE_CODE_PLUGIN_DIRS": "/x"})
        self.assertNotIn(("env", "CLAUDE_CODE_PLUGIN_DIRS"), managed_paths(shared, SHARED_SKILLS, {}))


class PluginDirsTest(TempCase):
    def test_plugin_dirs_keeps_local_extras_and_adds_shared(self):
        home = make_home(self.root, "b", mods=["usage-band", "corp-mod"])
        mods = os.path.join(home, ".claude", "mods")
        current = "%s/corp-mod:/opt/company/plugin" % mods
        got = plugin_dirs(current, home, ["usage-band", "config-sync"])
        self.assertEqual(
            got.split(":"),
            ["%s/config-sync" % mods, "%s/corp-mod" % mods, "/opt/company/plugin", "%s/usage-band" % mods],
        )

    def test_plugin_dirs_puts_config_sync_first(self):
        home = make_home(self.root, "b", mods=["usage-band"])
        mods = os.path.join(home, ".claude", "mods")
        got = plugin_dirs("%s/usage-band" % mods, home, ["config-sync", "usage-band"])
        self.assertEqual(got.split(":"), ["%s/config-sync" % mods, "%s/usage-band" % mods])
        again = plugin_dirs("%s/usage-band:%s/config-sync" % (mods, mods), home, ["config-sync", "usage-band"])
        self.assertEqual(again.split(":"), ["%s/config-sync" % mods, "%s/usage-band" % mods])

    def test_plugin_dirs_keeps_pc_only_config_sync(self):
        home = make_home(self.root, "b", mods=["config-sync", "usage-band"])
        mods = os.path.join(home, ".claude", "mods")
        current = "%s/usage-band:%s/config-sync" % (mods, mods)
        self.assertEqual(plugin_dirs(current, home, ["usage-band"]), current)

    def test_plugin_dirs_accepts_tilde_form(self):
        home = make_home(self.root, "b", mods=["usage-band"])
        got = plugin_dirs("~/.claude/mods/usage-band", home, ["usage-band"])
        self.assertEqual(got, "~/.claude/mods/usage-band")

    def test_plugin_dirs_drops_missing_entries_under_mods_only(self):
        home = make_home(self.root, "b")
        mods = os.path.join(home, ".claude", "mods")
        got = plugin_dirs("%s/gone:/opt/elsewhere/gone" % mods, home, [])
        self.assertEqual(got, "/opt/elsewhere/gone")


class WriteSettingsTest(TempCase):
    def test_apply_preserves_unmanaged_keys_written_concurrently(self):
        p = Paths(make_home(self.root, "a"))
        snapshot = read_json(p.settings)
        shared = dict(SHARED, theme="light")
        r = merge(base_of(SHARED), snapshot, shared, managed_paths(shared, SHARED_SKILLS, snapshot))
        # Orca rewrites the file after our read
        later = read_json(p.settings)
        later["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": "orca-new.sh"}]}]
        with open(p.settings, "w") as f:
            json.dump(later, f, indent=2)
        changed = write_settings(p, lambda d: apply_patch(d, r.local_patch))
        self.assertTrue(changed)
        final = read_json(p.settings)
        self.assertEqual(final["theme"], "light")
        self.assertIn("SessionStart", final["hooks"])
        self.assertEqual(list(final.keys()), list(later.keys()))

    def test_write_settings_noop_returns_false(self):
        p = Paths(make_home(self.root, "a"))
        before = os.stat(p.settings).st_mtime_ns
        self.assertFalse(write_settings(p, lambda d: d))
        self.assertEqual(os.stat(p.settings).st_mtime_ns, before)
