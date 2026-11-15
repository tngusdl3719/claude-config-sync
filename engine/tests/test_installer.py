from __future__ import annotations

import json
import os
import subprocess
import time

from helpers import HERE, TempCase, make_home, make_remote, read_json, write

from claude_sync.claudemd import IMPORT_LINE
from claude_sync.paths import Paths
from claude_sync.setup import init
from claude_sync.sync import run_sync

ROOT = os.path.dirname(os.path.dirname(HERE))
INSTALLER = os.path.join(ROOT, "installer", "claude-sync-install.command")
DEPLOY = os.path.join(ROOT, "scripts", "deploy.sh")


class InstallerTest(TempCase):
    def test_installer_against_local_remote(self):
        remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a", skills=["brand"], mods=["usage-band"]))
        init(a, remote=remote, pc="pc-a", skills=["brand"], mods=["usage-band"],
             keys=["effortLevel", "theme"], claude_md="move", require_approval=True, now=1.0)
        env = dict(os.environ, DEPLOY_TARGET=os.path.join(a.shared, "mods", "config-sync"))
        subprocess.run(["bash", DEPLOY], check=True, capture_output=True, env=env)
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")

        b_home = make_home(self.root, "b", claude_md="# 회사\n", skills=["corp-x"])
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE=remote, CLAUDE_SYNC_NONINTERACTIVE="1",
                   CLAUDE_SYNC_SKIP_SSH="1", CLAUDE_SYNC_PC="pc-b", CLAUDE_SYNC_WORDS="corp,internal-host")
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("/sync", r.stdout)
        b = Paths(b_home)
        engine_dir = os.path.join(b.shared, "mods", "config-sync", "engine")
        caches = [d for d, _, _ in os.walk(engine_dir) if d.endswith("__pycache__")]
        self.assertEqual(caches, [], "engine wrote bytecode into the shared repo")
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        self.assertTrue(os.path.islink(os.path.join(b.mods, "config-sync")))
        self.assertFalse(os.path.islink(os.path.join(b.skills, "corp-x")))
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n\n# 회사\n")
        with open(b.blocklist_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "corp\ninternal-host\n")
        dirs = read_json(b.settings)["env"]["CLAUDE_CODE_PLUGIN_DIRS"].split(":")
        self.assertEqual(dirs[0], os.path.join(b.mods, "config-sync"))

    def test_installer_rerun_is_safe(self):
        remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a"))
        init(a, remote=remote, pc="pc-a", skills=[], mods=[], keys=["theme"], claude_md="move", now=1.0)
        subprocess.run(["bash", DEPLOY], check=True, capture_output=True,
                       env=dict(os.environ, DEPLOY_TARGET=os.path.join(a.shared, "mods", "config-sync")))
        run_sync(a, auto=False, now=2.0)
        b_home = make_home(self.root, "b")
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE=remote, CLAUDE_SYNC_NONINTERACTIVE="1",
                   CLAUDE_SYNC_SKIP_SSH="1", CLAUDE_SYNC_PC="pc-b", CLAUDE_SYNC_WORDS="")
        for _ in range(2):
            r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(Paths(b_home).claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read().count(IMPORT_LINE), 1)

    def test_installer_refuses_foreign_shared_repo(self):
        remote = make_remote(self.root)
        b_home = make_home(self.root, "b")
        other = make_remote(os.path.join(self.root, "x"))
        subprocess.run(["git", "clone", "-q", other, Paths(b_home).shared], check=True, capture_output=True)
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE=remote, CLAUDE_SYNC_NONINTERACTIVE="1",
                   CLAUDE_SYNC_SKIP_SSH="1", CLAUDE_SYNC_PC="pc-b", CLAUDE_SYNC_WORDS="")
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)


TOKEN = "glpat-INSTALL-secret-7731"
SWITCH = os.path.join(ROOT, "scripts", "switch-remote.sh")
ENGINE_BIN = os.path.join(ROOT, "bin", "claude-sync")


def _fake_helper(root):
    log = os.path.join(root, "helper.log")
    script = os.path.join(root, "fake-helper")
    with open(script, "w") as f:
        f.write('#!/bin/sh\necho "ARGS:$*" >> "%s"\ncat >> "%s"\n' % (log, log))
    os.chmod(script, 0o755)
    return "!" + script, log


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


class HttpsInstallerTest(TempCase):
    def _first_pc(self):
        remote = make_remote(self.root)
        a = Paths(make_home(self.root, "a", skills=["brand"], mods=["usage-band"]))
        init(a, remote=remote, pc="pc-a", skills=["brand"], mods=["usage-band"],
             keys=["effortLevel", "theme"], claude_md="move", require_approval=True, now=1.0)
        subprocess.run(["bash", DEPLOY], check=True, capture_output=True,
                       env=dict(os.environ, DEPLOY_TARGET=os.path.join(a.shared, "mods", "config-sync")))
        self.assertEqual(run_sync(a, auto=False, now=2.0).last_result, "ok")
        return remote, a

    def test_https_mode_skips_ssh_and_keeps_token_out_of_files(self):
        remote, _ = self._first_pc()
        helper, log = _fake_helper(self.root)
        b_home = make_home(self.root, "b", claude_md="# 회사\n", skills=["corp-x"])
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE="file://%s" % remote, CLAUDE_SYNC_TEST_HTTPS_LIKE="1",
                   CLAUDE_SYNC_TOKEN_USER="gitlab+deploy-token-9", CLAUDE_SYNC_TOKEN=TOKEN,
                   CLAUDE_SYNC_CREDENTIAL_HELPER=helper, CLAUDE_SYNC_NONINTERACTIVE="1",
                   CLAUDE_SYNC_PC="pc-b", CLAUDE_SYNC_WORDS="corp", CLAUDE_SYNC_RECEIVE_ONLY="1")
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        b = Paths(b_home)
        self.assertFalse(os.path.exists(os.path.join(b_home, ".ssh", "claude_sync_ed25519")))
        self.assertNotIn("claude-sync-server", _read(os.path.join(b_home, ".ssh", "config")))
        self.assertIn("password=%s" % TOKEN, _read(log).splitlines())
        self.assertNotIn(TOKEN, _read(os.path.join(b.shared, ".git", "config")))
        origin = subprocess.run(["git", "-C", b.shared, "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(origin, "file://%s" % remote)
        with open(os.path.join(b.sync_dir, "state.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["receive_only"])
        helpers = subprocess.run(["git", "-C", b.shared, "config", "--local", "--get-all", "credential.helper"],
                                 capture_output=True, text=True).stdout.split("\n")[:-1]
        self.assertIn(helper[1:], helpers[-1])
        self.assertIn("get|store", helpers[-1])
        self.assertTrue(os.path.islink(os.path.join(b.skills, "brand")))
        with open(b.claude_md, encoding="utf-8") as f:
            self.assertEqual(f.read(), IMPORT_LINE + "\n\n# 회사\n")

    def test_ssh_probe_gives_up_after_its_limit(self):
        b_home = make_home(self.root, "b")
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE="nohost:claude-config.git",
                   CLAUDE_SYNC_SSH_COMMAND="sleep 30 #", CLAUDE_SYNC_PROBE_SECONDS="2",
                   CLAUDE_SYNC_SKIP_SSH="1", CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_PC="pc-b")
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("서버 연결 확인 중", r.stdout)
        self.assertIn("https 주소로", r.stderr)

    def _ssh_install(self, remote, **extra):
        b_home = make_home(self.root, "b")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SYNC_SSH_HOST"}
        env.update(HOME=b_home, CLAUDE_SYNC_REMOTE=remote, CLAUDE_SYNC_SSH_COMMAND="false #",
                   CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_PC="pc-b", **extra)
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env, timeout=60)
        config = os.path.join(b_home, ".ssh", "config")
        return r, _read(config) if os.path.exists(config) else ""

    def test_the_sync_server_address_comes_from_the_person(self):
        r, config = self._ssh_install("claude-sync-server:claude-config.git", CLAUDE_SYNC_SSH_HOST="192.0.2.7")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("서버에 연결할 수 없습니다", r.stderr)
        self.assertIn("Host claude-sync-server\n  HostName 192.0.2.7\n", config)

    def test_no_sync_server_address_stops_before_writing_the_ssh_config(self):
        r, config = self._ssh_install("claude-sync-server:claude-config.git")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CLAUDE_SYNC_SSH_HOST", r.stderr)
        self.assertNotIn("claude-sync-server", config)
        self.assertNotIn("HostName", config)

    def test_other_ssh_addresses_leave_the_ssh_config_alone(self):
        r, config = self._ssh_install("git@github.com:me/claude-config.git")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("서버에 연결할 수 없습니다", r.stderr)
        self.assertNotIn("claude-sync-server", config)

    def test_probe_ends_even_when_the_transport_ignores_sigterm(self):
        b_home = make_home(self.root, "b")
        marker = "31.%d" % os.getpid()
        stubborn = "bash -c 'trap \"\" TERM; sleep %s; sleep %s' #" % (marker, marker)
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_REMOTE="nohost:claude-config.git",
                   CLAUDE_SYNC_SSH_COMMAND=stubborn, CLAUDE_SYNC_PROBE_SECONDS="2",
                   CLAUDE_SYNC_SKIP_SSH="1", CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_PC="pc-b")
        started = time.time()
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env, timeout=25)
        self.assertNotEqual(r.returncode, 0)
        self.assertLess(time.time() - started, 12)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep %s" % marker], capture_output=True, text=True).stdout.strip()
        subprocess.run(["pkill", "-f", "sleep %s" % marker])
        self.assertEqual(left, "", "probe left its transport running")

    def test_https_clone_has_a_deadline(self):
        remote, _ = self._first_pc()
        helper, _ = _fake_helper(self.root)
        marker = "29.%d" % os.getpid()
        fakebin = os.path.join(self.root, "fakebin")
        os.makedirs(fakebin)
        real_git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        write(os.path.join(fakebin, "git"),
              '#!/bin/bash\nfor a in "$@"; do [ "$a" = clone ] && exec sleep %s; done\nexec "%s" "$@"\n' % (marker, real_git))
        os.chmod(os.path.join(fakebin, "git"), 0o755)
        b_home = make_home(self.root, "b")
        env = dict(os.environ, HOME=b_home, PATH=fakebin + ":" + os.environ["PATH"],
                   CLAUDE_SYNC_REMOTE="file://%s" % remote, CLAUDE_SYNC_TEST_HTTPS_LIKE="1",
                   CLAUDE_SYNC_TOKEN_USER="pc-b", CLAUDE_SYNC_TOKEN=TOKEN, CLAUDE_SYNC_CREDENTIAL_HELPER=helper,
                   CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_PC="pc-b", CLAUDE_SYNC_WORDS="",
                   CLAUDE_SYNC_CLONE_SECONDS="2")
        started = time.time()
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env, timeout=40)
        self.assertNotEqual(r.returncode, 0)
        self.assertLess(time.time() - started, 15)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep %s" % marker], capture_output=True, text=True).stdout.strip()
        subprocess.run(["pkill", "-f", "sleep %s" % marker])
        self.assertEqual(left, "")

    def test_address_is_required(self):
        b_home = make_home(self.root, "b")
        env = dict(os.environ, HOME=b_home, CLAUDE_SYNC_NONINTERACTIVE="1", CLAUDE_SYNC_PC="pc-b",
                   CLAUDE_SYNC_SKIP_SSH="1")
        env.pop("CLAUDE_SYNC_REMOTE", None)
        r = subprocess.run(["bash", INSTALLER], capture_output=True, text=True, env=env, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("주소", r.stderr)

    def test_switch_remote_moves_history(self):
        _, a = self._first_pc()
        target = make_remote(os.path.join(self.root, "gitlab"))
        helper, log = _fake_helper(self.root)
        env = dict(os.environ, HOME=a.home, CLAUDE_SYNC_TEST_HTTPS_LIKE="1", CLAUDE_SYNC_TOKEN=TOKEN,
                   CLAUDE_SYNC_CREDENTIAL_HELPER=helper, CLAUDE_SYNC_ENGINE=ENGINE_BIN)
        r = subprocess.run(["bash", SWITCH, "file://%s" % target, "gitlab+deploy-token-1"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        origin = subprocess.run(["git", "-C", a.shared, "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(origin, "file://%s" % target)
        local_head = subprocess.run(["git", "-C", a.shared, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        pushed = subprocess.run(["git", "--git-dir", target, "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertEqual(pushed.strip(), local_head.strip())
        self.assertIn("password=%s" % TOKEN, _read(log).splitlines())
        self.assertNotIn(TOKEN, _read(os.path.join(a.shared, ".git", "config")))
