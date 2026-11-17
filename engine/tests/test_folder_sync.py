from __future__ import annotations

import json
import os
from unittest import mock

from helpers import TempCase, make_home, make_remote, read_json, write
from test_cli_backends import call as cli_call

from claude_sync import folder_store, rowstore
from claude_sync.actions import ActionError
from claude_sync.gitio import git, head
from claude_sync.paths import Paths
from claude_sync.setup import SetupError, init, join, use_folder
from claude_sync.state import load_state, save_state
from claude_sync.sync import run_sync

NOW = 1_800_000_000.0


def edit_settings(p, **changes):
    data = read_json(p.settings)
    data.update(changes)
    with open(p.settings, "w") as f:
        json.dump(data, f, indent=2)


def steady_clock(case):
    """Each file a second after the last, so their order is the order they were written."""
    ticks = iter(range(10 ** 6))
    patch = mock.patch.object(folder_store, "clock", lambda: NOW + next(ticks))
    patch.start()
    case.addCleanup(patch.stop)


class FolderWorld(TempCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        steady_clock(self)
        self.folder = os.path.join(self.root, "iCloud Drive", "Claude Sync")
        self.a = Paths(make_home(self.root, "a", skills=["brand"]))
        out = init(self.a, pc="pc-a", skills=["brand"], mods=[], keys=["effortLevel", "theme"], claude_md="move",
                   now=NOW, backend="folder", folder=self.folder)
        self.assertEqual(out["result"], "ok", out)

    def names(self):
        return sorted(n for n in os.listdir(self.folder) if n.endswith(".bundle"))

    def kinds(self):
        return [n.split("-")[1] for n in self.names()]

    def join(self, name, **kw):
        home = make_home(self.root, name, claude_md="# 회사 지침\n")
        out = join(Paths(home), backend="folder", folder=self.folder, pc="pc-" + name, now=NOW, **kw)
        self.assertEqual(out["result"], "ok", out)
        return Paths(home)


class FolderSyncTest(FolderWorld):
    def test_first_pc_makes_the_folder_and_one_full_file(self):
        self.assertEqual(self.kinds(), ["full"])
        self.assertIn(head(self.a), self.names()[0])
        s = load_state(self.a)
        self.assertEqual((s.backend, s.folder_path), ("folder", self.folder))

    def test_two_pcs_round_trip(self):
        b = self.join("b")
        self.assertEqual(read_json(b.settings)["theme"], "dark")
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        edit_settings(b, effortLevel="high")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        self.assertEqual(self.kinds(), ["full", "delta"])
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 2).last_result, "ok")
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")
        self.assertEqual(head(self.a), head(b))

    def test_receive_only_pc_never_writes(self):
        c = self.join("c", receive_only=True)
        before = os.listdir(self.folder)
        edit_settings(c, effortLevel="high")
        run_sync(c, auto=False, now=NOW + 1)
        self.assertEqual(os.listdir(self.folder), before)

    def test_files_are_written_whole_and_never_changed(self):
        first = self.names()[0]
        with open(os.path.join(self.folder, first), "rb") as f:
            data = f.read()
        for i in range(3):
            edit_settings(self.a, effortLevel="v%d" % i)
            self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1 + i).last_result, "ok")
        with open(os.path.join(self.folder, first), "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual([n for n in os.listdir(self.folder) if not n.endswith(".bundle")], [])

    def test_full_file_every_n_and_a_new_pc_reads_from_the_latest_one(self):
        with mock.patch.object(rowstore, "FULL_EVERY", 2):
            for i in range(5):
                edit_settings(self.a, effortLevel="v%d" % i)
                self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1 + i).last_result, "ok")
        self.assertEqual(self.kinds(), ["full", "delta", "delta", "full", "delta", "delta"])
        read = []
        real = folder_store.FolderRows.read

        def counting(store, row, path):
            read.append(row.id)
            return real(store, row, path)

        # The PCs' clocks are a day or more apart from the first file: only the latest full one and after.
        old = self.names()[0]
        os.rename(os.path.join(self.folder, old), os.path.join(self.folder, "20200101T000000Z" + old[16:]))
        with mock.patch.object(folder_store.FolderRows, "read", counting):
            c = self.join("c")
        self.assertEqual(len(read), 3, read)
        self.assertEqual(read_json(c.settings)["effortLevel"], "v4")

    def test_a_clock_that_runs_behind_still_counts_within_a_day(self):
        b = self.join("b")
        edit_settings(b, effortLevel="high")
        with mock.patch.object(folder_store, "clock", lambda: NOW - 3600):
            self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 2).last_result, "ok")
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")

    def test_a_file_that_arrives_after_a_newer_full_one_comes_in_with_its_pcs_next_sync(self):
        b = self.join("b")
        edit_settings(b, effortLevel="high")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 10).last_result, "ok")
        late = [n for n in self.names() if "-delta-" in n][-1]
        away = os.path.join(self.root, late)
        os.rename(os.path.join(self.folder, late), away)  # b was offline: its sync app has not carried it yet
        edit_settings(self.a, theme="light")
        with mock.patch.object(rowstore, "FULL_EVERY", 0), \
                mock.patch.object(folder_store, "clock", lambda: NOW + 3 * 86400):
            self.assertEqual(run_sync(self.a, auto=False, now=NOW + 20).last_result, "ok")
        self.assertEqual(self.kinds()[-1], "full")
        os.rename(away, os.path.join(self.folder, late))  # it arrives, days older than a's full file
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 30).last_result, "ok")
        self.assertNotEqual(read_json(self.a.settings).get("effortLevel"), "high", "the late file itself is not read")
        # b's next sync merges a's full file and sends the result, b's change with it.
        with mock.patch.object(folder_store, "clock", lambda: NOW + 3 * 86400 + 60):
            self.assertEqual(run_sync(b, auto=False, now=NOW + 40).last_result, "ok")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 50).last_result, "ok")
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")
        self.assertEqual(read_json(b.settings)["theme"], "light")
        self.assertEqual(head(self.a), head(b))

    def test_unfinished_foreign_and_damaged_files_are_passed_over(self):
        b = self.join("b")
        edit_settings(self.a, theme="light")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
        newest = os.path.join(self.folder, self.names()[-1])
        with open(newest, "rb") as f:
            data = f.read()
        with open(newest, "wb") as f:  # a sync app still bringing it
            f.write(data[: len(data) // 2])
        for name in (".DS_Store", "notes.txt", ".20261117T000000Z-full-%s.bundle.partial" % ("0" * 40),
                     "20261117T000000Z-delta-%s.bundle" % ("1" * 40)):
            write(os.path.join(self.folder, name), "x")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 2).last_result, "ok")
        self.assertNotEqual(read_json(b.settings).get("theme"), "light")
        with open(newest, "wb") as f:
            f.write(data)  # it has arrived
        self.assertEqual(run_sync(b, auto=False, now=NOW + 3).last_result, "ok")
        self.assertEqual(read_json(b.settings)["theme"], "light")

    def test_concurrent_files_converge(self):
        b = self.join("b")
        edit_settings(self.a, theme="light")
        edit_settings(b, effortLevel="high")
        # Both pass the pre-upload check before either file exists (the race the check cannot close).
        with mock.patch.object(rowstore, "moved_since", return_value=False):
            self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
            with mock.patch.object(folder_store, "fetch", return_value=None):
                self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        deltas = [n for n in self.names() if "-delta-" in n]
        self.assertEqual(len(deltas), 2)
        self.assertEqual(deltas[0][-47:], deltas[1][-47:], "both sit on the same base: two tips")
        for t in range(2, 6):
            run_sync(self.a, auto=False, now=NOW + t)
            run_sync(b, auto=False, now=NOW + t)
        for p in (self.a, b):
            data = read_json(p.settings)
            self.assertEqual((data["theme"], data["effortLevel"]), ("light", "high"))
        self.assertEqual(head(self.a), head(b))

    def test_upload_waits_when_another_pc_uploaded_first(self):
        b = self.join("b")
        edit_settings(b, effortLevel="high")
        real_fetch = folder_store.fetch
        calls = []

        def fetch_then_a_pushes(p, s):
            out = real_fetch(p, s)
            if not calls:
                calls.append(1)
                edit_settings(self.a, theme="light")
                self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
            return out

        with mock.patch.object(folder_store, "fetch", side_effect=fetch_then_a_pushes):
            s = run_sync(b, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("먼저", s.last_error)
        s = run_sync(b, auto=False, now=NOW + 3)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(b.settings)["theme"], "light")

    def test_a_missing_folder_is_offline_and_says_where(self):
        os.rename(self.folder, self.folder + "-gone")
        edit_settings(self.a, theme="light")
        s = run_sync(self.a, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "offline")
        self.assertIn(self.folder, s.last_error)
        self.assertFalse(os.path.exists(self.folder), "a sync never makes the folder again by itself")

    def test_join_before_any_pc_and_a_second_first_pc_are_refused(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        home = make_home(self.root, "b")
        with self.assertRaises(SetupError):
            join(Paths(home), backend="folder", folder=empty, pc="pc-b", now=NOW)
        self.assertFalse(os.path.exists(Paths(home).shared))
        with self.assertRaises(SetupError):
            join(Paths(home), backend="folder", folder=os.path.join(self.root, "nowhere"), pc="pc-b", now=NOW)
        self.assertFalse(os.path.exists(os.path.join(self.root, "nowhere")))
        other = Paths(make_home(self.root, "c"))
        with self.assertRaises(SetupError) as cm:
            init(other, pc="pc-c", skills=[], mods=[], keys=[], now=NOW, backend="folder", folder=self.folder)
        self.assertIn("이미", str(cm.exception))


class FolderMoveTest(TempCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        steady_clock(self)
        self.a = Paths(make_home(self.root, "a"))
        out = init(self.a, remote=make_remote(self.root), pc="pc-a", skills=[], mods=[], keys=["theme"],
                   claude_md="move", now=NOW)
        self.assertEqual(out["result"], "ok", out)
        self.folder = os.path.join(self.root, "Dropbox", "Claude Sync")

    def test_a_git_pc_moves_to_a_folder_and_another_follows(self):
        out = use_folder(self.a, self.folder)
        self.assertEqual((out["backend"], out["folder"]), ("folder", self.folder))
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
        (name,) = os.listdir(self.folder)
        self.assertIn("-full-" + head(self.a), name)
        b = Paths(make_home(self.root, "b"))
        out = join(b, backend="folder", folder=self.folder, pc="pc-b", now=NOW)
        self.assertEqual(out["result"], "ok", out)
        self.assertEqual(head(b), head(self.a))

    def test_a_receive_only_pc_moves_only_where_a_sender_went_first(self):
        s = load_state(self.a)
        s.receive_only = True
        save_state(self.a, s)
        with self.assertRaises(ActionError) as cm:
            use_folder(self.a, self.folder)
        self.assertIn("먼저", str(cm.exception))
        self.assertFalse(os.path.exists(self.folder))
        self.assertEqual(load_state(self.a).backend, "git")

    def test_a_folder_with_another_history_is_refused(self):
        other = Paths(make_home(self.root, "other"))
        out = init(other, pc="pc-o", skills=[], mods=[], keys=[], now=NOW, backend="folder", folder=self.folder)
        self.assertEqual(out["result"], "ok", out)
        with self.assertRaises(ActionError) as cm:
            use_folder(self.a, self.folder)
        self.assertIn("이어지지 않는", str(cm.exception))
        self.assertEqual(load_state(self.a).backend, "git")

    def test_the_cli_moves_and_reports_the_folder(self):
        code, raw, out = cli_call(self.a.home, "set-remote", "--backend", "folder", "--folder", self.folder)
        self.assertEqual(code, 0, out)
        code, raw, out = cli_call(self.a.home, "status")
        self.assertEqual(out["storage"], {"backend": "folder", "folder": self.folder})
        b = make_home(self.root, "b")
        run_sync(self.a, auto=False, now=NOW + 1)
        code, raw, out = cli_call(b, "join", "--backend", "folder", "--folder", self.folder, "--pc", "pc-b")
        self.assertEqual((code, out["result"]), (0, "ok"), out)
