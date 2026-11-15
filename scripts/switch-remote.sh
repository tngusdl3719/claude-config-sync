#!/bin/bash
# Moves this PC's shared repo to an https remote (GitLab with a project access token), then syncs.
#   bash scripts/switch-remote.sh https://gitlab.com/<id>/claude-config.git gitlab+deploy-token-<n>
# The token is typed at a hidden prompt and reaches the engine on stdin only.
set -euo pipefail
url=${1:?usage: switch-remote.sh <https-url> <token-username>}
user=${2:?usage: switch-remote.sh <https-url> <token-username>}
engine=${CLAUDE_SYNC_ENGINE:-$HOME/.claude/shared/mods/config-sync/bin/claude-sync}

if [ -n "${CLAUDE_SYNC_TOKEN+set}" ]; then
  token=$CLAUDE_SYNC_TOKEN
else
  read -r -s -p "GitLab 액세스 토큰 (붙여 넣어도 화면에 보이지 않아요): " token </dev/tty
  printf '\n'
fi
[ -n "$token" ] || { echo "토큰이 비어 있어요" >&2; exit 1; }

printf '%s\n' "$token" | "$engine" set-remote "$url" --username "$user"
unset token
"$engine" sync | /usr/bin/python3 -I -c '
import json, sys
r = json.load(sys.stdin)
print("sync:", r.get("last_result") or r.get("error"), r.get("last_error") or "")
sys.exit(0 if r.get("last_result") == "ok" else 1)
'
