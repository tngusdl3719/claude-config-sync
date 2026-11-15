from __future__ import annotations

import os
import re
import shutil
from unittest import mock

from helpers import HERE, TempCase, write

import claude_sync
from claude_sync import actions
from claude_sync.lock import Lock
from claude_sync.paths import Paths
from claude_sync.state import State, save_state
from test_cli_backends import call

APP_INFO = os.path.join(os.path.dirname(os.path.dirname(HERE)), "app", "Sources", "ClaudeSyncCore", "AppInfo.swift")


def _engine(root, version=None, extra=()):
    """An engine folder laid out like mods/config-sync (and the app's Resources/engine)."""
    write(os.path.join(root, "bin", "claude-sync"), "#!/bin/sh\n")
    os.chmod(os.path.join(root, "bin", "claude-sync"), 0o755)
    write(os.path.join(root, ".claude-plugin", "plugin.json"), "{}\n")
    init = '"""engine"""\n' + ('__version__ = "%s"\n' % version if version else "")
    write(os.path.join(root, "engine", "claude_sync", "__init__.py"), init)
    for name in extra:
        write(os.path.join(root, name), name + "\n")
    return root


def _files(root):
    out = set()
    for d, _, names in os.walk(root):
        out |= {os.path.relpath(os.path.join(d, n), root) for n in names}
    return out


class AdoptEngineTest(TempCase):
    def setUp(self):
        super().setUp()
        self.p = Paths(os.path.join(self.root, "home"))
        self.target = os.path.join(self.p.shared, "mods", "config-sync")
        _engine(self.target, None, ["hooks/old.ts", ".claude-plugin/types/claude-code/index.d.ts"])

    def test_the_app_and_the_engine_share_one_version(self):
        app = re.search(r'version = "([^"]+)"', open(APP_INFO, encoding="utf-8").read()).group(1)
        self.assertEqual(claude_sync.__version__, app)

    def test_a_newer_engine_replaces_the_shared_one_and_keeps_generated_types(self):
        src = _engine(os.path.join(self.root, "app-engine"), "9.9.9", ["hooks/new.ts"])
        out = actions.adopt_engine(self.p, src)
        self.assertEqual(out, {"adopted": True, "from": None, "to": "9.9.9"})
        self.assertEqual(_files(self.target), _files(src) | {".claude-plugin/types/claude-code/index.d.ts"})
        self.assertTrue(os.access(os.path.join(self.target, "bin", "claude-sync"), os.X_OK))

    def test_a_copy_cut_short_keeps_the_old_version_so_it_is_tried_again(self):
        src = _engine(os.path.join(self.root, "app-engine"), "2.0.0",
                      ["hooks/a.ts", "engine/claude_sync/sync.py", "engine/claude_sync/zz.py", "engine/main.py", "types/x.d.ts"])
        real_copy = shutil.copy2
        for cut in range(1, len(_files(src)) + 1):  # the disk fills up at each file in turn
            _engine(self.target, "1.0.0")
            calls = []

            def failing_copy(a, b):
                calls.append(a)
                if len(calls) == cut:
                    raise OSError("disk full")
                return real_copy(a, b)
            with mock.patch("claude_sync.actions.shutil.copy2", failing_copy):
                with self.assertRaises(OSError):
                    actions.adopt_engine(self.p, src)
            self.assertEqual(actions.engine_version(self.target), "1.0.0", "cut at file %d: the version goes last" % cut)
        self.assertEqual(actions.adopt_engine(self.p, src)["adopted"], True)
        self.assertEqual(_files(self.target), _files(src) | {".claude-plugin/types/claude-code/index.d.ts"})

    def test_the_same_or_an_older_engine_changes_nothing(self):
        _engine(self.target, "1.2.0")
        before = _files(self.target)
        for version in ("1.2.0", "1.1.9", "0.10.0"):
            src = _engine(os.path.join(self.root, "app-" + version), version, ["hooks/new.ts"])
            self.assertEqual(actions.adopt_engine(self.p, src), {"adopted": False, "from": "1.2.0", "to": version})
        self.assertEqual(_files(self.target), before)
        newer = _engine(os.path.join(self.root, "app-1.10.0"), "1.10.0")
        self.assertTrue(actions.adopt_engine(self.p, newer)["adopted"], "1.10.0 is newer than 1.2.0")

    def test_a_receive_only_pc_never_changes_the_shared_engine(self):
        save_state(self.p, State(receive_only=True))
        before = _files(self.target)
        src = _engine(os.path.join(self.root, "app-engine"), "9.9.9", ["hooks/new.ts"])
        self.assertEqual(actions.adopt_engine(self.p, src),
                         {"adopted": False, "from": None, "to": "9.9.9", "reason": "receive_only"})
        self.assertEqual(_files(self.target), before)

    def test_it_waits_for_no_sync(self):
        lock = Lock(self.p)
        self.assertTrue(lock.acquire(wait=False))
        self.addCleanup(lock.release)
        src = _engine(os.path.join(self.root, "app-engine"), "9.9.9")
        with self.assertRaises(actions.ActionError):
            actions.adopt_engine(self.p, src, wait_seconds=0.2)

    def test_a_folder_that_is_not_an_engine_is_refused(self):
        os.makedirs(os.path.join(self.root, "empty"))
        with self.assertRaises(actions.ActionError):
            actions.adopt_engine(self.p, os.path.join(self.root, "empty"))
        self.assertIn("hooks/old.ts", _files(self.target))

    def test_from_the_command_line(self):
        src = _engine(os.path.join(self.root, "app-engine"), "9.9.9")
        code, _, out = call(self.p.home, "adopt-engine", src)
        self.assertEqual((code, out), (0, {"adopted": True, "from": None, "to": "9.9.9"}))
