#!/bin/bash
# 설정 동기화(config-sync) 설치 파일 — 새 Mac에서 터미널로 한 번 실행합니다:
#   bash ~/Downloads/claude-sync-install.command
# 비밀값은 들어 있지 않습니다. 여러 번 실행해도 안전합니다.
# 저장소는 git(GitLab·GitHub 등) 또는 Notion 중에서 고릅니다. git 주소가 https:// 이면 액세스 토큰으로,
# 아니면 SSH 키로 연결합니다. Notion은 연결(integration)의 비밀 키로 연결합니다.
set -euo pipefail

ssh_host=${CLAUDE_SYNC_SSH_HOST:-}  # the address of your own sync server, for claude-sync-server:… remotes
alias_name=claude-sync-server
key=$HOME/.ssh/claude_sync_ed25519
shared=$HOME/.claude/shared
quiet=${CLAUDE_SYNC_NONINTERACTIVE:-}
helper=${CLAUDE_SYNC_CREDENTIAL_HELPER:-osxkeychain}
probe_limit=${CLAUDE_SYNC_PROBE_SECONDS:-20}
clone_limit=${CLAUDE_SYNC_CLONE_SECONDS:-300}
ssh_cmd=${CLAUDE_SYNC_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=10}

say() { printf '%s\n' "$*"; }
fail() { say "✗ $*" >&2; exit 1; }
ask() { # ask <변수> <질문> <기본값>
  local answer=""
  if [ -z "$quiet" ]; then
    if [ -n "$3" ]; then
      read -r -p "$2 [$3] " answer </dev/tty || true
    else
      read -r -p "$2: " answer </dev/tty || true
    fi
  fi
  printf -v "$1" '%s' "${answer:-$3}"
}

# Runs a command in a process group of its own with a hard deadline: its exit code, or 124
# when the deadline passed. At the deadline the whole group (git, ssh, the https helper)
# is stopped, killed if it ignores the polite signal.
bounded() {
  local limit=$1 waited=0
  shift
  /usr/bin/perl -e 'setpgrp(0, 0); exec @ARGV or exit 127' -- "$@" >/dev/null 2>&1 &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$limit" ]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
      sleep 1
      kill -KILL -- "-$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"
}

# A network check: 0 reached, 1 refused, 2 no answer in time.
probe() {
  local rc=0
  bounded "$probe_limit" "$@" || rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -eq 124 ] && return 2
  return 1
}

# git with only our credential helper: no prompts, the token comes from the keychain.
git_https() {
  GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=$helper" -c credential.useHttpPath=true "$@"
}

say "== Claude Code 설정 동기화 설치 =="

# 1. 준비 확인
command -v git >/dev/null || fail "git이 없습니다. 터미널에서 xcode-select --install 을 먼저 실행하세요."
if [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
  fail "Xcode Command Line Tools가 필요합니다. xcode-select --install 을 실행한 뒤 다시 실행하세요."
fi
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null \
  || fail "/usr/bin/python3 (3.8 이상)이 필요합니다. xcode-select --install 을 실행하세요."

# 2. 저장소 종류와 주소
backend=${CLAUDE_SYNC_BACKEND:-}
if [ -z "$backend" ] && [ -n "${CLAUDE_SYNC_REMOTE:-}" ]; then
  backend=git
fi
if [ -z "$backend" ]; then
  say "공용 설정을 어디에 둘까요?  1) git 저장소 (GitLab·GitHub)   2) Notion 페이지"
  ask kind_answer "저장소 종류 (1/2)" "1"
  case "$kind_answer" in 2|notion|Notion) backend=notion ;; *) backend=git ;; esac
fi
case "$backend" in git|notion) ;; *) fail "저장소 종류는 git 또는 notion 이에요: $backend" ;; esac
remote=${CLAUDE_SYNC_REMOTE:-}
page=${CLAUDE_SYNC_NOTION_PAGE:-}
mode=ssh
if [ "$backend" = notion ]; then
  mode=notion
  if [ -z "$page" ]; then
    say "동기화용 Notion 페이지 주소를 붙여 넣으세요. 그 페이지의 … → 연결(Connections)에"
    say "Claude Sync용 연결을 먼저 추가해야 해요."
    ask page "Notion 페이지 주소" ""
  fi
  printf '%s' "$page" | grep -Eq '[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}' \
    || fail "Notion 페이지 주소에서 페이지 id(32자리)를 찾지 못했어요: $page"
else
  if [ -z "$remote" ]; then
    say "동기화 저장소 주소를 입력하세요. 예: https://gitlab.com/아이디/claude-config.git, https://github.com/아이디/claude-config.git"
    ask remote "동기화 저장소 주소" ""
  fi
  [ -n "$remote" ] || fail "동기화 저장소 주소가 필요해요."
  case "$remote" in
    https://*) mode=https ;;
    file://*) [ "${CLAUDE_SYNC_TEST_HTTPS_LIKE:-}" = 1 ] && mode=https ;;
  esac
fi

# 3. 이 PC 이름
pc=${CLAUDE_SYNC_PC:-}
if [ -z "$pc" ]; then
  ask pc "이 PC의 이름 (영문·숫자·하이픈)" "company-mac"
fi
case "$pc" in
  *[!A-Za-z0-9-]*|"") fail "PC 이름은 영문·숫자·하이픈만 쓸 수 있어요: $pc" ;;
esac

# 3b. 금지어
if [ -n "${CLAUDE_SYNC_WORDS+set}" ]; then
  words=$CLAUDE_SYNC_WORDS
else
  say ""
  say "이 PC 밖으로 나가면 안 되는 단어를 쉼표로 적어 주세요 (회사명, 내부 도메인, 서버 이름 등)."
  say "공용 파일에 이 단어가 들어가면 올리지 않고 알려 드려요. 나중에 /sync 에서 고칠 수 있어요."
  ask words "금지어" ""
fi

# 3c. 받기 전용: 회사처럼 바깥으로 올리기가 막힌(또는 막아야 하는) PC
receive_only=${CLAUDE_SYNC_RECEIVE_ONLY:-}
if [ -z "$receive_only" ]; then
  say ""
  say "이 PC를 '받기 전용'으로 둘까요? 다른 PC의 설정은 계속 받고, 이 PC에서는 아무것도 올리지 않아요."
  say "회사 PC처럼 외부 저장소로 올리기가 막혀 있거나, 회사 정보가 나가면 안 되는 PC라면 y를 권해요."
  [ "$backend" = notion ] && say "Notion을 쓰더라도 회사 PC에서 올리기를 켤지는 회사 규정을 보고 정하세요."
  ask ro_answer "받기 전용 (y/n)" "n"
  case "$ro_answer" in y|Y|yes|YES|예|네) receive_only=1 ;; *) receive_only=0 ;; esac
fi
setup_args=(setup --pc "$pc" --words "$words")
[ "$receive_only" = 1 ] && setup_args+=(--receive-only)

report() { # report <engine JSON>
  printf '%s' "$1" | /usr/bin/python3 -I -c '
import json, sys
r = json.load(sys.stdin)
if r.get("error") and r.get("result") is None:
    print("✗", r["error"]); sys.exit(1)
print("✓ 적용 결과:", r.get("result"), "· 백업:", r.get("backup"))
for o in r.get("overwritten") or []:
    print("  공용 값으로 바뀐 설정:", ".".join(o["path"]), o["local"], "→", o["shared"])
for c in r.get("collisions") or []:
    print("  이름이 겹쳐 건너뛴 공용 항목:", "%s/%s" % (c["kind"], c["name"]))
for p in r.get("missing_plugins") or []:
    print("  이 PC에 없는 플러그인 (직접 설치 필요):", p)
for k in r.get("managed_settings_keys") or []:
    print("  회사 관리 설정이 정한 항목 (mod가 막힐 수 있음):", k)
if r.get("result") not in ("ok", None):
    print("  상태:", r.get("result"), r.get("error") or "")
'
}

finish() {
  say ""
  say "끝났어요. Claude Code를 다시 시작하면 입력창 위에 동기화 상태가 보이고 /sync 로 앱을 열 수 있어요."
  say "Codex 리뷰 지침을 쓰려면 이 PC에도 codex 설치와 codex login 이 필요해요."
}

if [ "$mode" = notion ]; then
  # 4c. Notion 비밀 키: 키체인에만 저장하고, 화면·파일·인자에는 남기지 않음 (엔진에 표준 입력으로 넘김)
  if [ -n "${CLAUDE_SYNC_NOTION_SECRET+set}" ]; then
    secret=$CLAUDE_SYNC_NOTION_SECRET
  elif [ -n "$quiet" ]; then
    fail "Notion 비밀 키가 필요해요."
  else
    read -r -s -p "Notion 연결의 비밀 키 (붙여 넣어도 화면에 보이지 않아요): " secret </dev/tty || true
    say ""
  fi
  secret=$(printf '%s' "$secret" | tr -d '[:space:]')
  [ -n "$secret" ] || fail "Notion 비밀 키가 비어 있어요."
  page_id=$(printf '%s' "$page" | /usr/bin/python3 -I -c 'import re, sys
text = sys.stdin.read().split("?")[0].split("#")[0]
ids = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", text)
print(ids[-1].replace("-", "").lower() if ids else "")')
  [ -n "$page_id" ] || fail "Notion 페이지 주소에서 페이지 id(32자리)를 찾지 못했어요: $page"
  error_of() { # the engine's JSON error text
    printf '%s' "$1" | /usr/bin/python3 -I -c 'import json, sys
try:
    print(json.load(sys.stdin).get("error") or "설정에 실패했어요")
except Exception:
    print("설정에 실패했어요")'
  }
  # 엔진: 이 설치 파일 끝에 들어 있는 것(scripts/build-installer.sh)을 풀어 씀.
  unpack_engine() {
    local at
    at=$(grep -an '^__CLAUDE_SYNC_ENGINE__$' "$0" | tail -n 1 | cut -d: -f1 || true)
    [ -n "$at" ] || fail "이 설치 파일에는 동기화 엔진이 들어 있지 않아요. 배포용 설치 파일(dist)을 쓰세요."
    unpack=$(mktemp -d)
    trap 'rm -rf "$unpack"' EXIT
    tail -n +"$((at + 1))" "$0" \
      | /usr/bin/python3 -I -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))' \
      | tar -xzf - -C "$unpack" || fail "설치 파일 안의 엔진을 풀지 못했어요."
    engine=$unpack/config-sync/bin/claude-sync
  }
  if [ -e "$shared" ] && [ ! -d "$shared/.git" ]; then
    fail "$shared 가 이미 있습니다. 옮기거나 지운 뒤 다시 실행하세요."
  fi
  # 처음이든 다시 실행이든 이 설치 파일에 든 엔진의 join에 맡겨요. join은 같은 페이지면 비밀 키를 새로 저장하고
  # 다시 적용하고, 받기 전에 멈춘 설치는 처음부터 다시 하고, 다른 저장소면 거절해요.
  unpack_engine
  join_args=(join --backend notion --page "$page_id" --pc "$pc" --words "$words")
  [ "$receive_only" = 1 ] && join_args+=(--receive-only)
  say "Notion에서 공용 설정을 받아 이 PC에 적용하는 중…"
  out=$(printf '%s\n' "$secret" | "$engine" "${join_args[@]}") \
    || { unset secret; fail "$(error_of "$out")"; }
  unset secret
  report "$out"
  [ -x "$shared/mods/config-sync/bin/claude-sync" ] \
    || fail "공용 설정은 받았지만 공용 저장소에 동기화 mod(config-sync)가 없어 이 PC는 앞으로 동기화하지 못해요. 첫 PC에서 config-sync mod를 공용으로 보낸 뒤 이 설치 파일을 다시 실행하세요."
  finish
  exit 0
fi

if [ "$mode" = https ]; then
  # 4a. 액세스 토큰: 키체인에만 저장하고, 화면·파일·git 설정에는 남기지 않음
  # (GitLab 프로젝트 액세스 토큰과 GitHub fine-grained 토큰은 사용자 이름을 따지지 않아 PC 이름을 그대로 씁니다)
  token_user=${CLAUDE_SYNC_TOKEN_USER:-$pc}
  case "$token_user" in
    *[!A-Za-z0-9._+@-]*|"") fail "토큰 사용자 이름이 올바르지 않아요." ;;
  esac
  if [ -n "${CLAUDE_SYNC_TOKEN+set}" ]; then
    token=$CLAUDE_SYNC_TOKEN
  elif [ -n "$quiet" ]; then
    fail "액세스 토큰이 필요해요."
  else
    read -r -s -p "액세스 토큰 — GitLab 프로젝트 토큰 또는 GitHub fine-grained 토큰 (붙여 넣어도 화면에 보이지 않아요): " token </dev/tty || true
    say ""
  fi
  [ -n "$token" ] || fail "액세스 토큰이 비어 있어요."
  if [ "${remote#https://}" != "$remote" ]; then
    rest=${remote#https://}
    host=${rest%%/*}
    path=${rest#*/}
    case "$host/$path" in
      *[!A-Za-z0-9./_-]*|*/|/*) fail "주소가 올바르지 않아요: $remote" ;;
    esac
    case "$path" in *.git) ;; *) fail "주소는 .git 으로 끝나야 해요: $remote" ;; esac
    clone_url="https://${token_user//@/%40}@$host/$path"
  else
    host=test.invalid
    path=${remote#file:///}
    clone_url=$remote
  fi
  say "토큰을 키체인에 저장하는 중…"
  printf 'protocol=https\nhost=%s\npath=%s\nusername=%s\npassword=%s\n\n' "$host" "$path" "$token_user" "$token" \
    | git -c credential.helper= -c "credential.helper=$helper" -c credential.useHttpPath=true credential approve \
    || fail "토큰을 키체인에 저장하지 못했어요."

  say "서버 연결 확인 중… (최대 ${probe_limit}초)"
  if probe env GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=$helper" -c credential.useHttpPath=true ls-remote "$clone_url"; then rc=0; else rc=$?; fi
  [ "$rc" -eq 2 ] && fail "저장소가 ${probe_limit}초 안에 답하지 않았어요. 네트워크를 확인하고 다시 실행하세요."
  [ "$rc" -eq 0 ] || fail "저장소에 연결하지 못했어요. 주소와 토큰(read/write 권한)을 확인하세요."
  say "✓ 저장소에 연결됐어요."
else
  # 4b. SSH: 전용 키와 ssh 설정, 처음이면 공개키 등록을 기다림
  command -v ssh >/dev/null || fail "ssh가 없습니다."
  if [ -z "${CLAUDE_SYNC_SKIP_SSH:-}" ]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    if [ ! -f "$key" ]; then
      ssh-keygen -q -t ed25519 -N '' -C "$pc" -f "$key"
    fi
    touch "$HOME/.ssh/config"
    if [ "${remote#"$alias_name":}" != "$remote" ] && ! grep -q "^Host $alias_name\$" "$HOME/.ssh/config"; then
      [ -n "$ssh_host" ] || ask ssh_host "동기화 서버 주소(IP 또는 도메인)" ""
      [ -n "$ssh_host" ] || fail "동기화 서버 주소가 필요해요. CLAUDE_SYNC_SSH_HOST로 주거나, https 주소로 다시 실행하세요."
      case "$ssh_host" in *[!A-Za-z0-9.:-]*|-*) fail "서버 주소가 올바르지 않아요: $ssh_host" ;; esac
      {
        printf '\nHost %s\n' "$alias_name"
        printf '  HostName %s\n  User claudesync\n  IdentityFile %s\n' "$ssh_host" "$key"
        printf '  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n  ConnectTimeout 5\n'
      } >> "$HOME/.ssh/config"
    fi
  fi
  clone_url=$remote
  blocked="회사 네트워크가 SSH를 막고 있을 수 있어요 — https 주소로 다시 실행하세요."
  say "서버 연결 확인 중… (최대 ${probe_limit}초)"
  if probe env GIT_SSH_COMMAND="$ssh_cmd" git ls-remote "$remote"; then rc=0; else rc=$?; fi
  [ "$rc" -eq 2 ] && fail "$blocked"
  if [ "$rc" -ne 0 ]; then
    [ -n "$quiet" ] && fail "서버에 연결할 수 없습니다: $remote"
    say ""
    say "아직 이 PC가 서버에 등록되지 않았어요. 아래 공개키 한 줄을 복사해서"
    say "개인 Mac의 Claude Code에서 /sync → '새 PC 등록' 칸에 붙여 넣어 주세요."
    say "(공개키는 다른 사람이 봐도 괜찮은 값입니다)"
    say ""
    cat "$key.pub"
    say ""
    say "등록될 때까지 기다립니다… (최대 30분, 그만두려면 Ctrl+C. 나중에 다시 실행해도 됩니다)"
    tries=0
    while true; do
      sleep 10
      if probe env GIT_SSH_COMMAND="$ssh_cmd" git ls-remote "$remote"; then rc=0; else rc=$?; fi
      [ "$rc" -eq 0 ] && break
      [ "$rc" -eq 2 ] && fail "$blocked"
      tries=$((tries + 1))
      [ "$tries" -ge 180 ] && fail "30분 안에 등록되지 않았어요. 등록한 뒤 이 파일을 다시 실행하세요."
    done
    say "✓ 서버에 연결됐어요."
  fi
fi

# 5. 공용 저장소 받기
if [ -d "$shared/.git" ]; then
  current=$(git -C "$shared" remote get-url origin 2>/dev/null || true)
  [ "$current" = "$clone_url" ] || [ "$current" = "$remote" ] \
    || fail "$shared 에 다른 저장소가 있습니다 ($current). 옮기거나 지운 뒤 다시 실행하세요."
elif [ -e "$shared" ]; then
  fail "$shared 가 이미 있습니다. 옮기거나 지운 뒤 다시 실행하세요."
else
  say "공용 설정을 받는 중… (최대 ${clone_limit}초)"
  rc=0
  if [ "$mode" = https ]; then
    bounded "$clone_limit" env GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=$helper" \
      -c credential.useHttpPath=true clone -q "$clone_url" "$shared" || rc=$?
  else
    bounded "$clone_limit" env GIT_SSH_COMMAND="$ssh_cmd" git clone -q "$remote" "$shared" || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    rm -rf "$shared"
    [ "$rc" -eq 124 ] && fail "공용 저장소를 ${clone_limit}초 안에 받지 못했어요. 네트워크를 확인하고 다시 실행하세요."
    fail "공용 저장소를 받지 못했어요."
  fi
fi
engine=$shared/mods/config-sync/bin/claude-sync
[ -x "$engine" ] || fail "공용 저장소에 동기화 엔진이 없습니다: $engine"

if [ "$mode" = https ]; then
  # 저장소 안에 연결 설정(주소·키체인 사용)을 남김. 토큰은 표준 입력으로만 넘김.
  printf '%s\n' "$token" | "$engine" set-remote "$remote" --username "$token_user" >/dev/null \
    || fail "저장소 연결 설정을 저장하지 못했어요."
  unset token
fi

# 7. 적용
say "공용 설정을 이 PC에 적용하는 중…"
out=$("$engine" "${setup_args[@]}") || fail "설정 적용에 실패했어요: $out"
report "$out"
finish
exit 0
