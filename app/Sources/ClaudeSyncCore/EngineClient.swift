import Foundation

public enum EngineFailure: Error, Equatable, Sendable {
    case timedOut
    case notFound(String)
    /// The engine answered `{"error": ...}`.
    case failed(String, hits: [Hit])
    /// Anything else: the tail of what it printed.
    case badOutput(String)

    public var message: String {
        switch self {
        case .timedOut: return L10n.t("시간 초과: 동기화 엔진이 제때 끝나지 않았어요", "Timed out: the sync engine did not finish in time")
        case .notFound(let path): return L10n.t("동기화 엔진을 찾지 못했어요: ", "Sync engine not found: ") + path
        case .failed(let text, _): return text
        case .badOutput(let text): return text
        }
    }
}

/// An answer the app only needs to know succeeded.
public struct EngineOK: Decodable, Equatable, Sendable {
    public init(from decoder: Decoder) throws {}
}

public enum Timeouts {
    public static let quick: TimeInterval = 20
    public static let sync: TimeInterval = 180
    public static let setup: TimeInterval = 600
}

/// Where the shared history lives.
public enum Backend: String, Equatable, Sendable {
    case git, notion, folder
}

/// A Notion public connection (spec §7) for 'Notion으로 연결'. Its secret is never inside the app, which
/// everyone may download: the app's own connection signs in through a broker that holds the secret
/// (server/notion-broker), and a Mac with a connection of its own keeps that in its keychain.
public struct NotionOAuthClient: Equatable, Sendable {
    public var clientID: String
    /// A secret of this Mac's own (scripts/set-notion-oauth.sh); empty when a broker holds it.
    public var clientSecret: String
    /// The broker that holds the secret; nil for a Mac with its own.
    public var broker: String?

    public init(clientID: String, clientSecret: String = "", broker: String? = nil) {
        self.clientID = clientID
        self.clientSecret = clientSecret
        self.broker = broker
    }

    /// What `notion-login` reads on stdin: the id with the broker, or with this Mac's secret.
    public var engineInput: [String: String] {
        broker.map { ["client_id": clientID, "broker": $0] } ?? ["client_id": clientID, "client_secret": clientSecret]
    }

    /// The app's own connection, through its broker (nil in a build that names no broker).
    public static let builtIn: NotionOAuthClient? = AppInfo.notionBroker.isEmpty || AppInfo.notionClientID.isEmpty
        ? nil : NotionOAuthClient(clientID: AppInfo.notionClientID, broker: AppInfo.notionBroker)

    /// A Mac's own connection when it has one, else the app's.
    public static func choose(keychain: NotionOAuthClient?) -> NotionOAuthClient? { keychain ?? builtIn }

    /// This Mac's connection, read once (the app sees a keychain change when it next starts).
    public static let thisMac = choose(keychain: fromKeychain())

    /// The keychain item scripts/set-notion-oauth.sh makes (account = client id, password = client secret),
    /// read through `security`, which made it and so may read it without asking. Nil when there is none.
    public static func fromKeychain(security: String = "/usr/bin/security",
                                    item: String = "claude-sync-notion-oauth") -> NotionOAuthClient? {
        guard let attributes = output(security, ["find-generic-password", "-s", item]),
              let pattern = try? Regex(#""acct"<blob>="([^"]+)""#),
              let match = attributes.firstMatch(of: pattern),
              let id = match.output[1].substring.map(String.init),
              let secret = output(security, ["find-generic-password", "-s", item, "-w"])?
                  .trimmingCharacters(in: .whitespacesAndNewlines),
              !id.isEmpty, !secret.isEmpty else { return nil }
        return NotionOAuthClient(clientID: id, clientSecret: secret)
    }

    private static func output(_ executable: String, _ args: [String]) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = args
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return process.terminationStatus == 0 ? String(decoding: data, as: UTF8.self) : nil
    }
}

/// What `adopt-engine` did: whether the shared engine was replaced, from which version to which.
public struct AdoptResult: Decodable, Equatable, Sendable {
    public var adopted: Bool
    public var from: String?
    public var to: String?
    /// "receive_only" when this PC leaves the shared repo alone.
    public var reason: String?

    public init(adopted: Bool, from: String?, to: String?, reason: String?) {
        self.adopted = adopted
        self.from = from
        self.to = to
        self.reason = reason
    }
}

public struct NotionPage: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var title: String

    public init(id: String, title: String) {
        self.id = id
        self.title = title
    }
}

/// What `notion-login` prints: the pages the person shared while signing in.
public struct NotionLogin: Decodable, Equatable, Sendable {
    public var pages: [NotionPage]
    public var workspace: String

    enum CodingKeys: String, CodingKey { case pages, workspace }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pages = (try? c.decode([NotionPage].self, forKey: .pages)) ?? []
        workspace = (try? c.decode(String.self, forKey: .workspace)) ?? ""
    }
}

/// What a first PC puts in the shared repo.
public struct InitChoice: Equatable, Sendable {
    public var remote: String
    public var username: String
    public var pc: String
    public var skills: [String]
    public var mods: [String]
    public var keys: [String]
    public var moveClaudeMd: Bool
    public var requireApproval: Bool
    public var backend: Backend
    public var notionPage: String
    /// Blocklist words, in place before the first upload.
    public var words: [String] = []
    /// Notion reached through 'Notion으로 연결' (the engine holds the token) rather than a pasted secret.
    public var notionSignedIn = false
    /// A folder storage: the folder (its sync app carries the files).
    public var folder = ""

    public init(remote: String, username: String, pc: String, skills: [String], mods: [String], keys: [String],
                moveClaudeMd: Bool, requireApproval: Bool, backend: Backend = .git, notionPage: String = "") {
        self.remote = remote
        self.username = username
        self.pc = pc
        self.skills = skills
        self.mods = mods
        self.keys = keys
        self.moveClaudeMd = moveClaudeMd
        self.requireApproval = requireApproval
        self.backend = backend
        self.notionPage = notionPage
    }
}

/// One typed call per engine command. The token, where there is one, goes on stdin only.
public struct EngineClient: Sendable {
    public var runner: EngineRunner

    public init(runner: EngineRunner) {
        self.runner = runner
    }

    func call<T: Decodable>(_ type: T.Type, _ args: [String], stdin: String? = nil,
                            timeout: TimeInterval) async -> Result<T, EngineFailure> {
        let out = await runner.run(args, stdin: stdin, timeout: timeout)
        if out.timedOut { return .failure(.timedOut) }
        if out.exitCode == 127 && out.stdout.isEmpty { return .failure(.notFound(runner.executable)) }
        if out.exitCode == 0, let value = try? EngineJSON.decode(T.self, from: out.stdout) {
            return .success(value)
        }
        if let reply = try? EngineJSON.decode(EngineErrorReply.self, from: out.stdout) {
            return .failure(.failed(reply.error, hits: reply.hits))
        }
        let printed = String(decoding: out.stdout, as: UTF8.self)
        let tail = [out.stderr, printed]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty }
        let lines = tail?.split(separator: "\n").suffix(3).joined(separator: "\n")
        return .failure(.badOutput(lines ?? L10n.t("동기화 엔진이 아무것도 출력하지 않았어요 (종료 코드 \(out.exitCode))", "The sync engine printed nothing (exit code \(out.exitCode))")))
    }

    public func status() async -> Result<Status, EngineFailure> {
        await call(Status.self, ["status"], timeout: Timeouts.quick)
    }

    public func sync(auto: Bool = false) async -> Result<Status, EngineFailure> {
        await call(Status.self, auto ? ["sync", "--auto"] : ["sync"], timeout: Timeouts.sync)
    }

    public func log(_ count: Int = 30) async -> Result<[LogEntry], EngineFailure> {
        await call([LogEntry].self, ["log", "-n", String(count)], timeout: Timeouts.quick)
    }

    public func diff(_ item: String) async -> Result<DiffReply, EngineFailure> {
        await call(DiffReply.self, ["diff", item], timeout: Timeouts.quick)
    }

    public func share(kind: String, name: String) async -> Result<EngineOK, EngineFailure> {
        await call(EngineOK.self, ["share", kind, name], timeout: Timeouts.sync)
    }

    public func unshare(kind: String, name: String) async -> Result<EngineOK, EngineFailure> {
        await call(EngineOK.self, ["unshare", kind, name], timeout: Timeouts.sync)
    }

    public func resolve(_ conflictID: String, take: Bool) async -> Result<Status, EngineFailure> {
        await call(Status.self, ["resolve", conflictID, take ? "take" : "keep"], timeout: Timeouts.sync)
    }

    public func approve() async -> Result<Status, EngineFailure> {
        await call(Status.self, ["approve"], timeout: Timeouts.sync)
    }

    public func reject() async -> Result<Status, EngineFailure> {
        await call(Status.self, ["reject"], timeout: Timeouts.sync)
    }

    public func allowOnce() async -> Result<Status, EngineFailure> {
        await call(Status.self, ["allow-once"], timeout: Timeouts.sync)
    }

    public func discardBlocked() async -> Result<Status, EngineFailure> {
        await call(Status.self, ["discard-blocked"], timeout: Timeouts.sync)
    }

    public func revert(_ sha: String) async -> Result<Status, EngineFailure> {
        await call(Status.self, ["revert", sha], timeout: Timeouts.sync)
    }

    public func setAuto(_ on: Bool) async -> Result<Status, EngineFailure> {
        await call(Status.self, ["auto", on ? "on" : "off"], timeout: Timeouts.quick)
    }

    public func words() async -> Result<WordsReply, EngineFailure> {
        await call(WordsReply.self, ["block", "list"], timeout: Timeouts.quick)
    }

    public func addWord(_ word: String) async -> Result<WordsReply, EngineFailure> {
        await call(WordsReply.self, ["block", "add", word], timeout: Timeouts.quick)
    }

    public func removeWord(_ word: String) async -> Result<WordsReply, EngineFailure> {
        await call(WordsReply.self, ["block", "remove", word], timeout: Timeouts.quick)
    }

    public func candidates() async -> Result<Candidates, EngineFailure> {
        await call(Candidates.self, ["candidates"], timeout: Timeouts.quick)
    }

    public func join(remote: String, username: String, pc: String, words: [String], receiveOnly: Bool = false,
                     token: String) async -> Result<JoinResult, EngineFailure> {
        var args = ["join", "--remote", remote, "--username", username, "--pc", pc,
                    "--words", words.joined(separator: ",")]
        if receiveOnly { args.append("--receive-only") }
        return await call(JoinResult.self, args, stdin: Self.stdinLine(token), timeout: Timeouts.setup)
    }

    /// A new PC reading a Notion page's "Claude Sync" database: with the integration secret on stdin,
    /// or (secret nil) with the token the last `notionLogin` left in the keychain.
    public func joinNotion(page: String, pc: String, words: [String], receiveOnly: Bool,
                           secret: String?) async -> Result<JoinResult, EngineFailure> {
        var args = ["join", "--backend", "notion", "--page", page, "--pc", pc, "--words", words.joined(separator: ",")]
        if receiveOnly { args.append("--receive-only") }
        if secret == nil { args.append("--oauth") }
        return await call(JoinResult.self, args, stdin: secret.map(Self.stdinLine), timeout: Timeouts.setup)
    }

    /// A new PC reading a synced folder.
    public func joinFolder(folder: String, pc: String, words: [String], receiveOnly: Bool) async -> Result<JoinResult, EngineFailure> {
        var args = ["join", "--backend", "folder", "--folder", folder, "--pc", pc, "--words", words.joined(separator: ",")]
        if receiveOnly { args.append("--receive-only") }
        return await call(JoinResult.self, args, timeout: Timeouts.setup)
    }

    /// 'Notion으로 연결': the engine opens the browser and waits (up to 5 minutes) for the consent.
    public func notionLogin(_ client: NotionOAuthClient) async -> Result<NotionLogin, EngineFailure> {
        let line = (try? JSONSerialization.data(withJSONObject: client.engineInput))
            .map { String(decoding: $0, as: UTF8.self) + "\n" } ?? "\n"
        return await call(NotionLogin.self, ["notion-login"], stdin: line, timeout: Timeouts.setup)
    }

    /// Move this PC to a git remote, or give it a new token (the token on stdin only).
    public func setGitStorage(remote: String, username: String, token: String) async -> Result<EngineOK, EngineFailure> {
        await call(EngineOK.self, ["set-remote", remote, "--username", username], stdin: Self.stdinLine(token),
                   timeout: Timeouts.setup)
    }

    /// Move this PC to a Notion page (or connect it again): with a pasted secret on stdin, or (secret nil)
    /// with the token the last `notionLogin` left in the keychain.
    public func setNotionStorage(page: String, secret: String?) async -> Result<EngineOK, EngineFailure> {
        var args = ["set-remote", "--backend", "notion", "--page", page]
        if secret == nil { args.append("--oauth") }
        return await call(EngineOK.self, args, stdin: secret.map(Self.stdinLine), timeout: Timeouts.setup)
    }

    /// Move this PC to a synced folder (or point it at the folder again).
    public func setFolderStorage(folder: String) async -> Result<EngineOK, EngineFailure> {
        await call(EngineOK.self, ["set-remote", "--backend", "folder", "--folder", folder], timeout: Timeouts.setup)
    }

    /// Puts the engine this app carries (`folder`) into the shared repo when it is newer; run with that engine.
    public func adoptEngine(from folder: String) async -> Result<AdoptResult, EngineFailure> {
        await call(AdoptResult.self, ["adopt-engine", folder], timeout: Timeouts.quick)
    }

    public func setReceiveOnly(_ on: Bool) async -> Result<Status, EngineFailure> {
        await call(Status.self, ["mode", on ? "receive-only" : "normal"], timeout: Timeouts.quick)
    }

    public func initFirstPC(_ c: InitChoice, token: String) async -> Result<JoinResult, EngineFailure> {
        var args = c.backend == .notion ? ["init", "--backend", "notion", "--page", c.notionPage, "--pc", c.pc]
            : c.backend == .folder ? ["init", "--backend", "folder", "--folder", c.folder, "--pc", c.pc]
            : ["init", "--remote", c.remote, "--username", c.username, "--pc", c.pc]
        args += ["--skills", c.skills.joined(separator: ","), "--mods", c.mods.joined(separator: ","),
                 "--keys", c.keys.joined(separator: ","), "--claude-md", c.moveClaudeMd ? "move" : "keep"]
        if c.requireApproval { args.append("--require-approval") }
        if !c.words.isEmpty { args += ["--words", c.words.joined(separator: ",")] }
        if c.backend == .git { args.append("--token-stdin") }
        if c.backend == .notion && c.notionSignedIn {
            args.append("--oauth")
            return await call(JoinResult.self, args, timeout: Timeouts.setup)
        }
        if c.backend == .folder { return await call(JoinResult.self, args, timeout: Timeouts.setup) }
        return await call(JoinResult.self, args, stdin: Self.stdinLine(token), timeout: Timeouts.setup)
    }

    static func stdinLine(_ token: String) -> String {
        token.trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
    }
}
