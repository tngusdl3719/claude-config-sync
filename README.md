# Claude Code config sync (config-sync)

Keeps the Claude Code setup of several Macs in step, but **only the parts you mark as shared**: the `CLAUDE.md` instructions, chosen settings, skills and mods. They travel through private storage of your choice: a **GitLab or GitHub repository** (private) or a **Notion page**. PC-only items (company skills, MCP servers, memory, hooks and so on) never leave the Mac, and on a work PC a blocklist stops company words from being uploaded.

## What's inside

| Folder | Contents |
|---|---|
| `engine/` | The sync engine (Python 3.9 standard library only; the `claude-sync` command, JSON output) |
| `mod/` | The view inside Claude Code (`/sync`, a status line above the prompt) |
| `app/` | The Mac app, Claude Sync (SwiftUI, macOS 14 or later) |
| `installer/` | A Terminal installer, for setting up without the app |
| `server/` | (Optional) scripts for a self-hosted SSH git server |
| `scripts/` | Build, deploy and diagnostic scripts |

## The Mac app (Claude Sync)

The app speaks Korean and English: **Settings → 언어 / Language**. Messages that come from the engine, and the Terminal installer, are in Korean for now.

### Install

1. Download `ClaudeSync-<version>.zip`, unzip it and move `Claude Sync.app` to **Applications**.
2. If macOS warns about an "unidentified developer" on first launch (the app has no Apple developer signature):
   - System Settings → **Privacy & Security** → "Claude Sync" near the bottom → **Open Anyway**, then open it again.
   - Or Control-click the app in Finder → **Open**.
3. Icons appear in the Dock and the menu bar, and the main window opens. If this Mac is not set up yet, the setup guide comes first.

### First setup (the guide)

- **Receive another PC's settings**: storage → connection (a token, or signing in to Notion) → PC name → blocked words.
- **This is the first PC**: the same steps, plus choosing which skills, mods and settings to share.
- Restart Claude Code when it is done.

### Choosing the storage

| Storage | What you need |
|---|---|
| **GitLab** | A private project, and a token for each PC from Settings → Access tokens (role **Developer**, scopes `read_repository` and `write_repository`). For a new project, unprotect `main` under Settings → Repository → Protected branches. |
| **GitHub** | A private repository. A README is fine, but do not pick a `.gitignore` or license template. For each PC, a **fine-grained token** (Settings → Developer settings) limited to that repository (*Only select repositories*) with **Contents: Read and write**. |
| **Notion** | One page to sync with. Connect with **Connect with Notion** (signing in) or with an internal integration's **secret key** (`ntn_…`). |

- Tokens and secret keys are typed into hidden fields and kept **only in this Mac's keychain**. They never go into files, logs or command arguments.
- Every PC has to use the same storage to sync with the others. You can switch between GitLab, GitHub and Notion, or connect again, later under **Storage** in the app. If the new storage holds other history that does not continue this PC's, the move is refused and nothing changes.
- **Connect with Notion** appears only when the build includes a Notion public connection (see *Development*). Without it, use a secret key.

### Everyday use

- The main window has **Status** (Sync now, auto sync, anything that needs a look), **Items** (shared and PC-only; hover over or click ⓘ for a description), **Storage**, **Blocked words**, **History** and **Settings**.
- The menu bar icon also shows the status and has **Sync now**. Clicking the Dock icon brings the main window back. Closing the window keeps the app running and syncing.
- While the app runs it syncs on a schedule (every 15 minutes by default) and when the Mac wakes. The interval, **Open at login** and the language are in **Settings**.
- When a skill or mod description is in another language than the app's, macOS 15 or later translates it on the Mac (the Translation framework).
- A **Receive only** PC only receives and never uploads anything (sharing or unsharing items, one-time approvals and reverting are hidden).

## Terminal installer

To set up without the app, run `dist/claude-sync-install.command` (made by `scripts/build-installer.sh`) on the new Mac:

```bash
bash ~/Downloads/claude-sync-install.command
```

An `https://` repository address connects with an access token; choosing Notion connects with a secret key. For a self-hosted SSH server (`claude-sync-server:…` addresses) it asks for the server's address, which you can also give as `CLAUDE_SYNC_SSH_HOST`.

## Development

It builds with the Swift in the Command Line Tools; Xcode is not needed.

```bash
app/test.sh                                  # app tests (Swift Testing)
cd engine/tests && python3 -m unittest discover -s . -p 'test_*.py'   # engine tests
scripts/build-app.sh                         # dist/Claude Sync.app and dist/ClaudeSync-<version>.zip (ad-hoc signed, engine inside)
scripts/build-installer.sh                   # dist/claude-sync-install.command
scripts/deploy.sh                            # copy the engine and mod into the shared repo's mods/config-sync (other PCs get them on the next sync)
```

- To include **Connect with Notion**, create a public connection in Notion (redirect URI `http://localhost:47821/callback`), store its client ID and secret in the keychain with `bash scripts/set-notion-oauth.sh`, then build the app. The secret ends up inside the app, so use such a build **only for yourself** and do not hand the app file to others.
- `dist/` is not tracked by git.
