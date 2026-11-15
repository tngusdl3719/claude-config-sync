#!/bin/bash
# 토큰 진단: 키체인 저장이 되는지, 그리고 받기·올리기·동기화 중 어느 단계에서 토큰이 사라지는지 기록합니다.
# 토큰 값은 출력하지 않습니다.   실행: bash diagnose-credentials.sh
set -u
repo=$HOME/.claude/shared
url=$(git -C "$repo" remote get-url origin)
rest=${url#https://}
user=${rest%%@*}
hostpath=${rest#*@}
host=${hostpath%%/*}
path=${hostpath#*/}
kc=(-c credential.helper= -c credential.helper=osxkeychain -c credential.useHttpPath=true)

has_token() {
  local n
  n=$(printf 'protocol=https\nhost=%s\npath=%s\nusername=%s\n\n' "$host" "$path" "$user" \
    | GIT_TERMINAL_PROMPT=0 git -C "$repo" credential fill 2>/dev/null | grep -c '^password=')
  [ "$n" = 1 ] && echo "   → 키체인의 토큰: 있음" || echo "   → 키체인의 토큰: 없음"
}

echo "== 0. 환경"
echo "git: $(command -v git) ($(git --version))"

echo "== 1. 더미 값으로 키체인 저장·읽기 시험"
printf 'protocol=https\nhost=probe.invalid\npath=a/b.git\nusername=probe\npassword=dummy-123\n\n' | git "${kc[@]}" credential approve
n=$(printf 'protocol=https\nhost=probe.invalid\npath=a/b.git\nusername=probe\n\n' | GIT_TERMINAL_PROMPT=0 git "${kc[@]}" credential fill 2>/dev/null | grep -c '^password=dummy-123$')
echo "   → 더미 읽기: $n (1이면 키체인 정상)"
printf 'protocol=https\nhost=probe.invalid\npath=a/b.git\nusername=probe\n\n' | git "${kc[@]}" credential reject

echo "== 2. 토큰 다시 저장"
read -r -s -p "GitLab 액세스 토큰 (화면에 안 보여요): " token </dev/tty
echo
printf 'protocol=https\nhost=%s\npath=%s\nusername=%s\npassword=%s\n\n' "$host" "$path" "$user" "$token" \
  | git -C "$repo" credential approve
unset token
has_token

echo "== 3. 받기 (fetch)"
GIT_TRACE=1 GIT_TERMINAL_PROMPT=0 git -C "$repo" fetch origin 2>&1 \
  | grep -E "credential-osxkeychain (get|store|erase)'|fatal|error|returned" | sed 's/.*trace: //' | sort | uniq -c
has_token

echo "== 4. 올리기 시험 (dry-run: 실제로 올리지 않음)"
GIT_TRACE=1 GIT_TERMINAL_PROMPT=0 git -C "$repo" push --dry-run origin HEAD:main 2>&1 \
  | grep -E "credential-osxkeychain (get|store|erase)'|fatal|error|returned|->" | sed 's/.*trace: //' | sort | uniq -c
has_token

echo "== 5. 동기화 엔진"
GIT_TRACE=1 "$repo/mods/config-sync/bin/claude-sync" sync 2>/tmp/claude-sync-trace.$$ \
  | /usr/bin/python3 -c "import json,sys;d=json.load(sys.stdin);print('   →', d.get('last_result'), d.get('last_error'))"
grep -E "credential-osxkeychain (get|store|erase)'" /tmp/claude-sync-trace.$$ | sed 's/.*trace: //' | sort | uniq -c
rm -f /tmp/claude-sync-trace.$$
has_token
