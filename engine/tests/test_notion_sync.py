from __future__ import annotations

import gzip
import json
import os
import subprocess
from unittest import mock

from helpers import TempCase, make_home, read_json, write
from fake_notion import TOKEN, FakeNotion

from claude_sync import notion_store, rowstore
from claude_sync.claudemd import IMPORT_LINE
from claude_sync.gitio import git, head
from claude_sync.paths import Paths
from claude_sync.setup import SetupError, init, join
from claude_sync.state import load_state
from claude_sync.sync import run_sync

NOW = 1_800_000_000.0


def edit_settings(p, **changes):
    data = read_json(p.settings)
    data.update(changes)
    with open(p.settings, "w") as f:
        json.dump(data, f, indent=2)


class NotionWorld(TempCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNotion().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.creds = os.path.join(self.root, "keychain")
        env = mock.patch.dict(os.environ, {"CLAUDE_SYNC_NOTION_API": self.fake.base,
                                           "CLAUDE_SYNC_CREDENTIAL_HELPER": "store --file=%s" % self.creds,
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.page = self.fake.add_page()
        self.url = "https://www.notion.so/me/Claude-Sync-%s" % self.page
        self.a = Paths(make_home(self.root, "a", skills=["brand"]))
        out = init(self.a, pc="pc-a", skills=["brand"], mods=[], keys=["effortLevel", "theme"], claude_md="move",
                   now=NOW, backend="notion", page=self.url, secret=TOKEN)
        self.assertEqual(out["result"], "ok", out)

    def rows(self):
        (ds,) = self.fake.data_sources.values()
        return [self.fake.rows[r] for r in ds["rows"]]

    def kinds(self):
        return [r["properties"]["종류"]["select"]["name"] for r in self.rows()]

    def join(self, name, **kw):
        home = make_home(self.root, name, claude_md="# 회사 지침\n")
        out = join(Paths(home), backend="notion", page=self.url, secret=TOKEN, pc="pc-" + name, now=NOW, **kw)
        self.assertEqual(out["result"], "ok", out)
        return Paths(home)


class NotionSyncTest(NotionWorld):
    def test_first_pc_creates_database_and_full_row(self):
        self.assertEqual(len(self.fake.databases), 1)
        (db,) = self.fake.databases.values()
        self.assertEqual(db["title"], "Claude Sync")
        self.assertEqual(self.kinds(), ["전체본"])
        row = self.rows()[0]["properties"]
        self.assertEqual(row["PC"]["rich_text"][0]["text"]["content"], "pc-a")
        self.assertEqual(row["Head"]["rich_text"][0]["text"]["content"], head(self.a))
        s = load_state(self.a)
        self.assertEqual((s.backend, s.notion_page_id), ("notion", self.page))

    def test_two_pcs_round_trip(self):
        b = self.join("b")
        self.assertEqual(read_json(b.settings)["theme"], "dark")
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n\n# 회사 지침\n")
        edit_settings(b, effortLevel="high")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        self.assertEqual(self.kinds(), ["전체본", "변경분"])
        s = run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")
        self.assertEqual(head(self.a), head(b))

    def test_receive_only_pc_never_creates_rows(self):
        b = self.join("b", receive_only=True)
        edit_settings(b, theme="light")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        self.assertEqual(len(self.rows()), 1)
        edit_settings(self.a, effortLevel="low")
        run_sync(self.a, auto=False, now=NOW + 2)
        self.assertEqual(run_sync(b, auto=False, now=NOW + 3).last_result, "ok")
        self.assertEqual(read_json(b.settings)["effortLevel"], "low")
        self.assertEqual(len(self.rows()), 2)

    def test_blocked_words_never_reach_notion(self):
        b = self.join("b", words=["corp"])
        write(b.shared_claude_md(), "공용 v1\ncorp 내부 주소\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "blocked")
        self.assertEqual(len(self.rows()), 1)
        write(b.shared_claude_md(), "공용 v2\n")
        self.assertEqual(run_sync(b, auto=False, now=NOW + 2).last_result, "ok")
        self.assertEqual(len(self.rows()), 2)
        run_sync(self.a, auto=False, now=NOW + 3)
        history = git(self.a, "log", "-p", "--all").stdout
        self.assertIn("공용 v2", history)
        self.assertNotIn("corp", history)

    def test_full_row_every_n_rows_and_new_pc_reads_from_the_latest_one(self):
        with mock.patch.object(rowstore, "FULL_EVERY", 2):
            for i in range(5):
                edit_settings(self.a, effortLevel="v%d" % i)
                self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1 + i).last_result, "ok")
            self.assertEqual(self.kinds(), ["전체본", "변경분", "변경분", "전체본", "변경분", "변경분"])
            downloads = len([r for r in self.fake.requests if r.startswith("GET /files/")])
            c = self.join("c")
        fetched = len([r for r in self.fake.requests if r.startswith("GET /files/")]) - downloads
        self.assertEqual(fetched, 3, "the latest full row and the rows after it")
        self.assertEqual(read_json(c.settings)["effortLevel"], "v4")

    def test_large_bundle_is_sent_in_parts(self):
        with mock.patch.object(notion_store, "PART_BYTES", 300):
            write(self.a.shared_claude_md(), "".join("줄 %d\n" % i for i in range(400)))
            self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
        files = self.rows()[-1]["properties"]["묶음"]["files"]
        self.assertGreater(len(files), 2)
        self.assertEqual([f["name"] for f in files], sorted(f["name"] for f in files))
        # Each part is a gzip file of its own, named so the File Upload API accepts it.
        self.assertTrue(all(f["name"].endswith(".gz") for f in files), files)
        self.assertTrue(all(u["data"][:2] == b"\x1f\x8b" for u in self.fake.uploads.values()))
        b = self.join("b")
        with open(b.shared_claude_md(), encoding="utf-8") as f:
            self.assertIn("줄 399", f.read())

    def test_a_damaged_part_is_skipped_not_a_crash(self):
        b = self.join("b")
        edit_settings(self.a, theme="light")
        self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
        url = self.rows()[-1]["properties"]["묶음"]["files"][0]["file"]["url"]
        self.fake.files[url.rsplit("/", 1)[1]] = b"\x1f\x8b" + b"not really gzip"
        self.assertEqual(run_sync(b, auto=False, now=NOW + 2).last_result, "ok")
        self.assertNotEqual(read_json(b.settings).get("theme"), "light")

    def test_part_format_follows_its_name_not_its_first_bytes(self):
        raw = b"\x1f\x8b" + b"the middle of an older raw bundle"
        self.assertEqual(notion_store._part_data("part-002", raw), raw)
        self.assertEqual(notion_store._part_data("part-001.gz", gzip.compress(raw, mtime=0)), raw)

    def test_a_part_that_does_not_compress_still_fits_a_free_workspace(self):
        self.assertLessEqual(len(gzip.compress(os.urandom(notion_store.PART_BYTES), mtime=0)), 5 * 1024 * 1024)

    def test_concurrent_rows_converge(self):
        b = self.join("b")
        edit_settings(self.a, theme="light")
        edit_settings(b, effortLevel="high")
        # Both pass the pre-upload check before either row exists (the race the check cannot close).
        with mock.patch.object(rowstore, "moved_since", return_value=False):
            self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
            with mock.patch.object(notion_store, "fetch", return_value=None):
                self.assertEqual(run_sync(b, auto=False, now=NOW + 1).last_result, "ok")
        bases = [r["properties"]["Base"]["rich_text"] for r in self.rows()[1:]]
        self.assertEqual(len(bases), 2)
        self.assertEqual(bases[0], bases[1], "both rows sit on the same base: two tips")
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
        real_fetch = notion_store.fetch
        calls = []

        def fetch_then_a_pushes(p, s):
            out = real_fetch(p, s)
            if not calls:
                calls.append(1)
                edit_settings(self.a, theme="light")
                self.assertEqual(run_sync(self.a, auto=False, now=NOW + 1).last_result, "ok")
            return out

        with mock.patch.object(notion_store, "fetch", side_effect=fetch_then_a_pushes):
            s = run_sync(b, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("먼저", s.last_error)
        self.assertEqual(len(self.rows()), 2)
        s = run_sync(b, auto=False, now=NOW + 3)
        self.assertEqual(s.last_result, "ok", s.last_error)
        self.assertEqual(read_json(b.settings)["theme"], "light")
        run_sync(self.a, auto=False, now=NOW + 4)
        self.assertEqual(read_json(self.a.settings)["effortLevel"], "high")

    def test_wrong_secret_is_offline_with_a_readable_message(self):
        b = self.join("b")
        with open(self.creds, "w") as f:
            f.write("https://claude-sync:ntn_wrong@api.notion.com/%s\n" % self.page)
        s = run_sync(b, auto=False, now=NOW + 1)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("비밀 키", s.last_error)
        os.remove(self.creds)
        s = run_sync(b, auto=False, now=NOW + 2)
        self.assertEqual(s.last_result, "offline")
        self.assertIn("비밀 키", s.last_error)

    def test_secret_stays_out_of_files(self):
        b = self.join("b")
        run_sync(b, auto=False, now=NOW + 1)
        for p in (self.a, b):
            for path in (os.path.join(p.shared, ".git", "config"), os.path.join(p.sync_dir, "state.json"),
                         os.path.join(p.sync_dir, "log")):
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        self.assertNotIn(TOKEN, f.read(), path)
            leftovers = subprocess.run(["grep", "-rl", TOKEN, p.home], capture_output=True, text=True).stdout
            self.assertEqual(leftovers, "")

    def test_join_before_any_pc_is_refused_and_leaves_nothing(self):
        page = self.fake.add_page()
        home = make_home(self.root, "c")
        with self.assertRaises(SetupError) as cm:
            join(Paths(home), backend="notion", page=page, secret=TOKEN, pc="pc-c", now=NOW)
        self.assertIn("아직 첫 PC", str(cm.exception))
        self.assertFalse(os.path.exists(Paths(home).shared))
