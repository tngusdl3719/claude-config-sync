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

# The language of these messages and of the engine's: CLAUDE_SYNC_LANG, else the one chosen in the
# Claude Sync app, else the Mac's first language (Korean when it is Korean, English otherwise).
lang=${CLAUDE_SYNC_LANG:-}
[ -n "$lang" ] || lang=$(defaults read com.suhyeon.claudesync appLanguage 2>/dev/null || true)
[ -n "$lang" ] || lang=$({ defaults read -g AppleLanguages 2>/dev/null || true; } | sed -n 2p | tr -d ' \t",')
case "$lang" in ko*) lang=ko ;; *) lang=en ;; esac
export CLAUDE_SYNC_LANG=$lang
t() { if [ "$lang" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }  # t "한국어" "English"

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

say "$(t "== Claude Code 설정 동기화 설치 ==" "== Claude Code settings sync: install ==")"

# 1. 준비 확인
command -v git >/dev/null || fail "$(t "git이 없습니다. 터미널에서 xcode-select --install 을 먼저 실행하세요." "git is missing. Run xcode-select --install in Terminal first.")"
if [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
  fail "$(t "Xcode Command Line Tools가 필요합니다. xcode-select --install 을 실행한 뒤 다시 실행하세요." "The Xcode Command Line Tools are needed. Run xcode-select --install, then run this again.")"
fi
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null \
  || fail "$(t "/usr/bin/python3 (3.8 이상)이 필요합니다. xcode-select --install 을 실행하세요." "/usr/bin/python3 (3.8 or later) is needed. Run xcode-select --install.")"

# 2. 저장소 종류와 주소
backend=${CLAUDE_SYNC_BACKEND:-}
if [ -z "$backend" ] && [ -n "${CLAUDE_SYNC_REMOTE:-}" ]; then
  backend=git
fi
if [ -z "$backend" ]; then
  say "$(t "공용 설정을 어디에 둘까요?  1) git 저장소 (GitLab·GitHub)   2) Notion 페이지" "Where should the shared settings live?  1) a git repository (GitLab, GitHub)   2) a Notion page")"
  ask kind_answer "$(t "저장소 종류 (1/2)" "Storage (1/2)")" "1"
  case "$kind_answer" in 2|notion|Notion) backend=notion ;; *) backend=git ;; esac
fi
case "$backend" in git|notion) ;; *) fail "$(t "저장소 종류는 git 또는 notion 이에요: $backend" "The storage is git or notion: $backend")" ;; esac
remote=${CLAUDE_SYNC_REMOTE:-}
page=${CLAUDE_SYNC_NOTION_PAGE:-}
mode=ssh
if [ "$backend" = notion ]; then
  mode=notion
  if [ -z "$page" ]; then
    say "$(t "동기화용 Notion 페이지 주소를 붙여 넣으세요. 그 페이지의 … → 연결(Connections)에" "Paste the address of the Notion page to sync with. First add the Claude Sync")"
    say "$(t "Claude Sync용 연결을 먼저 추가해야 해요." "integration to that page under … → Connections.")"
    ask page "$(t "Notion 페이지 주소" "Notion page address")" ""
  fi
  printf '%s' "$page" | grep -Eq '[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}' \
    || fail "$(t "Notion 페이지 주소에서 페이지 id(32자리)를 찾지 못했어요: $page" "No page id (32 characters) in the Notion page address: $page")"
else
  if [ -z "$remote" ]; then
    say "$(t "동기화 저장소 주소를 입력하세요. 예: https://gitlab.com/아이디/claude-config.git, https://github.com/아이디/claude-config.git" "Enter the sync repository's address, e.g. https://gitlab.com/you/claude-config.git or https://github.com/you/claude-config.git")"
    ask remote "$(t "동기화 저장소 주소" "Sync repository address")" ""
  fi
  [ -n "$remote" ] || fail "$(t "동기화 저장소 주소가 필요해요." "The sync repository's address is needed.")"
  case "$remote" in
    https://*) mode=https ;;
    file://*) [ "${CLAUDE_SYNC_TEST_HTTPS_LIKE:-}" = 1 ] && mode=https ;;
  esac
fi

# 3. 이 PC 이름
pc=${CLAUDE_SYNC_PC:-}
if [ -z "$pc" ]; then
  ask pc "$(t "이 PC의 이름 (영문·숫자·하이픈)" "This PC's name (letters, digits, hyphens)")" "company-mac"
fi
case "$pc" in
  *[!A-Za-z0-9-]*|"") fail "$(t "PC 이름은 영문·숫자·하이픈만 쓸 수 있어요: $pc" "A PC name may use only letters, digits and hyphens: $pc")" ;;
esac

# 3b. 금지어
if [ -n "${CLAUDE_SYNC_WORDS+set}" ]; then
  words=$CLAUDE_SYNC_WORDS
else
  say ""
  say "$(t "이 PC 밖으로 나가면 안 되는 단어를 쉼표로 적어 주세요 (회사명, 내부 도메인, 서버 이름 등)." "List, separated by commas, the words that must never leave this PC (company name, internal domains, server names...).")"
  say "$(t "공용 파일에 이 단어가 들어가면 올리지 않고 알려 드려요. 나중에 /sync 에서 고칠 수 있어요." "A shared file that contains one is not uploaded, and you are told. You can change them later in /sync.")"
  ask words "$(t "금지어" "Blocked words")" ""
fi

# 3c. 받기 전용: 회사처럼 바깥으로 올리기가 막힌(또는 막아야 하는) PC
receive_only=${CLAUDE_SYNC_RECEIVE_ONLY:-}
if [ -z "$receive_only" ]; then
  say ""
  say "$(t "이 PC를 '받기 전용'으로 둘까요? 다른 PC의 설정은 계속 받고, 이 PC에서는 아무것도 올리지 않아요." "Make this PC 'receive only'? It keeps receiving the other PCs' settings and uploads nothing.")"
  say "$(t "회사 PC처럼 외부 저장소로 올리기가 막혀 있거나, 회사 정보가 나가면 안 되는 PC라면 y를 권해요." "For a PC that cannot upload to outside storage, or whose company information must not leave (a work PC), y is recommended.")"
  [ "$backend" = notion ] && say "$(t "Notion을 쓰더라도 회사 PC에서 올리기를 켤지는 회사 규정을 보고 정하세요." "Even with Notion, check your company's rules before letting a work PC upload.")"
  ask ro_answer "$(t "받기 전용 (y/n)" "Receive only (y/n)")" "n"
  case "$ro_answer" in y|Y|yes|YES|예|네) receive_only=1 ;; *) receive_only=0 ;; esac
fi
setup_args=(setup --pc "$pc" --words "$words")
[ "$receive_only" = 1 ] && setup_args+=(--receive-only)

report() { # report <engine JSON>
  printf '%s' "$1" | /usr/bin/python3 -I -c '
import json, os, sys
T = lambda ko, en: en if os.environ.get("CLAUDE_SYNC_LANG") == "en" else ko
r = json.load(sys.stdin)
if r.get("error") and r.get("result") is None:
    print("✗", r["error"]); sys.exit(1)
print(T("✓ 적용 결과:", "✓ Applied:"), r.get("result"), T("· 백업:", "· backup:"), r.get("backup"))
for o in r.get("overwritten") or []:
    print(T("  공용 값으로 바뀐 설정:", "  Setting changed to the shared value:"), ".".join(o["path"]), o["local"], "→", o["shared"])
for c in r.get("collisions") or []:
    print(T("  이름이 겹쳐 건너뛴 공용 항목:", "  Shared item skipped (same name here):"), "%s/%s" % (c["kind"], c["name"]))
for p in r.get("missing_plugins") or []:
    print(T("  이 PC에 없는 플러그인 (직접 설치 필요):", "  Plugin missing on this PC (install it yourself):"), p)
for k in r.get("managed_settings_keys") or []:
    print(T("  회사 관리 설정이 정한 항목 (mod가 막힐 수 있음):", "  Set by managed (company) settings, which may block mods:"), k)
if r.get("result") not in ("ok", None):
    print(T("  상태:", "  Status:"), r.get("result"), r.get("error") or "")
'
}

finish() {
  say ""
  say "$(t "끝났어요. Claude Code를 다시 시작하면 입력창 위에 동기화 상태가 보이고 /sync 로 앱을 열 수 있어요." "Done. Restart Claude Code: the sync status shows above the prompt, and /sync opens the app.")"
  say "$(t "Codex 리뷰 지침을 쓰려면 이 PC에도 codex 설치와 codex login 이 필요해요." "To use the Codex review instructions, this PC also needs codex installed and codex login run.")"
}

if [ "$mode" = notion ]; then
  # 4c. Notion 비밀 키: 키체인에만 저장하고, 화면·파일·인자에는 남기지 않음 (엔진에 표준 입력으로 넘김)
  if [ -n "${CLAUDE_SYNC_NOTION_SECRET+set}" ]; then
    secret=$CLAUDE_SYNC_NOTION_SECRET
  elif [ -n "$quiet" ]; then
    fail "$(t "Notion 비밀 키가 필요해요." "The Notion secret key is needed.")"
  else
    read -r -s -p "$(t "Notion 연결의 비밀 키 (붙여 넣어도 화면에 보이지 않아요): " "The Notion integration's secret key (hidden as you paste): ")" secret </dev/tty || true
    say ""
  fi
  secret=$(printf '%s' "$secret" | tr -d '[:space:]')
  [ -n "$secret" ] || fail "$(t "Notion 비밀 키가 비어 있어요." "The Notion secret key is empty.")"
  page_id=$(printf '%s' "$page" | /usr/bin/python3 -I -c 'import re, sys
text = sys.stdin.read().split("?")[0].split("#")[0]
ids = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", text)
print(ids[-1].replace("-", "").lower() if ids else "")')
  [ -n "$page_id" ] || fail "$(t "Notion 페이지 주소에서 페이지 id(32자리)를 찾지 못했어요: $page" "No page id (32 characters) in the Notion page address: $page")"
  error_of() { # the engine's JSON error text
    printf '%s' "$1" | /usr/bin/python3 -I -c 'import json, os, sys
T = lambda ko, en: en if os.environ.get("CLAUDE_SYNC_LANG") == "en" else ko
try:
    print(json.load(sys.stdin).get("error") or T("설정에 실패했어요", "Setup failed"))
except Exception:
    print(T("설정에 실패했어요", "Setup failed"))'
  }
  # 엔진: 이 설치 파일 끝에 들어 있는 것(scripts/build-installer.sh)을 풀어 씀.
  unpack_engine() {
    local at
    at=$(grep -an '^__CLAUDE_SYNC_ENGINE__$' "$0" | tail -n 1 | cut -d: -f1 || true)
    [ -n "$at" ] || fail "$(t "이 설치 파일에는 동기화 엔진이 들어 있지 않아요. 배포용 설치 파일(dist)을 쓰세요." "This installer has no sync engine inside. Use the release installer (dist).")"
    unpack=$(mktemp -d)
    trap 'rm -rf "$unpack"' EXIT
    tail -n +"$((at + 1))" "$0" \
      | /usr/bin/python3 -I -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))' \
      | tar -xzf - -C "$unpack" || fail "$(t "설치 파일 안의 엔진을 풀지 못했어요." "Could not unpack the engine inside the installer.")"
    engine=$unpack/config-sync/bin/claude-sync
  }
  if [ -e "$shared" ] && [ ! -d "$shared/.git" ]; then
    fail "$(t "$shared 가 이미 있습니다. 옮기거나 지운 뒤 다시 실행하세요." "$shared already exists. Move or delete it, then run this again.")"
  fi
  # 처음이든 다시 실행이든 이 설치 파일에 든 엔진의 join에 맡겨요. join은 같은 페이지면 비밀 키를 새로 저장하고
  # 다시 적용하고, 받기 전에 멈춘 설치는 처음부터 다시 하고, 다른 저장소면 거절해요.
  unpack_engine
  join_args=(join --backend notion --page "$page_id" --pc "$pc" --words "$words")
  [ "$receive_only" = 1 ] && join_args+=(--receive-only)
  say "$(t "Notion에서 공용 설정을 받아 이 PC에 적용하는 중…" "Fetching the shared settings from Notion and applying them to this PC…")"
  out=$(printf '%s\n' "$secret" | "$engine" "${join_args[@]}") \
    || { unset secret; fail "$(error_of "$out")"; }
  unset secret
  report "$out"
  [ -x "$shared/mods/config-sync/bin/claude-sync" ] \
    || fail "$(t "공용 설정은 받았지만 공용 저장소에 동기화 mod(config-sync)가 없어 이 PC는 앞으로 동기화하지 못해요. 첫 PC에서 config-sync mod를 공용으로 보낸 뒤 이 설치 파일을 다시 실행하세요." "The shared settings arrived, but the shared storage has no sync mod (config-sync), so this PC cannot sync from now on. Share the config-sync mod from the first PC, then run this installer again.")"
  finish
  exit 0
fi

if [ "$mode" = https ]; then
  # 4a. 액세스 토큰: 키체인에만 저장하고, 화면·파일·git 설정에는 남기지 않음
  # (GitLab 프로젝트 액세스 토큰과 GitHub fine-grained 토큰은 사용자 이름을 따지지 않아 PC 이름을 그대로 씁니다)
  token_user=${CLAUDE_SYNC_TOKEN_USER:-$pc}
  case "$token_user" in
    *[!A-Za-z0-9._+@-]*|"") fail "$(t "토큰 사용자 이름이 올바르지 않아요." "Not a valid token user name.")" ;;
  esac
  if [ -n "${CLAUDE_SYNC_TOKEN+set}" ]; then
    token=$CLAUDE_SYNC_TOKEN
  elif [ -n "$quiet" ]; then
    fail "$(t "액세스 토큰이 필요해요." "An access token is needed.")"
  else
    read -r -s -p "$(t "액세스 토큰 — GitLab 프로젝트 토큰 또는 GitHub fine-grained 토큰 (붙여 넣어도 화면에 보이지 않아요): " "Access token — a GitLab project token or a GitHub fine-grained token (hidden as you paste): ")" token </dev/tty || true
    say ""
  fi
  [ -n "$token" ] || fail "$(t "액세스 토큰이 비어 있어요." "The access token is empty.")"
  if [ "${remote#https://}" != "$remote" ]; then
    rest=${remote#https://}
    host=${rest%%/*}
    path=${rest#*/}
    case "$host/$path" in
      *[!A-Za-z0-9./_-]*|*/|/*) fail "$(t "주소가 올바르지 않아요: $remote" "Not a valid address: $remote")" ;;
    esac
    case "$path" in *.git) ;; *) fail "$(t "주소는 .git 으로 끝나야 해요: $remote" "The address must end in .git: $remote")" ;; esac
    clone_url="https://${token_user//@/%40}@$host/$path"
  else
    host=test.invalid
    path=${remote#file:///}
    clone_url=$remote
  fi
  say "$(t "토큰을 키체인에 저장하는 중…" "Saving the token in the keychain…")"
  printf 'protocol=https\nhost=%s\npath=%s\nusername=%s\npassword=%s\n\n' "$host" "$path" "$token_user" "$token" \
    | git -c credential.helper= -c "credential.helper=$helper" -c credential.useHttpPath=true credential approve \
    || fail "$(t "토큰을 키체인에 저장하지 못했어요." "Could not save the token in the keychain.")"

  say "$(t "서버 연결 확인 중… (최대 ${probe_limit}초)" "Checking the connection… (up to ${probe_limit} s)")"
  if probe env GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=$helper" -c credential.useHttpPath=true ls-remote "$clone_url"; then rc=0; else rc=$?; fi
  [ "$rc" -eq 2 ] && fail "$(t "저장소가 ${probe_limit}초 안에 답하지 않았어요. 네트워크를 확인하고 다시 실행하세요." "The repository did not answer within ${probe_limit} s. Check the network and run this again.")"
  [ "$rc" -eq 0 ] || fail "$(t "저장소에 연결하지 못했어요. 주소와 토큰(read/write 권한)을 확인하세요." "Could not reach the repository. Check the address and the token (read/write access).")"
  say "$(t "✓ 저장소에 연결됐어요." "✓ Connected to the repository.")"
else
  # 4b. SSH: 전용 키와 ssh 설정, 처음이면 공개키 등록을 기다림
  command -v ssh >/dev/null || fail "$(t "ssh가 없습니다." "ssh is missing.")"
  if [ -z "${CLAUDE_SYNC_SKIP_SSH:-}" ]; then
    # The server's address first, so a missing one stops before anything in ~/.ssh is made.
    need_alias=
    if [ "${remote#"$alias_name":}" != "$remote" ] && ! grep -qs "^Host $alias_name\$" "$HOME/.ssh/config"; then
      need_alias=1
      [ -n "$ssh_host" ] || ask ssh_host "$(t "동기화 서버 주소(IP 또는 도메인)" "Sync server address (IP or domain)")" ""
      [ -n "$ssh_host" ] || fail "$(t "동기화 서버 주소가 필요해요. CLAUDE_SYNC_SSH_HOST로 주거나, https 주소로 다시 실행하세요." "The sync server's address is needed. Give it as CLAUDE_SYNC_SSH_HOST, or run this again with an https address.")"
      case "$ssh_host" in *[!A-Za-z0-9.:-]*|-*) fail "$(t "서버 주소가 올바르지 않아요: $ssh_host" "Not a valid server address: $ssh_host")" ;; esac
    fi
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    if [ ! -f "$key" ]; then
      ssh-keygen -q -t ed25519 -N '' -C "$pc" -f "$key"
    fi
    touch "$HOME/.ssh/config"
    if [ -n "$need_alias" ]; then
      {
        printf '\nHost %s\n' "$alias_name"
        printf '  HostName %s\n  User claudesync\n  IdentityFile %s\n' "$ssh_host" "$key"
        printf '  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n  ConnectTimeout 5\n'
      } >> "$HOME/.ssh/config"
    fi
  fi
  clone_url=$remote
  blocked=$(t "회사 네트워크가 SSH를 막고 있을 수 있어요 — https 주소로 다시 실행하세요." "The company network may be blocking SSH — run this again with an https address.")
  say "$(t "서버 연결 확인 중… (최대 ${probe_limit}초)" "Checking the connection… (up to ${probe_limit} s)")"
  if probe env GIT_SSH_COMMAND="$ssh_cmd" git ls-remote "$remote"; then rc=0; else rc=$?; fi
  [ "$rc" -eq 2 ] && fail "$blocked"
  if [ "$rc" -ne 0 ]; then
    [ -n "$quiet" ] && fail "$(t "서버에 연결할 수 없습니다: $remote" "Cannot reach the server: $remote")"
    say ""
    say "$(t "아직 이 PC가 서버에 등록되지 않았어요. 아래 공개키 한 줄을 복사해서" "This PC is not registered with the server yet. Copy the public key line below")"
    say "$(t "개인 Mac의 Claude Code에서 /sync → '새 PC 등록' 칸에 붙여 넣어 주세요." "and paste it into /sync → 'Register a new PC' in Claude Code on your personal Mac.")"
    say "$(t "(공개키는 다른 사람이 봐도 괜찮은 값입니다)" "(A public key is safe for others to see)")"
    say ""
    cat "$key.pub"
    say ""
    say "$(t "등록될 때까지 기다립니다… (최대 30분, 그만두려면 Ctrl+C. 나중에 다시 실행해도 됩니다)" "Waiting for the registration… (up to 30 min; Ctrl+C to stop. You can also run this again later)")"
    tries=0
    while true; do
      sleep 10
      if probe env GIT_SSH_COMMAND="$ssh_cmd" git ls-remote "$remote"; then rc=0; else rc=$?; fi
      [ "$rc" -eq 0 ] && break
      [ "$rc" -eq 2 ] && fail "$blocked"
      tries=$((tries + 1))
      [ "$tries" -ge 180 ] && fail "$(t "30분 안에 등록되지 않았어요. 등록한 뒤 이 파일을 다시 실행하세요." "Not registered within 30 min. Register, then run this file again.")"
    done
    say "$(t "✓ 서버에 연결됐어요." "✓ Connected to the server.")"
  fi
fi

# 5. 공용 저장소 받기
if [ -d "$shared/.git" ]; then
  current=$(git -C "$shared" remote get-url origin 2>/dev/null || true)
  [ "$current" = "$clone_url" ] || [ "$current" = "$remote" ] \
    || fail "$(t "$shared 에 다른 저장소가 있습니다 ($current). 옮기거나 지운 뒤 다시 실행하세요." "$shared holds another repository ($current). Move or delete it, then run this again.")"
elif [ -e "$shared" ]; then
  fail "$(t "$shared 가 이미 있습니다. 옮기거나 지운 뒤 다시 실행하세요." "$shared already exists. Move or delete it, then run this again.")"
else
  say "$(t "공용 설정을 받는 중… (최대 ${clone_limit}초)" "Fetching the shared settings… (up to ${clone_limit} s)")"
  rc=0
  if [ "$mode" = https ]; then
    bounded "$clone_limit" env GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=$helper" \
      -c credential.useHttpPath=true clone -q "$clone_url" "$shared" || rc=$?
  else
    bounded "$clone_limit" env GIT_SSH_COMMAND="$ssh_cmd" git clone -q "$remote" "$shared" || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    rm -rf "$shared"
    [ "$rc" -eq 124 ] && fail "$(t "공용 저장소를 ${clone_limit}초 안에 받지 못했어요. 네트워크를 확인하고 다시 실행하세요." "Could not fetch the shared repository within ${clone_limit} s. Check the network and run this again.")"
    fail "$(t "공용 저장소를 받지 못했어요." "Could not fetch the shared repository.")"
  fi
fi
engine=$shared/mods/config-sync/bin/claude-sync
[ -x "$engine" ] || fail "$(t "공용 저장소에 동기화 엔진이 없습니다: $engine" "The shared repository has no sync engine: $engine")"

if [ "$mode" = https ]; then
  # 저장소 안에 연결 설정(주소·키체인 사용)을 남김. 토큰은 표준 입력으로만 넘김.
  printf '%s\n' "$token" | "$engine" set-remote "$remote" --username "$token_user" >/dev/null \
    || fail "$(t "저장소 연결 설정을 저장하지 못했어요." "Could not save the repository connection settings.")"
  unset token
fi

# 7. 적용
say "$(t "공용 설정을 이 PC에 적용하는 중…" "Applying the shared settings to this PC…")"
out=$("$engine" "${setup_args[@]}") || fail "$(t "설정 적용에 실패했어요: $out" "Could not apply the settings: $out")"
report "$out"
finish
exit 0
