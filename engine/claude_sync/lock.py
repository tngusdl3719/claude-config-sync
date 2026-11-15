"""One sync at a time per PC: a lock directory, broken when stale."""
from __future__ import annotations

import os
import shutil
import time

from .paths import Paths


class Lock:
    def __init__(self, p: Paths, stale_seconds: int = 600, wait_seconds: float = 20.0):
        self.path = p.lock_dir
        self.stale_seconds = stale_seconds
        self.wait_seconds = wait_seconds
        self.held = False

    def _try(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            os.mkdir(self.path)
        except FileExistsError:
            try:
                age = time.time() - os.stat(self.path).st_mtime
            except FileNotFoundError:
                return False
            if age <= self.stale_seconds:
                return False
            shutil.rmtree(self.path, ignore_errors=True)
            try:
                os.mkdir(self.path)
            except FileExistsError:
                return False
        with open(os.path.join(self.path, "owner"), "w") as f:
            f.write("%d %f\n" % (os.getpid(), time.time()))
        self.held = True
        return True

    def acquire(self, wait: bool) -> bool:
        deadline = time.time() + (self.wait_seconds if wait else 0)
        while True:
            if self._try():
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def release(self) -> None:
        if self.held:
            shutil.rmtree(self.path, ignore_errors=True)
            self.held = False
