from __future__ import annotations

import os
import unittest

import helpers  # noqa: F401  (puts the engine on sys.path)
from fake_notion import TOKEN, FakeNotion, dashed

from claude_sync import notion
from claude_sync.notion import Client, NotionError, page_id

PROPS = {
    "요약": {"title": {}},
    "PC": {"rich_text": {}},
    "종류": {"select": {"options": [{"name": "전체본"}, {"name": "변경분"}]}},
    "묶음": {"files": {}},
}


class PageIdTest(unittest.TestCase):
    def test_page_id_from_url_dashed_or_plain(self):
        h = "0123456789abcdef0123456789abcdef"
        self.assertEqual(page_id("https://www.notion.so/me/Claude-Sync-%s?pvs=4" % h), h)
        self.assertEqual(page_id("https://www.notion.so/%s#abc" % h), h)
        self.assertEqual(page_id(dashed(h)), h)
        self.assertEqual(page_id(h.upper()), h)
        for bad in ("", "https://www.notion.so/no-id-here", "1234"):
            with self.assertRaises(NotionError):
                page_id(bad)


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNotion().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.slept = []
        self.client = Client(TOKEN, base=self.fake.base, sleep=self.slept.append)
        self.page = self.fake.add_page()

    def test_sends_bearer_and_version(self):
        self.assertEqual(self.client.children(self.page), [])
        self.assertEqual(self.fake.auth_headers[-1], "Bearer " + TOKEN)

    def test_rate_limit_is_retried_after_waiting(self):
        self.fake.throttle_next = 2
        self.assertEqual(self.client.children(self.page), [])
        self.assertEqual(self.slept, [0, 0])

    def test_rate_limit_gives_up_after_three_retries(self):
        self.fake.throttle_next = 10
        with self.assertRaises(NotionError) as cm:
            self.client.children(self.page)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(len(self.slept), 3)

    def test_wrong_secret_is_401_and_secret_never_in_message(self):
        bad = Client("ntn_wrong_secret_value", base=self.fake.base, sleep=self.slept.append)
        with self.assertRaises(NotionError) as cm:
            bad.children(self.page)
        self.assertEqual(cm.exception.status, 401)
        self.assertNotIn("ntn_wrong_secret_value", str(cm.exception))

    def test_secret_with_control_characters_is_refused_without_echoing_it(self):
        for bad in ("ntn_example_secret\rbroken", "ntn_example secret", "ntn_\x00x", "ntn_é"):
            with self.assertRaises(NotionError) as cm:
                Client(bad, base=self.fake.base, sleep=self.slept.append).children(self.page)
            self.assertEqual(cm.exception.code, "bad_secret")
            self.assertNotIn(bad, str(cm.exception))
        self.assertEqual(len(self.fake.auth_headers), 0, "nothing was sent")

    def test_secret_is_masked_before_long_messages_are_cut(self):
        for pad in range(256, 281, 4):  # the key straddles the 300-character cut
            self.fake.fail_next = (400, {"object": "error", "code": "validation_error",
                                         "message": "x" * pad + " got " + TOKEN + " back"})
            with self.assertRaises(NotionError) as cm:
                self.client.children(self.page)
            self.assertNotIn(TOKEN[:6], str(cm.exception), pad)

    def test_unreachable_server_is_status_zero(self):
        client = Client(TOKEN, base="http://127.0.0.1:9", sleep=self.slept.append)
        with self.assertRaises(NotionError) as cm:
            client.children(self.page)
        self.assertEqual(cm.exception.status, 0)

    def test_database_rows_and_files_round_trip(self):
        db, ds = self.client.create_database(self.page, "Claude Sync", PROPS)
        children = self.client.children(self.page)
        self.assertEqual(children[0]["type"], "child_database")
        self.assertEqual(self.client.data_source_of(db), ds)
        payload = bytes(range(256)) * 50 + b"\r\n--tail\r\n"
        up = self.client.upload("part-001.gz", payload)
        self.client.create_row(ds, {
            "요약": {"title": [{"text": {"content": "지침"}}]},
            "PC": {"rich_text": [{"text": {"content": "pc-a"}}]},
            "종류": {"select": {"name": "전체본"}},
            "묶음": {"files": [{"type": "file_upload", "file_upload": {"id": up}, "name": "part-001.gz"}]},
        })
        rows = self.client.query(ds)
        self.assertEqual(len(rows), 1)
        self.assertEqual(notion.text(rows[0], "요약"), "지침")
        self.assertEqual(notion.text(rows[0], "PC"), "pc-a")
        self.assertEqual(notion.select(rows[0], "종류"), "전체본")
        files = notion.files(rows[0], "묶음")
        self.assertEqual([f["name"] for f in files], ["part-001.gz"])
        before = len(self.fake.auth_headers)
        self.assertEqual(self.client.download(files[0]["url"]), payload)
        self.assertEqual(len(self.fake.auth_headers), before, "file urls are presigned: no Authorization header")

    def test_query_pages_through_all_rows_in_both_directions(self):
        _, ds = self.client.create_database(self.page, "Claude Sync", PROPS)
        for i in range(7):
            self.client.create_row(ds, {"요약": {"title": [{"text": {"content": "r%d" % i}}]}})
        rows = self.client.query(ds, page_size=3)
        self.assertEqual(len(rows), 7)
        self.assertEqual(len({r["id"] for r in rows}), 7)
        times = [r["created_time"] for r in rows]
        self.assertEqual(times, sorted(times))
        newest = self.client.query(ds, descending=True, page_size=2, limit=3)
        self.assertEqual(len(newest), 3)
        self.assertEqual(newest[0]["created_time"], max(times))

    def test_upload_over_the_plan_limit_is_refused(self):
        self.fake.max_file_bytes = 1000
        with self.assertRaises(NotionError) as cm:
            self.client.upload("part-001.gz", b"x" * 1001)
        self.assertEqual(cm.exception.status, 400)

    def test_upload_name_without_a_supported_extension_is_refused(self):
        with self.assertRaises(NotionError) as cm:
            self.client.upload("part-001", b"x")
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("extension", str(cm.exception))

    def test_default_base_is_notion_and_env_overrides_for_tests(self):
        old = os.environ.pop("CLAUDE_SYNC_NOTION_API", None)
        try:
            self.assertEqual(Client(TOKEN).base, "https://api.notion.com")
            os.environ["CLAUDE_SYNC_NOTION_API"] = self.fake.base
            self.assertEqual(Client(TOKEN).base, self.fake.base)
        finally:
            os.environ.pop("CLAUDE_SYNC_NOTION_API", None)
            if old is not None:
                os.environ["CLAUDE_SYNC_NOTION_API"] = old


if __name__ == "__main__":
    unittest.main()
