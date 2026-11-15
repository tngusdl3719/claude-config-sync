from __future__ import annotations

import os
import re
import stat
import subprocess

from helpers import HERE, TempCase, make_remote, run, write

ROOT = os.path.dirname(os.path.dirname(HERE))
FORCED = os.path.join(ROOT, "server", "claudesync-git")
SETUP = os.path.join(ROOT, "server", "setup.sh")
GIT_SHELL = os.path.join(subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip(), "git-shell")


def forced_command_name():
    with open(SETUP, encoding="utf-8") as f:
        return re.search(r'restrict,command="([^"]+)"', f.read()).group(1)


class ForcedCommandTest(TempCase):
    """The claudesync key reaches claude-config.git and nothing else on the server."""

    def setUp(self):
        super().setUp()
        self.repo = make_remote(self.root)
        self.other = make_remote(os.path.join(self.root, "ubuntu"))
        seed = os.path.join(self.root, "seed")
        run(["git", "clone", "-q", self.other, seed])
        write(os.path.join(seed, "secret.txt"), "trading\n")
        run(["git", "-C", seed, "add", "-A"])
        run(["git", "-C", seed, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "x"])
        run(["git", "-C", seed, "push", "-q", "origin", "HEAD:main"])
        # A stand-in for ssh: drops options and the host, then runs the forced command the way sshd would.
        self.fake_ssh = os.path.join(self.root, "fake-ssh")
        # sshd runs a key's forced command through the account's login shell (git-shell),
        # from the account's home, with the client's command in SSH_ORIGINAL_COMMAND.
        self.home = os.path.join(self.root, "claudesync-home")
        os.makedirs(os.path.join(self.home, "git-shell-commands"))
        os.symlink(FORCED, os.path.join(self.home, "git-shell-commands", "claudesync-git"))
        write(self.fake_ssh, '#!/bin/bash\ncd "%s"\nHOME="%s" SSH_ORIGINAL_COMMAND="${@: -1}" CLAUDESYNC_REPO="%s" exec "%s" -c "%s"\n'
              % (self.home, self.home, self.repo, GIT_SHELL, forced_command_name()))
        os.chmod(self.fake_ssh, os.stat(self.fake_ssh).st_mode | stat.S_IEXEC)

    def _git(self, *args):
        env = dict(os.environ, GIT_SSH_COMMAND=self.fake_ssh, GIT_PROTOCOL="")
        return subprocess.run(["git"] + list(args), cwd=self.root, env=env, capture_output=True, text=True)

    def test_clone_and_push_claude_config(self):
        r = self._git("clone", "-q", "server:claude-config.git", "mine")
        self.assertEqual(r.returncode, 0, r.stderr)
        write(os.path.join(self.root, "mine", "CLAUDE.md"), "x\n")
        run(["git", "-C", os.path.join(self.root, "mine"), "add", "-A"])
        run(["git", "-C", os.path.join(self.root, "mine"), "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "x"])
        env = dict(os.environ, GIT_SSH_COMMAND=self.fake_ssh)
        r = subprocess.run(["git", "-C", os.path.join(self.root, "mine"), "push", "-q", "origin", "HEAD:main"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_repos_are_refused(self):
        for path in (self.other, "/home/ubuntu/other-project.git", "../ubuntu/other-project.git", "claude-config.git/../x"):
            r = self._git("clone", "-q", "server:%s" % path, "stolen-%d" % len(path))
            self.assertNotEqual(r.returncode, 0, path)
            self.assertFalse(os.path.exists(os.path.join(self.root, "stolen-%d" % len(path), "secret.txt")))

    def test_shell_commands_are_refused(self):
        r = subprocess.run([FORCED], env=dict(os.environ, SSH_ORIGINAL_COMMAND="sh -c id", CLAUDESYNC_REPO=self.repo),
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run([FORCED], env=dict(os.environ, SSH_ORIGINAL_COMMAND="", CLAUDESYNC_REPO=self.repo),
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
