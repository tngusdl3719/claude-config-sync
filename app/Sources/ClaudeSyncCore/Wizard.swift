import Foundation

/// The storages the app offers: two git hosts (the engine treats them alike) and Notion.
public enum StorageKind: String, CaseIterable, Identifiable, Sendable {
    case gitlab, github, notion, folder

    public var id: Self { self }
    public var title: String {
        switch self {
        case .gitlab: return "GitLab"
        case .github: return "GitHub"
        case .notion: return "Notion"
        case .folder: return L10n.t("동기화 폴더", "Sync folder")
        }
    }
    public var backend: Backend {
        switch self {
        case .gitlab, .github: return .git
        case .notion: return .notion
        case .folder: return .folder
        }
    }

    /// One line on the storage page's list.
    public var blurb: String {
        switch self {
        case .gitlab: return L10n.t("GitLab 저장소에 액세스 토큰으로 올리고 받아요", "A GitLab repository, with an access token")
        case .github: return L10n.t("GitHub 비공개 저장소에 토큰으로 올리고 받아요", "A private GitHub repository, with a token")
        case .notion: return L10n.t("Notion 페이지에 로그인해서 올리고 받아요", "A Notion page you sign in to")
        case .folder: return L10n.t("iCloud Drive·Dropbox·Google Drive 같은 동기화 폴더에 파일로 올리고 받아요",
                                    "A folder a sync app carries (iCloud Drive, Dropbox, Google Drive…), as files")
        }
    }

    /// The title of the sheet that moves this PC here (GitLab, GitHub, Notion take 으로; 폴더, 로).
    public var moveTitle: String {
        self == .folder ? L10n.t("동기화 폴더로 옮기기", "Move to a sync folder") : L10n.t("\(title)으로 옮기기", "Move to \(title)")
    }

    /// What a configured PC uses (a git remote on github.com is GitHub; any other git host, GitLab-like).
    public static func of(_ storage: Storage?) -> StorageKind {
        guard let storage else { return .gitlab }
        if storage.backend == .notion { return .notion }
        if storage.backend == .folder { return .folder }
        return Wizard.isGitHub(storage.remote) ? .github : .gitlab
    }

    /// The git host a typed address is on, so its help matches it; until one is typed, the tab chosen.
    public static func forAddress(_ text: String, chosen: StorageKind) -> StorageKind {
        guard chosen.backend == .git, case .ok(let repo) = Wizard.checkRepoURL(text) else { return chosen }
        return Wizard.isGitHub(repo) ? .github : .gitlab
    }
}

/// Checks and helpers for the first-run setup screens.
public enum Wizard {
    public enum URLCheck: Equatable, Sendable {
        case ok(String)
        case invalid(String)
    }

    /// The settings a first PC shares by default (the first design's list).
    public static let recommendedKeys = [
        "effortLevel", "modelSettings", "theme", "permissions.defaultMode",
        "env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "enabledPlugins",
        "skillOverrides", "teammateMode", "inputNeededNotifEnabled", "agentPushNotifEnabled", "skipWorkflowUsageWarning",
    ]

    /// An https repository address, normalised to end in `.git`.
    public static func checkRepoURL(_ raw: String) -> URLCheck {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.hasPrefix("https://") else {
            return .invalid(L10n.t("https:// 로 시작하는 주소가 필요해요 (예: https://gitlab.com/아이디/claude-config.git)", "An address starting with https:// is needed (e.g. https://gitlab.com/you/claude-config.git)"))
        }
        if text.contains(where: { $0.isWhitespace }) { return .invalid(L10n.t("주소에 빈칸이 있어요", "The address has a space in it")) }
        while text.hasSuffix("/") { text.removeLast() }
        guard let url = URL(string: text), let host = url.host, !host.isEmpty, url.user == nil, url.password == nil else {
            return .invalid(L10n.t("주소 형식이 올바르지 않아요", "The address does not look right"))
        }
        let path = url.path.split(separator: "/")
        guard path.count >= 2 else { return .invalid(L10n.t("저장소 경로(아이디/프로젝트)가 필요해요", "The repository path (you/project) is needed")) }
        if !text.hasSuffix(".git") { text += ".git" }
        return .ok(text)
    }

    /// Where this repository's token is made: GitHub's fine-grained token page, or the GitLab
    /// project's access tokens page.
    public static func accessTokensPage(_ repo: String) -> URL? {
        guard case .ok(let normal) = checkRepoURL(repo) else { return nil }
        if isGitHub(normal) {
            return URL(string: "https://github.com/settings/personal-access-tokens/new")
        }
        return URL(string: String(normal.dropLast(4)) + "/-/settings/access_tokens")
    }

    /// The host of a git address: https://[user@]host/…, ssh://[user@]host/…, or the scp form [user@]host:path.
    public static func gitHost(_ remote: String) -> String {
        if remote.contains("://") { return (URL(string: remote)?.host ?? "").lowercased() }
        let beforePath = remote.split(separator: ":", maxSplits: 1).first.map(String.init) ?? ""
        return (beforePath.split(separator: "@").last.map(String.init) ?? "").lowercased()
    }

    /// github.com itself (and its own hosts, like ssh.github.com). Other hosts, GitHub Enterprise among
    /// them, cannot be told from the address and are shown as a plain git host.
    public static func isGitHub(_ remote: String) -> Bool {
        let host = gitHost(remote)
        return host == "github.com" || host.hasSuffix(".github.com")
    }

    /// Where a new private repository is made.
    public static func newRepoPage(_ kind: StorageKind) -> URL {
        URL(string: kind == .github ? "https://github.com/new" : "https://gitlab.com/projects/new")!
    }

    /// Where a Notion integration (and its secret) is made.
    public static let notionIntegrationsPage = URL(string: "https://www.notion.so/profile/integrations")!

    /// The 32-hex page id inside a Notion page address (or a dashed id), lowercased.
    public static func checkNotionPage(_ raw: String) -> URLCheck {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let head = String(text.split(whereSeparator: { $0 == "?" || $0 == "#" }).first ?? "")
        let pattern = #"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return .invalid(L10n.t("확인하지 못했어요", "Could not check it")) }
        let matches = regex.matches(in: head, range: NSRange(head.startIndex..., in: head))
        guard let last = matches.last, let range = Range(last.range, in: head) else {
            return .invalid(L10n.t("Notion 페이지 주소에서 페이지 id(32자리)를 찾지 못했어요. 페이지의 … → 링크 복사로 받은 주소를 붙여 넣으세요", "No page id (32 characters) in that Notion address. Paste the address from the page's … → Copy link"))
        }
        return .ok(head[range].replacingOccurrences(of: "-", with: "").lowercased())
    }

    /// A computer name turned into what the engine accepts: lowercase letters, digits, hyphens.
    public static func sanitizePCName(_ raw: String) -> String {
        var out = ""
        for scalar in raw.lowercased().unicodeScalars {
            if ("a"..."z").contains(scalar) || ("0"..."9").contains(scalar) {
                out.unicodeScalars.append(scalar)
            } else if scalar == " " || scalar == "-" || scalar == "_" || scalar == "." {
                if !out.isEmpty && !out.hasSuffix("-") { out.append("-") }
            }
        }
        while out.hasSuffix("-") { out.removeLast() }
        if out.count > 40 { out = String(out.prefix(40)) }
        while out.hasSuffix("-") { out.removeLast() }
        return out.isEmpty ? "my-mac" : out
    }

    public static func isValidPCName(_ name: String) -> Bool {
        !name.isEmpty && name.unicodeScalars.allSatisfy {
            ("a"..."z").contains($0) || ("A"..."Z").contains($0) || ("0"..."9").contains($0) || $0 == "-"
        }
    }

    /// Blocklist words from free text: commas or new lines, trimmed, no repeats.
    public static func words(_ raw: String) -> [String] {
        var seen: [String] = []
        for part in raw.split(whereSeparator: { $0 == "," || $0.isNewline }) {
            let word = part.trimmingCharacters(in: .whitespaces)
            if !word.isEmpty && !seen.contains(word) { seen.append(word) }
        }
        return seen
    }

    public static func cleanToken(_ raw: String) -> String {
        raw.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
