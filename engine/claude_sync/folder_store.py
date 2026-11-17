"""The shared history kept in a synced folder (iCloud Drive, Dropbox, Google Drive, OneDrive, a NAS): one
file per upload, each a git bundle, never changed afterwards (rowstore has how rows make a history).

A file is written under a name no reader takes (".<name>.partial") and then renamed, so a sync app only
carries whole files; one that is still arriving fails to verify and is read on a later sync. The name
carries what a row needs:  <UTC time>-full-<head>.bundle  or  <UTC time>-delta-<head>-<base>.bundle
The times come from each PC's own clock, which may be off: a PC reads from a day before the latest full file.
A file that arrives later than that (its PC was offline while another wrote a full file) is not read, but
its PC's next sync merges the full file and sends the result, as after any two uploads that crossed.
"""
from __future__ import annotations

import calendar
import os
import re
import shutil
import time
from typing import List, Optional

from . import rowstore
from .gitio import git, head
from .lang import t
from .paths import Paths
from .state import State

NAME = re.compile(r"^(\d{8}T\d{6}Z)-(full|delta)-([0-9a-f]{40})(?:-([0-9a-f]{40}))?\.bundle$")
STAMP = "%Y%m%dT%H%M%SZ"
SKEW_SECONDS = 24 * 3600
clock = time.time
OTHER_HISTORY = ("이 폴더에는 이 PC와 이어지지 않는 다른 공용 설정이 있어요. 빈 폴더나 이 PC들이 쓰는 폴더를 고르세요",
                 "This folder holds other shared settings that do not continue this PC's. Choose an empty folder or the one these PCs use")


class FolderError(RuntimeError):
    pass


def folder_path(text: str) -> str:
    return os.path.abspath(os.path.expanduser((text or "").strip()))


def _missing(path: str) -> FolderError:
    return FolderError(t("동기화 폴더를 찾을 수 없어요: %s. 그 폴더의 동기화 앱(iCloud Drive·Dropbox 등)이 켜져 있는지 보세요",
                         "Cannot find the sync folder: %s. Check that its sync app (iCloud Drive, Dropbox…) is running") % path)


def _seconds(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp, STAMP))


class FolderRows(rowstore.Store):
    def __init__(self, path: str):
        self.path = path

    def all(self) -> List[rowstore.Row]:
        try:
            names = os.listdir(self.path)
        except FileNotFoundError:
            raise _missing(self.path) from None
        except OSError as e:
            raise FolderError(t("동기화 폴더를 읽지 못했어요: %s (%s)", "Could not read the sync folder: %s (%s)")
                              % (self.path, e.strerror)) from None
        rows = []
        for name in names:
            m = NAME.match(name)
            if m and (m.group(2) == "full") == (m.group(4) is None):
                rows.append(rowstore.Row(id=name[:-len(".bundle")], time=m.group(1), full=m.group(2) == "full",
                                         head=m.group(3), base=m.group(4) or ""))
        return sorted(rows, key=rowstore.key)

    def window(self) -> List[rowstore.Row]:
        rows = self.all()
        fulls = [r for r in rows if r.full]
        if not fulls:
            return rows
        start = _seconds(max(fulls, key=rowstore.key).time) - SKEW_SECONDS
        return [r for r in rows if _seconds(r.time) >= start]

    def read(self, row: rowstore.Row, path: str) -> bool:
        shutil.copyfile(os.path.join(self.path, row.id + ".bundle"), path)
        return True

    def add(self, *, full: bool, head: str, base: str, summary: str, pc: str, data: bytes) -> None:
        stamp = time.strftime(STAMP, time.gmtime(clock()))
        name = "%s-%s-%s%s.bundle" % (stamp, "full" if full else "delta", head, "" if full else "-" + base)
        partial = os.path.join(self.path, ".%s.partial" % name)
        try:
            with open(partial, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(partial, os.path.join(self.path, name))
        except FileNotFoundError:
            raise _missing(self.path) from None
        except OSError as e:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise FolderError(t("동기화 폴더에 쓰지 못했어요: %s (%s)", "Could not write in the sync folder: %s (%s)")
                              % (self.path, e.strerror)) from None


def _rows(s: State) -> FolderRows:
    if not s.folder_path:
        raise FolderError(t("폴더 저장소 설정이 없어요. 설정을 다시 하세요", "No folder storage settings. Set up again"))
    return FolderRows(s.folder_path)


def fetch(p: Paths, s: State) -> Optional[str]:
    """Bring in new files and point origin/main at the remote tip; an error text, or None."""
    try:
        rowstore.fetch(p, _rows(s))
        return None
    except FolderError as e:
        return str(e)


def push(p: Paths, s: State, remote: Optional[str], current: str) -> Optional[str]:
    """Write HEAD (== current) as one file; an error text, or None."""
    try:
        return rowstore.push(p, s.pc_name, _rows(s), remote, current)
    except FolderError as e:
        return str(e)


def check_move(p: Paths, folder_text: str, *, receive_only: bool) -> None:
    """Before this PC moves to the folder: what it holds must continue this PC's history. A receive-only
    PC writes nothing, so a sending PC must have moved there first."""
    path = folder_path(folder_text)
    rows = FolderRows(path).window() if os.path.isdir(path) else []
    if not rows:
        if receive_only:
            raise FolderError(t("이 폴더에는 아직 공용 설정이 없어요. 올리는 PC를 먼저 이 폴더로 옮기세요",
                                "This folder has no shared settings yet. Move a PC that uploads to this folder first"))
        return
    store = FolderRows(path)
    try:
        rowstore.absorb(p, store, rows)
        theirs, current = rowstore.tip(p, rows), head(p)
        if theirs is None:
            raise FolderError(t("이 폴더의 공용 설정을 읽지 못했어요. 동기화 앱이 파일을 다 받은 뒤 다시 하세요",
                                "Could not read this folder's shared settings. Try again once its sync app has every file"))
        if current and not git(p, "merge-base", theirs, current, check=False).stdout.strip():
            raise FolderError(t(*OTHER_HISTORY))
    finally:
        rowstore.forget_rows(p)


def connect(p: Paths, s: State, folder_text: str, *, create: bool, require_empty: bool = False) -> None:
    """Point this PC at the folder (made when `create`, never by a receive-only PC)."""
    if not (folder_text or "").strip():
        raise FolderError(t("동기화 폴더를 골라 주세요", "Choose the sync folder"))
    path = folder_path(folder_text)
    if not os.path.isdir(path):
        if not create:
            raise _missing(path)
        try:
            os.makedirs(path)
        except OSError as e:
            raise FolderError(t("폴더를 만들지 못했어요: %s (%s)", "Could not make the folder: %s (%s)") % (path, e.strerror)) from None
    if create and not os.access(path, os.W_OK):
        raise FolderError(t("이 폴더에 쓸 권한이 없어요: %s", "No permission to write in this folder: %s") % path)
    if require_empty and FolderRows(path).all():
        raise FolderError(t("이 폴더에는 이미 공용 설정이 있어요. '다른 PC 연결'로 설정하세요",
                            "This folder already holds shared settings. Set this PC up with 'Receive another PC's settings'"))
    s.backend, s.folder_path = "folder", path
