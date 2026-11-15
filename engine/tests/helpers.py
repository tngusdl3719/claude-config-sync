"""Temp homes and a bare remote, shaped like the real Macs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# The tests read the engine's and the installer's Korean messages; a test that wants English asks for it.
os.environ["CLAUDE_SYNC_LANG"] = "ko"

from claude_sync.paths import Paths  # noqa: E402

ORCA_HOOK = {"type": "command", "command": "orca-hook.sh", "timeout": 10}

PERSONAL_SETTINGS = {
    "env": {
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "85",
    },
    "permissions": {"defaultMode": "auto"},
    "skillOverrides": {"ui-ux-pro-max": "on", "design": "off"},
    "hooks": {"Stop": [{"hooks": [ORCA_HOOK]}]},
    "statusLine": {"type": "command", "command": "orca-statusline.sh"},
    "enabledPlugins": {"superpowers@claude-plugins-official": True},
    "effortLevel": "xhigh",
    "theme": "dark",
    "autoMode": {"environment": ["personal"]},
}


def run(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_home(root, name, settings=None, claude_md="# notes\n", skills=(), mods=(), plugin_dirs=None):
    home = os.path.join(root, name)
    p = Paths(home)
    os.makedirs(p.skills, exist_ok=True)
    os.makedirs(p.mods, exist_ok=True)
    data = json.loads(json.dumps(settings if settings is not None else PERSONAL_SETTINGS))
    if plugin_dirs is not None:
        data.setdefault("env", {})["CLAUDE_CODE_PLUGIN_DIRS"] = plugin_dirs
    write(p.settings, json.dumps(data, indent=2) + "\n")
    write(p.claude_md, claude_md)
    for skill in skills:
        write(os.path.join(p.skills, skill, "SKILL.md"), "---\nname: %s\n---\nbody\n" % skill)
    for mod in mods:
        write(os.path.join(p.mods, mod, "hooks", "register.ts"), "export const register = () => {}\n")
    os.makedirs(os.path.join(p.skills, "synced"), exist_ok=True)
    return home


def make_remote(root):
    remote = os.path.join(root, "remote.git")
    run(["git", "init", "-q", "--bare", "-b", "main", remote])
    return remote


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
