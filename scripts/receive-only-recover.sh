#!/bin/bash
# 회사 Mac 복구: 토큰을 다시 넣고, 새 엔진을 받아(올리지 않음) 이 PC를 '받기 전용'으로 바꿉니다.
# 토큰은 화면·파일·인자에 남기지 않습니다.   실행: bash receive-only-recover.sh
set -euo pipefail

repo=$HOME/.claude/shared
state=$HOME/.claude/sync/state.json
url=$(git -C "$repo" config --get remote.origin.url)
case "$url" in
  https://*@*/*) ;;
  *) echo "✗ 이 PC의 공용 저장소 주소가 https 형식이 아니에요: $url" >&2; exit 1 ;;
esac
rest=${url#https://}
user=$(printf '%s' "${rest%%@*}" | /usr/bin/python3 -I -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.stdin.read()))')
hostpath=${rest#*@}
host=${hostpath%%/*}
path=${hostpath#*/}
# get/store only: a refused request must never erase the token (the company proxy refuses uploads).
# Same text the engine's set-remote installs (CLAUDE_SYNC_CREDENTIAL_HELPER is for tests).
helper=${CLAUDE_SYNC_CREDENTIAL_HELPER:-osxkeychain}
case "$helper" in
  !*) helper_cmd=${helper#!} ;;
  /*) helper_cmd=$helper ;;
  *) helper_cmd="git credential-$helper" ;;
esac
guarded='!f() { case "$1" in get|store) '"$helper_cmd"' "$1" ;; *) cat >/dev/null ;; esac; }; f'

# Run a command with a time limit (seconds); the alarm survives exec and stops it.
limited() {
  local seconds=$1
  shift
  /usr/bin/perl -e 'alarm shift @ARGV; exec @ARGV or exit 127' "$seconds" "$@"
}

if [ -n "${CLAUDE_SYNC_TOKEN:-}" ]; then
  token=$CLAUDE_SYNC_TOKEN
else
  read -r -s -p "GitLab 액세스 토큰 (붙여 넣어도 화면에 보이지 않아요): " token </dev/tty
  echo
fi
token=$(printf '%s' "$token" | tr -d '[:space:]')
[ -n "$token" ] || { echo "✗ 토큰이 비어 있어요" >&2; exit 1; }

# Hold the engine's sync lock while the protection goes in and the new engine arrives, so a
# background sync cannot push in between. Every step under it is time-limited, well inside the
# engine's 600-second stale-lock limit.
lockdir=$HOME/.claude/sync/lock
mkdir -p "$HOME/.claude/sync"
owned=0
for _ in $(seq 1 60); do
  if mkdir "$lockdir" 2>/dev/null; then owned=1; break; fi
  sleep 1
done
[ "$owned" = 1 ] || { unset token; echo "✗ 다른 동기화가 1분 넘게 진행 중이에요. 잠시 뒤 다시 실행하세요." >&2; exit 1; }
printf '%s %s\n' "$$" "$(date +%s)" > "$lockdir/owner"
release() { [ "$owned" = 1 ] && rm -rf "$lockdir"; owned=0; }
trap release EXIT

echo "1/4 토큰 보호 설정과 받기 전용으로 바꾸는 중…"
# The repo's own helper setting protects the token even under the old engine, on every path below.
git -C "$repo" config --local --unset-all credential.helper || true
git -C "$repo" config --local --add credential.helper ""
git -C "$repo" config --local --add credential.helper "$guarded"
git -C "$repo" config --local credential.useHttpPath true
/usr/bin/python3 -I - "$state" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, ValueError):
    data = {}
data["receive_only"] = True
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
os.replace(tmp, path)
PY

echo "2/4 토큰을 키체인에 저장하는 중…"
if ! printf 'protocol=https\nhost=%s\npath=%s\nusername=%s\npassword=%s\n\n' "$host" "$path" "$user" "$token" \
    | limited 60 git -C "$repo" credential approve; then
  unset token
  echo "✗ 키체인에 저장하지 못했어요. 키체인이 잠겨 있으면 풀고 다시 실행하세요." >&2
  exit 1
fi
unset token

echo "3/4 새 동기화 엔진을 받는 중… (올리기는 하지 않아요)"
if ! GIT_TERMINAL_PROMPT=0 limited 120 git -C "$repo" fetch -q origin; then
  echo "✗ 서버에서 받지 못했어요(2분 제한). 토큰은 보호된 채 저장돼 있어요. 네트워크를 확인하고 다시 실행하세요." >&2
  exit 1
fi
if ! git -C "$repo" -c "user.name=$user" -c "user.email=$user@claude-sync" -c core.hooksPath=/dev/null \
    merge -q --no-edit -m "sync: $user: 받기 전용 전환 전 합치기" origin/main; then
  git -C "$repo" merge --abort 2>/dev/null || true
  echo "✗ 받은 내용과 이 PC의 변경이 충돌해요. 이 화면을 그대로 알려 주세요." >&2
  exit 1
fi
engine=$repo/mods/config-sync/bin/claude-sync
release

echo "4/4 동기화 확인 중…"
"$engine" sync | /usr/bin/python3 -I -c '
import json, sys
r = json.load(sys.stdin)
print("결과:", r.get("last_result"), "| 받기 전용:", r.get("receive_only"), "|", r.get("last_error") or "")
sys.exit(0 if r.get("last_result") == "ok" and r.get("receive_only") else 1)
'
echo "끝났어요. Claude Code를 다시 시작하면 입력창 위에 '받기 전용'으로 보여요."
