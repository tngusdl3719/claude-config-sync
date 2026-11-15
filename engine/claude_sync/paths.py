"""Where everything lives, derived from one home directory."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Paths:
    home: str
    claude: str = field(init=False)
    shared: str = field(init=False)
    sync_dir: str = field(init=False)
    settings: str = field(init=False)
    claude_md: str = field(init=False)
    skills: str = field(init=False)
    mods: str = field(init=False)
    state_file: str = field(init=False)
    blocklist_file: str = field(init=False)
    pending_dir: str = field(init=False)
    log_file: str = field(init=False)
    lock_dir: str = field(init=False)

    def __post_init__(self):
        home = os.path.abspath(self.home)
        claude = os.path.join(home, ".claude")
        sync_dir = os.path.join(claude, "sync")
        values = {
            "home": home,
            "claude": claude,
            "shared": os.path.join(claude, "shared"),
            "sync_dir": sync_dir,
            "settings": os.path.join(claude, "settings.json"),
            "claude_md": os.path.join(claude, "CLAUDE.md"),
            "skills": os.path.join(claude, "skills"),
            "mods": os.path.join(claude, "mods"),
            "state_file": os.path.join(sync_dir, "state.json"),
            "blocklist_file": os.path.join(sync_dir, "blocklist.txt"),
            "pending_dir": os.path.join(sync_dir, "pending"),
            "log_file": os.path.join(sync_dir, "log"),
            "lock_dir": os.path.join(sync_dir, "lock"),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def shared_settings(self) -> str:
        return os.path.join(self.shared, "settings.json")

    def shared_claude_md(self) -> str:
        return os.path.join(self.shared, "CLAUDE.md")
