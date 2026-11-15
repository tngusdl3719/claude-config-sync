from __future__ import annotations

import os
import plistlib
import subprocess

from helpers import HERE, TempCase, write

ROOT = os.path.dirname(os.path.dirname(HERE))
MAKE_DMG = os.path.join(ROOT, "scripts", "make-dmg.sh")
BUILD_APP = os.path.join(ROOT, "scripts", "build-app.sh")


def _fake_app(root, name="Fake"):
    app = os.path.join(root, name + ".app")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.example.fake", "CFBundleShortVersionString": "1.2.3"}, f)
    write(os.path.join(app, "Contents", "MacOS", "Fake"), "#!/bin/sh\n")
    return app


class DmgTest(TempCase):
    def test_the_dmg_opens_to_the_app_beside_an_applications_shortcut(self):
        app = _fake_app(self.root)
        dmg = os.path.join(self.root, "out", "Fake-1.2.3.dmg")
        os.makedirs(os.path.dirname(dmg))
        r = subprocess.run(["bash", MAKE_DMG, app, dmg], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        mount = os.path.join(self.root, "mnt")
        os.makedirs(mount)
        subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly", "-mountpoint", mount, dmg], check=True)
        try:
            self.assertEqual(sorted(n for n in os.listdir(mount) if not n.startswith(".")), ["Applications", "Fake.app"])
            self.assertEqual(os.readlink(os.path.join(mount, "Applications")), "/Applications")
            with open(os.path.join(mount, "Fake.app", "Contents", "Info.plist"), "rb") as f:
                self.assertEqual(plistlib.load(f)["CFBundleShortVersionString"], "1.2.3")
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", mount], check=False)

    def test_no_app_no_dmg(self):
        dmg = os.path.join(self.root, "x.dmg")
        r = subprocess.run(["bash", MAKE_DMG, os.path.join(self.root, "none.app"), dmg], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(dmg))

    def test_scripts_can_be_run_directly(self):
        import glob
        scripts = glob.glob(os.path.join(ROOT, "scripts", "*.sh")) + [
            os.path.join(ROOT, "installer", "claude-sync-install.command"),
            os.path.join(ROOT, "app", "Resources", "install-update.sh"),
            os.path.join(ROOT, "bin", "claude-sync"), os.path.join(ROOT, "app", "test.sh")]
        self.assertEqual([os.path.relpath(s, ROOT) for s in scripts if not os.access(s, os.X_OK)], [])

    def test_the_notion_broker_passes_its_own_tests(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("no node on this Mac")
        r = subprocess.run([node, "--test", os.path.join(ROOT, "server", "notion-broker")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])

    def test_the_build_puts_no_secret_in_the_app(self):
        with open(BUILD_APP, encoding="utf-8") as f:
            script = f.read()
        self.assertNotIn("notion-oauth.json", script)
        self.assertNotIn("find-generic-password", script)
        self.assertIn("make-dmg.sh", script)
        self.assertIn("install-update.sh", script, "the app carries the script that swaps updates in")
