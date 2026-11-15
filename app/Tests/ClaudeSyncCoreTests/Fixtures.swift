// Captured from `claude-sync status` / `claude-sync log -n 3` on the personal Mac (2026-10-20).
let realStatusJSON = #"""
{
 "pc": "personal-mac",
 "now": 1792480337.881601,
 "last_pull": 1792477811.95705,
 "last_push": 1792422183.631382,
 "last_result": "ok",
 "last_error": null,
 "auto_enabled": true,
 "require_approval": true,
 "others": [
  {
   "pc": "홍길동",
   "time": 1792420933
  }
 ],
 "items": {
  "claude_md": {
   "state": "ok"
  },
  "settings": {
   "count": 20,
   "keys": [
    "agentPushNotifEnabled",
    "effortLevel",
    "enabledPlugins.superpowers@claude-plugins-official",
    "enabledPlugins.swift-lsp@claude-plugins-official",
    "env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    "env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    "inputNeededNotifEnabled",
    "modelSettings.claude-fable-5-1",
    "modelSettings.claude-opus-5-5",
    "permissions.defaultMode",
    "skillOverrides.banner-design",
    "skillOverrides.brand",
    "skillOverrides.design",
    "skillOverrides.design-system",
    "skillOverrides.slides",
    "skillOverrides.ui-styling",
    "skillOverrides.ui-ux-pro-max",
    "skipWorkflowUsageWarning",
    "teammateMode",
    "theme"
   ],
   "state": "ok"
  },
  "skills": [
   {
    "name": "banner-design",
    "state": "ok"
   },
   {
    "name": "brand",
    "state": "ok"
   },
   {
    "name": "design",
    "state": "ok"
   },
   {
    "name": "design-system",
    "state": "ok"
   },
   {
    "name": "explain-diff-html",
    "state": "ok"
   },
   {
    "name": "slides",
    "state": "ok"
   },
   {
    "name": "ui-styling",
    "state": "ok"
   },
   {
    "name": "ui-ux-pro-max",
    "state": "ok"
   }
  ],
  "mods": [
   {
    "name": "command-guard",
    "state": "ok"
   },
   {
    "name": "compact-keeper",
    "state": "ok"
   },
   {
    "name": "config-sync",
    "state": "ok"
   },
   {
    "name": "keep-awake",
    "state": "ok"
   },
   {
    "name": "usage-alerts",
    "state": "ok"
   },
   {
    "name": "usage-band",
    "state": "ok"
   }
  ]
 },
 "local_only": {
  "skills": [],
  "mods": [],
  "settings": [
   "hooks",
   "statusLine",
   "skipDangerousModePermissionPrompt",
   "autoMode"
  ]
 },
 "incoming": 0,
 "outgoing": 0,
 "conflicts": [],
 "blocked": [],
 "pending_approval": [],
 "collisions": [],
 "overwritten": [],
 "words": []
}
"""#

let realLogJSON = #"""
[
 {
  "sha": "cbc4717063cea1e6cea29694782b8cdb37d3bd26",
  "time": 1792422183,
  "pc": "personal-mac",
  "subject": "sync: personal-mac: mod config-sync, 스킬 design, 스킬 ui-ux-pro-max, .gitignore",
  "files": [
   ".gitignore",
   "mods/config-sync/engine/claude_sync/cli.py",
   "mods/config-sync/engine/claude_sync/gitio.py",
   "mods/config-sync/engine/claude_sync/remote.py",
   "mods/config-sync/engine/claude_sync/setup.py",
   "mods/config-sync/engine/claude_sync/sync.py",
   "skills/design/scripts/logo/__pycache__/core.cpython-314.pyc",
   "skills/ui-ux-pro-max/scripts/__pycache__/core.cpython-314.pyc",
   "skills/ui-ux-pro-max/scripts/__pycache__/design_system.cpython-314.pyc"
  ]
 },
 {
  "sha": "94bc62c99b7574bec197b3d811e5574d4cb5419d",
  "time": 1792420933,
  "pc": "홍길동",
  "subject": "Add new file",
  "files": [
   "README.md"
  ]
 }
]
"""#
