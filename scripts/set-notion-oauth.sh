#!/bin/bash
# Notion 공개 연결(public connection)의 client id와 client secret을 이 Mac 키체인에 넣습니다.
# 앱을 빌드할 때(scripts/build-app.sh) 이 값이 'Notion으로 연결'용으로 앱에 들어갑니다(개인용).
# secret은 화면·인자에 남지 않게 security가 직접 물어봅니다.   실행: bash scripts/set-notion-oauth.sh
set -euo pipefail
item=claude-sync-notion-oauth
read -r -p "Notion 공개 연결의 OAuth client ID: " client_id </dev/tty
client_id=$(printf '%s' "$client_id" | tr -d '[:space:]')
case "$client_id" in
  ""|*[!A-Za-z0-9-]*) echo "✗ client ID 형식이 올바르지 않아요" >&2; exit 1 ;;
esac
security delete-generic-password -s "$item" >/dev/null 2>&1 || true
echo "이어서 OAuth client secret을 두 번 물어봐요 (붙여 넣어도 화면에 보이지 않아요)."
security add-generic-password -s "$item" -a "$client_id" -l "Claude Sync Notion 연결" -w
echo "✓ 키체인에 넣었어요. 이제 bash scripts/build-app.sh 로 앱을 다시 만드세요."
