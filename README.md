# Claude Code 설정 동기화 (config-sync)

여러 Mac의 Claude Code 설정 중 **공용으로 지정한 것만**(지침 `CLAUDE.md`, 일부 설정, 스킬, mods) 비공개 저장소를 통해 주고받습니다. 저장소는 **GitLab·GitHub(비공개 git 저장소)** 또는 **Notion 페이지** 중에서 고릅니다. PC 전용 항목(회사 스킬, MCP, 메모리, 훅 등)은 밖으로 나가지 않고, 회사 PC에서는 금지어 검사로 회사 내용이 새어 나가는 것을 막습니다.

> **English:** Syncs only the parts of your Claude Code setup you mark as shared (CLAUDE.md, chosen settings, skills, mods) between Macs, through a private GitLab/GitHub repository or a Notion page. PC-only items never leave the Mac, and a blocklist stops company words from being uploaded. The Mac app (Claude Sync) speaks Korean and English (Settings → 언어 / Language).

## 구성

| 폴더 | 내용 |
|---|---|
| `engine/` | 동기화 엔진 (Python 3.9 표준 라이브러리만, `claude-sync` 명령, JSON 출력) |
| `mod/` | Claude Code 안의 화면 (`/sync`, 입력창 위 상태 줄) |
| `app/` | Mac 앱 Claude Sync (SwiftUI, macOS 14 이상) |
| `installer/` | 터미널용 설치 파일 (앱 없이 설정할 때) |
| `server/` | (선택) 직접 운영하는 SSH git 서버용 스크립트 |
| `scripts/` | 빌드·배포·진단 스크립트 |

## Mac 앱 (Claude Sync)

### 설치

1. `ClaudeSync-<버전>.zip`을 받아 풀고, `Claude Sync.app`을 **응용 프로그램** 폴더로 옮깁니다.
2. 처음 열 때 "확인되지 않은 개발자" 경고가 뜨면(애플 개발자 서명이 없는 앱이라서):
   - 시스템 설정 → **개인정보 보호 및 보안** → 아래쪽의 "Claude Sync"를 **그래도 열기** → 다시 열기.
   - 또는 Finder에서 앱을 **Control-클릭 → 열기**.
3. Dock과 메뉴 막대에 아이콘이 생기고 메인 창이 열립니다. 이 Mac에 아직 동기화 설정이 없으면 설정 안내가 먼저 나와요.

### 처음 설정 (안내 창)

- **다른 PC의 설정 받기**: 저장소 → 연결(토큰 또는 Notion 로그인) → PC 이름 → 금지어 순서로 진행합니다.
- **이 PC가 처음이에요**: 위 단계에 더해 공용으로 보낼 스킬·mods·설정을 고릅니다.
- 끝나면 Claude Code를 다시 시작하세요.

### 저장소 고르기

| 저장소 | 준비할 것 |
|---|---|
| **GitLab** | 비공개 프로젝트. Settings → Access tokens에서 PC마다 토큰(역할 **Developer**, 권한 `read_repository`·`write_repository`). 새 프로젝트라면 Settings → Repository → Protected branches에서 main 보호를 풀어 주세요. |
| **GitHub** | 비공개 저장소. README는 있어도 되지만 `.gitignore`·라이선스 템플릿은 고르지 마세요. 토큰은 Settings → Developer settings → **Fine-grained tokens**에서 *Only select repositories*로 그 저장소만, 권한 **Contents: Read and write**. |
| **Notion** | 동기화에 쓸 페이지 하나. **Notion으로 연결**(로그인) 또는 내부 연결(integration)의 **비밀 키**(`ntn_…`)로 연결합니다. |

- 토큰·비밀 키는 화면에 보이지 않게 입력받아 **이 Mac의 키체인에만** 저장합니다. 파일·로그·명령 인자에 남기지 않습니다.
- 모든 PC가 같은 저장소를 써야 서로 동기화돼요. 저장소는 나중에 앱의 **저장소** 칸에서 바꾸거나(GitLab·GitHub·Notion 사이) 다시 연결할 수 있습니다. 옮길 곳에 이 PC와 이어지지 않는 다른 기록이 있으면 옮기지 않고 그대로 둡니다.
- **Notion으로 연결**(로그인)은 앱을 빌드한 사람의 Notion 공개 연결이 앱에 들어 있을 때만 나옵니다(아래 "앱 빌드" 참고). 없으면 비밀 키로 연결하세요.

### 쓰는 법

- 메인 창: **상태**(지금 동기화·자동 동기화·확인할 문제), **항목**(공용·PC 전용, ⓘ에 마우스를 올리거나 누르면 설명), **저장소**, **금지어**, **기록**, **설정**.
- 메뉴 막대 아이콘에서도 지금 동기화와 상태를 볼 수 있고, Dock 아이콘을 누르면 메인 창이 다시 열립니다. 창을 닫아도 앱은 계속 돌며 자동 동기화합니다.
- 앱이 켜져 있으면 정해진 주기(기본 15분)마다, 그리고 Mac이 잠에서 깰 때 자동으로 동기화합니다. 주기·로그인할 때 실행·언어는 **설정**에서 바꿉니다.
- 스킬·mod 설명이 앱 언어와 다르면 macOS 15 이상에서는 기기 안 번역(Translation)으로 번역해 보여 줍니다.
- **받기 전용** PC는 받기만 하고 아무것도 올리지 않습니다(공용으로 보내기·빼기, 이번만 허용, 되돌리기가 숨겨져요).

## 터미널 설치 파일

앱 없이 설정하려면 `scripts/build-installer.sh`로 만든 `dist/claude-sync-install.command`를 새 Mac에서 실행합니다.

```bash
bash ~/Downloads/claude-sync-install.command
```

저장소 주소가 `https://`면 액세스 토큰으로, Notion을 고르면 비밀 키로 연결합니다. 직접 운영하는 SSH 서버(`claude-sync-server:…` 주소)를 쓰면 서버 주소를 묻습니다(`CLAUDE_SYNC_SSH_HOST`로 줄 수도 있어요).

## 개발

Xcode 없이 Command Line Tools의 Swift로 빌드합니다.

```bash
app/test.sh                                  # 앱 테스트 (Swift Testing)
cd engine/tests && python3 -m unittest discover -s . -p 'test_*.py'   # 엔진 테스트
scripts/build-app.sh                         # dist/Claude Sync.app 과 dist/ClaudeSync-<버전>.zip (ad-hoc 서명, 엔진 포함)
scripts/build-installer.sh                   # dist/claude-sync-install.command
scripts/deploy.sh                            # 엔진·mod를 공용 저장소의 mods/config-sync로 복사 (다음 동기화 때 다른 PC로)
```

- **Notion으로 연결**을 넣으려면 Notion에서 공개 연결(public connection)을 만들고(리디렉션 URI `http://localhost:47821/callback`), `bash scripts/set-notion-oauth.sh`로 client ID와 secret을 키체인에 넣은 뒤 앱을 빌드하세요. secret이 앱 안에 들어가므로 **직접 쓰는 앱에만** 쓰고, 그 앱 파일을 남에게 나눠 주지 마세요.
- `dist/`는 git에 올라가지 않습니다.
