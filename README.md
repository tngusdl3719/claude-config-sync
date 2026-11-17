# Claude Code config sync (config-sync)

Keeps the Claude Code setup of several Macs in step, but **only the parts you mark as shared**: the `CLAUDE.md` instructions, chosen settings, skills and mods. They travel through private storage of your choice: a **GitLab or GitHub repository** (private), a **Notion page**, or a **sync folder** (iCloud Drive, Dropbox, Google Drive, OneDrive, a NAS). PC-only items (company skills, MCP servers, memory, hooks and so on) never leave the Mac, and on a work PC a blocklist stops company words from being uploaded.

## What's inside

| Folder | Contents |
|---|---|
| `engine/` | The sync engine (Python 3.9 standard library only; the `claude-sync` command, JSON output) |
| `mod/` | The view inside Claude Code (`/sync`, a status line above the prompt) |
| `app/` | The Mac app, Claude Sync (SwiftUI, macOS 14 or later) |
| `installer/` | A Terminal installer, for setting up without the app |
| `server/notion-broker/` | The Cloudflare Worker that finishes a Notion sign-in (it holds the connection's secret) |
| `server/` | (Optional) scripts for a self-hosted SSH git server |
| `scripts/` | Build, deploy and diagnostic scripts |

## The Mac app (Claude Sync)

The app speaks Korean and English: **Settings → 언어 / Language**. The engine's messages follow the same choice. (`/sync` inside Claude Code is in Korean for now.)

### Install

1. Download `ClaudeSync-<version>.dmg` from [Releases](https://github.com/tngusdl3719/claude-config-sync/releases/latest), open it and drag **Claude Sync** onto **Applications**.
2. If macOS warns about an "unidentified developer" on first launch (the app has no Apple developer signature):
   - System Settings → **Privacy & Security** → "Claude Sync" near the bottom → **Open Anyway**, then open it again.
   - Or Control-click the app in Finder → **Open**.
3. Icons appear in the Dock and the menu bar, and the main window opens. If this Mac is not set up yet, the setup guide comes first.

### Updates

The app keeps itself up to date. Once a day it asks GitHub for the latest release, downloads it, and checks its signature against the release key built into the app; anything else is not installed. It then installs the update and restarts right away (after a sync, setup or storage change that is under way), opening its window again if one was open; **Settings → Updates** shows how far it has got. Turn this off, check now, or install right away under **Settings → Updates**. A Mac that cannot reach GitHub (a work Mac, say) just skips the check; give it a new version by hand.

An update also carries the sync engine. When the app's engine is newer than the one in your shared storage, a PC that sends puts it there, and the other PCs pick it up on their next sync (a **Receive only** PC never does this).

### First setup (the guide)

- **Receive another PC's settings**: storage → connection (a token, or signing in to Notion) → PC name → blocked words.
- **This is the first PC**: the same steps, plus choosing which skills, mods and settings to share.
- Restart Claude Code when it is done.

### Choosing the storage

| Storage | What you need |
|---|---|
| **GitLab** | A private project, and a token for each PC from Settings → Access tokens (role **Developer**, scopes `read_repository` and `write_repository`). For a new project, unprotect `main` under Settings → Repository → Protected branches. |
| **GitHub** | A private repository. A README is fine, but do not pick a `.gitignore` or license template. For each PC, a **fine-grained token** (Settings → Developer settings) limited to that repository (*Only select repositories*) with **Contents: Read and write**. |
| **Notion** | One page to sync with. **Connect with Notion**: sign in, allow the page, pick it from the list. (Or, under *Use a secret key instead*, an internal integration's secret key `ntn_…` and the page's link.) |
| **Sync folder** | A folder a sync app carries between your Macs: iCloud Drive, Dropbox, Google Drive, OneDrive, or a NAS. The app lists the ones it finds (it uses a `Claude Sync` folder inside), or choose any folder. No token or sign-in; every PC picks the same folder of the same account, and its sync app has to be running. |

- Tokens and secret keys are typed into hidden fields and kept **only in this Mac's keychain**. They never go into files, logs or command arguments.
- Every PC has to use the same storage to sync with the others. Later, **Storage** in the app lists them with a switch each: one is on, switching another on moves this PC there, and the one in use can be connected again. If the new storage holds other history that does not continue this PC's, the move is refused and nothing changes.
- A sync folder gets one new file per upload (a git bundle) and none is ever changed, so a sync app that is slow, or two Macs writing at once, cannot damage it. A file still arriving is read on a later sync.
- **Connect with Notion** works on every Mac. Notion's sign-in ends with a request that needs the connection's secret, which an app anyone can download cannot keep, so that one step goes through a small broker (`server/notion-broker`, a Cloudflare Worker) that holds the secret, passes the request to Notion and keeps nothing. Your Notion tokens stay in your Mac's keychain.

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

An `https://` repository address connects with an access token; choosing Notion connects with a secret key. (A sync folder is set up in the app.) For a self-hosted SSH server (`claude-sync-server:…` addresses) it asks for the server's address, which you can also give as `CLAUDE_SYNC_SSH_HOST`.

The installer speaks the language chosen in the Claude Sync app, or the Mac's language when the app has no choice yet (Korean for Korean, English otherwise). To pick one, run it as `CLAUDE_SYNC_LANG=en bash …` or `CLAUDE_SYNC_LANG=ko bash …`.

## Development

It builds with the Swift in the Command Line Tools; Xcode is not needed.

```bash
app/test.sh                                  # app tests (Swift Testing)
cd engine/tests && python3 -m unittest discover -s . -p 'test_*.py'   # engine tests
scripts/build-app.sh                         # dist/Claude Sync.app and dist/ClaudeSync-<version>.dmg (ad-hoc signed, engine inside, no secrets)
scripts/build-installer.sh                   # dist/claude-sync-install.command
scripts/deploy.sh                            # copy the engine and mod into the shared repo's mods/config-sync (other PCs get them on the next sync)
scripts/release.sh                           # publish AppInfo.swift's version on GitHub: dmg, signature, installer
```

- **The Notion sign-in broker**: `server/notion-broker` (tests: `node --test server/notion-broker/`). Deploy it from that folder with `npx wrangler deploy`, then store the connection's secret once with `npx wrangler secret put NOTION_CLIENT_SECRET`; its client ID and redirect URI (`http://localhost:47821/callback`) are in `wrangler.toml`, and the app names the client ID and the broker's address in `AppInfo.swift`.
- **A connection of your own** instead: create a public connection in Notion with that redirect URI and store its client ID and secret in a Mac's keychain with `bash scripts/set-notion-oauth.sh`; that Mac then signs in with it directly, without the broker.
- **Releasing**: the version lives in `app/Sources/ClaudeSyncCore/AppInfo.swift` (the engine's `__version__` must match; a test checks). Releases are signed with an Ed25519 key whose private half stays in the releasing Mac's keychain (`bash scripts/update-key.sh new` once; its public half is `AppInfo.updatePublicKey`). Commit, push, then run `scripts/release.sh`.
- If you fork this, make your own update key and put its public half in `AppInfo.updatePublicKey`, point `AppInfo.repository` at your repository, and deploy your own broker for your own Notion connection (`AppInfo.notionClientID`, `AppInfo.notionBroker`).
- `dist/` is not tracked by git.
