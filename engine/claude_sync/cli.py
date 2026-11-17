"""claude-sync: every command prints one JSON value."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from .lang import t
from . import actions, remote, report
from . import setup as setup_mod
from .paths import Paths
from .sync import run_sync


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="claude-sync")
    ap.add_argument("--home", default=os.environ.get("HOME", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--auto", action="store_true")
    sub.add_parser("status")
    lg = sub.add_parser("log")
    lg.add_argument("-n", type=int, default=30)
    df = sub.add_parser("diff")
    df.add_argument("item")
    for name in ("share", "unshare"):
        sp = sub.add_parser(name)
        sp.add_argument("kind")
        sp.add_argument("name")
        if name == "share":
            sp.add_argument("--force", action="store_true")
    rs = sub.add_parser("resolve")
    rs.add_argument("id")
    rs.add_argument("choice", choices=["keep", "take"])
    for name in ("approve", "reject", "allow-once", "discard-blocked"):
        sub.add_parser(name)
    rv = sub.add_parser("revert")
    rv.add_argument("sha")
    bl = sub.add_parser("block")
    bl.add_argument("op", choices=["list", "add", "remove"])
    bl.add_argument("word", nargs="?")
    md = sub.add_parser("mode")
    md.add_argument("value", choices=["receive-only", "normal"])
    au = sub.add_parser("auto")
    au.add_argument("value", choices=["on", "off"])
    rp = sub.add_parser("register-pc")
    rp.add_argument("key")
    rp.add_argument("--admin-host", default="split")
    ad = sub.add_parser("adopt-engine")
    ad.add_argument("source")
    sr = sub.add_parser("set-remote")
    sr.add_argument("url", nargs="?")
    sr.add_argument("--username")
    sr.add_argument("--backend", choices=["git", "notion", "folder"], default="git")
    sr.add_argument("--page")
    sr.add_argument("--folder")
    sr.add_argument("--oauth", action="store_true")
    setup_mod.add_commands(sub)
    return ap


def _state(s) -> dict:
    return asdict(s)


def run(args) -> object:
    p = Paths(args.home)
    cmd = args.cmd
    if cmd == "sync":
        run_sync(p, auto=args.auto)
        return report.status(p)
    if cmd == "status":
        return report.status(p)
    if cmd == "log":
        return report.log(p, args.n)
    if cmd == "diff":
        return report.diff(p, args.item)
    if cmd == "share":
        return actions.share(p, args.kind, args.name, force=args.force)
    if cmd == "unshare":
        return actions.unshare(p, args.kind, args.name)
    if cmd in ("resolve", "approve", "reject", "allow-once", "discard-blocked", "revert"):
        fn = {
            "resolve": lambda: actions.resolve(p, args.id, args.choice),
            "approve": lambda: actions.approve(p),
            "reject": lambda: actions.reject(p),
            "allow-once": lambda: actions.allow_once(p),
            "discard-blocked": lambda: actions.discard_blocked(p),
            "revert": lambda: actions.revert(p, args.sha),
        }[cmd]
        fn()
        return report.status(p)
    if cmd == "block":
        return {"words": actions.block(p, args.op, args.word)}
    if cmd == "mode":
        actions.set_sync_mode(p, args.value)
        return report.status(p)
    if cmd == "auto":
        actions.set_auto(p, args.value == "on")
        return report.status(p)
    if cmd == "set-remote":
        if args.backend == "folder":
            return setup_mod.use_folder(p, args.folder or "")
        # The token arrives on stdin only, so it never shows up in a process list or shell history.
        if args.backend == "notion":
            if args.oauth:  # the token the last 'Notion으로 연결' left in the keychain
                return setup_mod.use_notion(p, args.page or "", None, oauth=True)
            return setup_mod.use_notion(p, args.page or "", setup_mod.stdin_secret())
        token = setup_mod.stdin_secret()
        if not args.url or not args.username:
            raise actions.ActionError(t("저장소 주소와 --username 이 필요해요", "The repository address and --username are needed"))
        return setup_mod.use_git(p, args.url, args.username, token)
    if cmd == "adopt-engine":
        return actions.adopt_engine(p, args.source)
    if cmd == "register-pc":
        return actions.register_pc(args.key, admin_host=args.admin_host)
    return setup_mod.run(p, args)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        out = run(args)
    except actions.ActionError as e:
        print(json.dumps(dict({"error": str(e)}, **e.data), ensure_ascii=False))
        return 1
    except Exception as e:  # every failure reaches the app as JSON
        print(json.dumps({"error": str(e)[:1000]}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 1 if isinstance(out, dict) and out.get("ok") is False else 0
