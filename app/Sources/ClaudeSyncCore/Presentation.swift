import Foundation

public enum Tint: Sendable, Equatable {
    case normal, attention, alert
}

/// What the menu bar shows: an SF Symbol, how loud, one line, and the mode when not the usual.
public struct MenuBarState: Equatable, Sendable {
    public var symbol: String
    public var tint: Tint
    public var headline: String
    public var modeLabel: String?
}

/// The storage in two lines: what it is, and where.
public struct StorageText: Equatable, Sendable {
    public var title: String
    public var detail: String

    public init(title: String, detail: String) {
        self.title = title
        self.detail = detail
    }
}

/// The same wording as the Claude Code `/sync` mod, so both say the same thing (in Korean or English).
public enum Presentation {
    public static let symbolOK = "arrow.triangle.2.circlepath"
    public static let symbolAttention = "exclamationmark.circle"
    public static let symbolAlert = "exclamationmark.triangle.fill"

    public static func ago(_ seconds: Double) -> String {
        if seconds < 60 { return L10n.t("방금", "just now") }
        if seconds < 3600 { return L10n.t("\(Int(seconds / 60))분 전", "\(Int(seconds / 60)) min ago") }
        if seconds < 86_400 { return L10n.t("\(Int(seconds / 3600))시간 전", "\(Int(seconds / 3600)) h ago") }
        return L10n.t("\(Int(seconds / 86_400))일 전", "\(Int(seconds / 86_400)) d ago")
    }

    public static func clock(_ at: Double, now: Double, calendar: Calendar = .current) -> String {
        let date = Date(timeIntervalSince1970: at)
        let parts = calendar.dateComponents([.month, .day, .hour, .minute], from: date)
        let hm = String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
        if calendar.isDate(date, inSameDayAs: Date(timeIntervalSince1970: now)) { return L10n.t("오늘 \(hm)", "today \(hm)") }
        return "\(parts.month ?? 0)/\(parts.day ?? 0) \(hm)"
    }

    /// The label of the link that opens the storage in the browser.
    public static func storageLink(_ s: Storage?) -> String {
        if s?.backend == .notion { return L10n.t("Notion에서 페이지 열기", "Open the page in Notion") }
        if s?.backend == .folder { return L10n.t("Finder에서 열기", "Open in Finder") }
        let title = storage(s).title
        return ["GitHub", "GitLab"].contains(title) ? L10n.t("\(title)에서 열기", "Open in \(title)")
                                                    : L10n.t("브라우저에서 열기", "Open in the browser")
    }

    public static func storage(_ s: Storage?, home: String = NSHomeDirectory()) -> StorageText {
        guard let s else { return StorageText(title: L10n.t("저장소 정보 없음", "No storage info"), detail: "") }
        switch s.backend {
        case .git:
            let address = s.remote.hasPrefix("https://") ? String(s.remote.dropFirst("https://".count)) : s.remote
            let host = Wizard.gitHost(s.remote)
            let title = Wizard.isGitHub(s.remote) ? "GitHub" : host.contains("gitlab") ? "GitLab" : L10n.t("git 저장소", "git repository")
            let user = L10n.t("토큰 이름 \(s.username)", "token name \(s.username)")
            return StorageText(title: title, detail: s.username.isEmpty ? address : "\(address) · \(user)")
        case .notion:
            let how = s.auth == "oauth" ? L10n.t("Notion 로그인으로 연결", "Signed in with Notion")
                : L10n.t("비밀 키로 연결", "Connected with a secret key")
            return StorageText(title: "Notion", detail: s.page.isEmpty ? how
                               : "\(how) · \(L10n.t("페이지", "page")) \(s.page.prefix(8))…")
        case .folder:
            return StorageText(title: StorageKind.folder.title, detail: CloudFolders.describe(s.folder, home: home))
        }
    }

    /// What a group of shared items is, for its ⓘ.
    public static func groupHelp(_ kind: String) -> String {
        switch kind {
        case "claude_md":
            return L10n.t("모든 대화에 자동으로 들어가는 공통 지시문이에요 (shared/CLAUDE.md).",
                          "Shared instructions added to every conversation (shared/CLAUDE.md).")
        case "settings":
            return L10n.t("Claude Code 설정 파일(settings.json) 중 PC끼리 같게 맞추는 항목이에요. 나머지 설정은 이 PC 전용이에요.",
                          "The Claude Code settings (settings.json) kept the same on every PC. Other settings stay on this PC.")
        case "skills":
            return L10n.t("Claude가 특정 작업을 할 때 꺼내 쓰는 지침·도구 묶음이에요. 이름 옆 ⓘ를 누르면 설명이 보여요.",
                          "Bundles of instructions and tools Claude uses for particular tasks. Click ⓘ next to a name to see what it does.")
        case "mods":
            return L10n.t("Claude Code의 화면과 동작을 바꾸는 확장이에요(입력창 위 띠, 위험한 명령 막기 등). 이름 옆 ⓘ를 누르면 설명이 보여요.",
                          "Extensions that change how Claude Code looks and works (a band above the prompt, blocking risky commands…). Click ⓘ next to a name to see what it does.")
        case "local":
            return L10n.t("이 Mac에만 있고 다른 PC로 가지 않는 항목이에요. '공용으로 보내기'로 함께 쓰게 할 수 있어요.",
                          "Items only on this Mac; they do not go to other PCs. 'Share' makes one shared.")
        default: return ""
        }
    }

    private static let settingTexts: [String: (String, String)] = [
        "theme": ("화면 테마", "Theme"),
        "effortLevel": ("모델이 얼마나 깊게 생각할지 (노력 수준)", "How hard the model thinks (effort level)"),
        "model": ("기본 모델", "Default model"),
        "language": ("답변 언어", "Answer language"),
        "outputStyle": ("답변 스타일", "Answer style"),
        "permissions": ("도구 사용 권한 규칙", "Tool permission rules"),
        "permissions.defaultMode": ("도구를 쓸 때의 기본 권한 모드", "Default permission mode for tools"),
        "extraKnownMarketplaces": ("추가로 등록한 플러그인 마켓", "Extra plugin marketplaces"),
        "agentPushNotifEnabled": ("에이전트 푸시 알림 켜기", "Agent push notifications"),
        "inputNeededNotifEnabled": ("입력이 필요할 때 알림 켜기", "Notify when input is needed"),
        "skipWorkflowUsageWarning": ("워크플로 사용량 경고 건너뛰기", "Skip the workflow usage warning"),
        "teammateMode": ("에이전트 팀원(teammate) 실행 방식", "How agent teammates run"),
        "hooks": ("훅: 도구 실행 전후 등에 자동으로 도는 명령", "Hooks: commands run around tool use and other events"),
        "statusLine": ("입력창 아래 상태 줄", "Status line under the prompt"),
        "autoMode": ("자동 모드 설정", "Auto mode settings"),
        "skipDangerousModePermissionPrompt": ("위험 모드 확인 창 건너뛰기", "Skip the dangerous-mode confirmation"),
        "env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": ("대화를 자동으로 줄이기(압축) 시작하는 비율",
                                                "When to start compacting the conversation (percent)"),
        "env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": ("에이전트 팀 실험 기능 켜기", "Experimental agent teams"),
    ]

    /// A settings key in a few words (known keys and families; others by name).
    public static func settingHelp(_ key: String) -> String {
        if let text = settingTexts[key] { return L10n.t(text.0, text.1) }
        func rest(_ prefix: String) -> String? { key.hasPrefix(prefix) ? String(key.dropFirst(prefix.count)) : nil }
        if let plugin = rest("enabledPlugins.") {
            let parts = plugin.split(separator: "@", maxSplits: 1).map(String.init)
            return parts.count == 2
                ? L10n.t("켜 둔 플러그인: \(parts[0]) (마켓 \(parts[1]))", "Enabled plugin: \(parts[0]) (marketplace \(parts[1]))")
                : L10n.t("켜 둔 플러그인: \(plugin)", "Enabled plugin: \(plugin)")
        }
        if let skill = rest("skillOverrides.") { return L10n.t("스킬 \(skill) 켜기·끄기", "Turn skill \(skill) on or off") }
        if let model = rest("modelSettings.") { return L10n.t("모델 \(model)의 세부 설정", "Settings for model \(model)") }
        if let name = rest("env.") {
            return L10n.t("Claude Code에 넘기는 환경 변수 \(name)", "Environment variable \(name) for Claude Code")
        }
        if let top = key.split(separator: ".").first.map(String.init), top != key, let text = settingTexts[top] {
            return "\(L10n.t(text.0, text.1)) (\(key))"
        }
        return L10n.t("Claude Code 설정 \(key)", "Claude Code setting \(key)")
    }

    public static func menuBar(_ status: Status?, syncing: Bool, failure: EngineFailure?) -> MenuBarState {
        let mode = status?.receiveOnly == true ? L10n.t("받기 전용", "Receive only") : nil
        if syncing {
            return MenuBarState(symbol: symbolOK, tint: .normal, headline: L10n.t("동기화 중…", "Syncing…"), modeLabel: mode)
        }
        // The latest engine call failed: an older good status must not hide that.
        if failure != nil {
            return MenuBarState(symbol: symbolAlert, tint: .alert, headline: L10n.t("동기화 엔진 오류", "Sync engine error"),
                                modeLabel: mode)
        }
        guard let s = status else {
            return MenuBarState(symbol: symbolOK, tint: .normal, headline: L10n.t("상태를 읽는 중…", "Reading status…"),
                                modeLabel: nil)
        }
        let alert = { (text: String) in MenuBarState(symbol: symbolAlert, tint: .alert, headline: text, modeLabel: mode) }
        switch s.lastResult {
        case .blocked: return alert(L10n.t("금지어 때문에 올리기를 멈췄어요", "Sending stopped: a blocked word was found"))
        case .conflict: return alert(L10n.t("충돌 \(s.conflicts.count)건", "\(s.conflicts.count) conflict(s)"))
        case .offline: return alert(L10n.t("서버에 연결하지 못했어요", "Could not reach the server"))
        case .error: return alert(L10n.t("동기화 오류", "Sync error"))
        case .needsApproval:
            let who = s.pendingApproval.first?.commits.first?.pc ?? L10n.t("다른 PC", "another PC")
            return MenuBarState(symbol: symbolAttention, tint: .attention,
                                headline: L10n.t("확인 필요 · \(who)의 실행 파일 변경", "Check needed · executable change from \(who)"),
                                modeLabel: mode)
        case .never:
            return MenuBarState(symbol: symbolOK, tint: .normal, headline: L10n.t("아직 동기화 전", "Not synced yet"), modeLabel: mode)
        case .ok: break
        }
        if s.incoming > 0 {
            return MenuBarState(symbol: symbolAttention, tint: .attention,
                                headline: L10n.t("받을 것 \(s.incoming)개", "\(s.incoming) to receive"), modeLabel: mode)
        }
        let last = s.lastPull.map { ago((s.now ?? Date().timeIntervalSince1970) - $0) } ?? L10n.t("기록 없음", "never")
        return MenuBarState(symbol: symbolOK, tint: .normal, headline: L10n.t("동기화 \(last) ✓", "Synced \(last) ✓"), modeLabel: mode)
    }

    /// Whether this PC may send: off for a receive-only PC.
    public static func canSend(_ status: Status) -> Bool { !status.receiveOnly }

    public static func label(_ state: ItemState) -> String {
        switch state {
        case .ok: return L10n.t("✓ 최신", "✓ Up to date")
        case .local: return L10n.t("↑ 이 PC에서 바뀜", "↑ Changed here")
        case .incoming: return L10n.t("↓ 받을 것 있음", "↓ Incoming")
        case .conflict: return L10n.t("⚠ 충돌", "⚠ Conflict")
        }
    }

    public static func label(_ result: LastResult) -> String {
        switch result {
        case .ok: return L10n.t("● 정상", "● OK")
        case .offline: return L10n.t("● 서버 연결 안 됨", "● Offline")
        case .blocked: return L10n.t("● 올리기 멈춤 — 금지어 발견", "● Sending stopped — blocked word found")
        case .conflict: return L10n.t("● 충돌", "● Conflict")
        case .needsApproval: return L10n.t("● 확인 필요", "● Check needed")
        case .error: return L10n.t("● 오류", "● Error")
        case .never: return L10n.t("● 아직 동기화 전", "● Not synced yet")
        }
    }

    public static func groupState(_ items: [ItemStatus]) -> ItemState {
        for s in [ItemState.conflict, .incoming, .local] where items.contains(where: { $0.state == s }) { return s }
        return .ok
    }
}

/// When the app runs an automatic sync, and never two at once.
public struct SyncScheduler: Equatable, Sendable {
    public static let intervals = [5, 15, 30, 60]
    public private(set) var intervalMinutes: Int
    public private(set) var running = false
    public private(set) var lastStarted: Date?

    public init(intervalMinutes: Int) {
        self.intervalMinutes = Self.intervals.contains(intervalMinutes) ? intervalMinutes : 15
    }

    /// A new interval; a sync already running stays running.
    public mutating func setInterval(_ minutes: Int) {
        intervalMinutes = Self.intervals.contains(minutes) ? minutes : 15
    }

    public mutating func begin(now: Date) -> Bool {
        if running { return false }
        running = true
        lastStarted = now
        return true
    }

    public mutating func end() {
        running = false
    }

    public func isDue(now: Date, autoEnabled: Bool) -> Bool {
        guard autoEnabled, !running else { return false }
        guard let last = lastStarted else { return true }
        return now.timeIntervalSince(last) >= Double(intervalMinutes * 60)
    }
}

/// One engine change at a time — a sync, a fix, setup, a blocklist edit — from every window and timer.
/// Status reads run beside it.
public struct WorkGate: Equatable, Sendable {
    public static let sync = "동기화"
    public private(set) var current: String?

    public init() {}

    public var syncing: Bool { current == Self.sync }

    public mutating func begin(_ label: String) -> Bool {
        if current != nil { return false }
        current = label
        return true
    }

    public mutating func end() {
        current = nil
    }
}
