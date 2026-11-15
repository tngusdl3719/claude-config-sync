import Foundation

/// The engine's JSON, read leniently: a field the engine leaves out (an older or newer
/// engine) falls back to empty rather than failing the whole decode.
public enum EngineJSON {
    public static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }
}

/// Any JSON value, for setting values shown to the person as they are.
public indirect enum JSONValue: Decodable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), null
    case array([JSONValue]), object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    /// The value as JSON text, e.g. `"dark"`, `3`, `true`.
    public var display: String {
        switch self {
        case .string(let s): return "\"\(s)\""
        case .number(let n): return n == n.rounded() && abs(n) < 1e15 ? String(Int(n)) : String(n)
        case .bool(let b): return b ? "true" : "false"
        case .null: return "null"
        case .array(let a): return "[" + a.map(\.display).joined(separator: ", ") + "]"
        case .object(let o): return "{" + o.keys.sorted().map { "\"\($0)\": \(o[$0]!.display)" }.joined(separator: ", ") + "}"
        }
    }
}

public enum LastResult: String, Sendable {
    case ok, offline, blocked, conflict, needsApproval = "needs_approval", error, never
}

public enum ItemState: String, Sendable {
    case ok, local, incoming, conflict
}

extension KeyedDecodingContainer {
    func lenient<T: Decodable>(_ key: Key, _ fallback: T) -> T {
        (try? decodeIfPresent(T.self, forKey: key)) ?? fallback
    }
}

public struct ItemStatus: Decodable, Equatable, Sendable {
    public var name: String
    public var state: ItemState
    enum CodingKeys: String, CodingKey { case name, state }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.lenient(.name, "")
        state = ItemState(rawValue: c.lenient(.state, "ok")) ?? .ok
    }
}

public struct StateOnly: Decodable, Equatable, Sendable {
    public var state: ItemState
    enum CodingKeys: String, CodingKey { case state }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = ItemState(rawValue: c.lenient(.state, "ok")) ?? .ok
    }
}

public struct SettingsItem: Decodable, Equatable, Sendable {
    public var count: Int
    public var keys: [String]
    public var state: ItemState
    enum CodingKeys: String, CodingKey { case count, keys, state }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        count = c.lenient(.count, 0)
        keys = c.lenient(.keys, [])
        state = ItemState(rawValue: c.lenient(.state, "ok")) ?? .ok
    }
}

public struct Items: Decodable, Equatable, Sendable {
    public var claudeMd: StateOnly?
    public var settings: SettingsItem?
    public var skills: [ItemStatus]
    public var mods: [ItemStatus]
    enum CodingKeys: String, CodingKey { case claudeMd, settings, skills, mods }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        claudeMd = c.lenient(.claudeMd, nil)
        settings = c.lenient(.settings, nil)
        skills = c.lenient(.skills, [])
        mods = c.lenient(.mods, [])
    }
}

public struct LocalOnly: Decodable, Equatable, Sendable {
    public var skills: [String]
    public var mods: [String]
    public var settings: [String]
    enum CodingKeys: String, CodingKey { case skills, mods, settings }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        skills = c.lenient(.skills, [])
        mods = c.lenient(.mods, [])
        settings = c.lenient(.settings, [])
    }
}

public struct OtherPC: Decodable, Equatable, Sendable {
    public var pc: String
    public var time: Double
}

public struct Conflict: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var kind: String
    public var file: String?
    public var path: [String]?
    public var local: JSONValue?
    public var remote: JSONValue?
    enum CodingKeys: String, CodingKey { case id, kind, file, path, local, remote }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenient(.id, "")
        kind = c.lenient(.kind, "file")
        file = c.lenient(.file, nil)
        path = c.lenient(.path, nil)
        local = c.lenient(.local, nil)
        remote = c.lenient(.remote, nil)
    }
}

public struct Hit: Decodable, Equatable, Sendable {
    public var file: String
    public var line: Int
    public var text: String
    public var word: String
    enum CodingKeys: String, CodingKey { case file, line, text, word }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        file = c.lenient(.file, "")
        line = c.lenient(.line, 0)
        text = c.lenient(.text, "")
        word = c.lenient(.word, "")
    }
}

public struct ApprovalCommit: Decodable, Equatable, Sendable {
    public var sha: String
    public var pc: String
    public var time: Double
    public var subject: String
}

public struct Approval: Decodable, Equatable, Sendable {
    public var remote: String
    public var files: [String]
    public var settings: [[String]]
    public var commits: [ApprovalCommit]
    enum CodingKeys: String, CodingKey { case remote, files, settings, commits }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        remote = c.lenient(.remote, "")
        files = c.lenient(.files, [])
        settings = c.lenient(.settings, [])
        commits = c.lenient(.commits, [])
    }
}

public struct Collision: Decodable, Equatable, Sendable {
    public var kind: String
    public var name: String
    public var reason: String
}

public struct Overwritten: Decodable, Equatable, Sendable {
    public var path: [String]
    public var local: JSONValue?
    public var shared: JSONValue?
}

/// `claude-sync status` (and the status the mutating commands print after they ran).
/// Where this PC's shared history lives (`status.storage`); never a secret.
public struct Storage: Decodable, Equatable, Sendable {
    public var backend: Backend
    /// git: the address without the user name.
    public var remote: String
    /// git: the name the token is stored under.
    public var username: String
    /// Notion: the page id, how this PC signs in ("oauth" or "secret"), and the page's address.
    public var page: String
    public var auth: String
    public var url: String

    enum CodingKeys: String, CodingKey { case backend, remote, username, page, auth, url }

    public init(backend: Backend, remote: String = "", username: String = "", page: String = "", auth: String = "",
                url: String = "") {
        self.backend = backend
        self.remote = remote
        self.username = username
        self.page = page
        self.auth = auth
        self.url = url
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let where_: String? = c.lenient(.backend, nil)
        backend = where_.flatMap(Backend.init(rawValue:)) ?? .git
        remote = c.lenient(.remote, "")
        username = c.lenient(.username, "")
        page = c.lenient(.page, "")
        auth = c.lenient(.auth, "")
        url = c.lenient(.url, "")
    }
}

/// What each skill and mod says it is (from its own SKILL.md or plugin.json), by name; shared and
/// PC-only apart, since a PC-only item may carry the name of a shared one.
public struct ItemDescriptions: Decodable, Equatable, Sendable {
    public struct Kinds: Decodable, Equatable, Sendable {
        public var skills: [String: String]
        public var mods: [String: String]

        enum CodingKeys: String, CodingKey { case skills, mods }

        public init(skills: [String: String] = [:], mods: [String: String] = [:]) {
            self.skills = skills
            self.mods = mods
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            skills = c.lenient(.skills, [:])
            mods = c.lenient(.mods, [:])
        }
    }

    public var shared: Kinds
    public var local: Kinds

    enum CodingKeys: String, CodingKey { case shared, local }

    public init(shared: Kinds = Kinds(), local: Kinds = Kinds()) {
        self.shared = shared
        self.local = local
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        shared = c.lenient(.shared, Kinds())
        local = c.lenient(.local, Kinds())
    }

    public func of(_ kind: String, _ name: String, local isLocal: Bool) -> String? {
        let kinds = isLocal ? local : shared
        return (kind == "skills" ? kinds.skills : kinds.mods)[name]
    }
}

public struct Status: Decodable, Equatable, Sendable {
    public var pc: String?
    public var now: Double?
    public var lastPull: Double?
    public var lastPush: Double?
    public var lastResult: LastResult
    public var lastError: String?
    public var autoEnabled: Bool
    public var requireApproval: Bool
    public var receiveOnly: Bool
    public var backend: Backend
    public var storage: Storage?
    public var descriptions: ItemDescriptions
    public var others: [OtherPC]
    public var items: Items?
    public var localOnly: LocalOnly?
    public var incoming: Int
    public var outgoing: Int
    public var conflicts: [Conflict]
    public var blocked: [Hit]
    public var pendingApproval: [Approval]
    public var collisions: [Collision]
    public var overwritten: [Overwritten]
    public var words: [String]

    enum CodingKeys: String, CodingKey {
        case pc, now, lastPull, lastPush, lastResult, lastError, autoEnabled, requireApproval, receiveOnly, backend
        case storage, descriptions, others, items, localOnly, incoming, outgoing, conflicts, blocked, pendingApproval, collisions
        case overwritten, words
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pc = c.lenient(.pc, nil)
        now = c.lenient(.now, nil)
        lastPull = c.lenient(.lastPull, nil)
        lastPush = c.lenient(.lastPush, nil)
        let raw: String? = c.lenient(.lastResult, nil)
        lastResult = raw.map { LastResult(rawValue: $0) ?? .error } ?? .never
        lastError = c.lenient(.lastError, nil)
        autoEnabled = c.lenient(.autoEnabled, true)
        requireApproval = c.lenient(.requireApproval, false)
        receiveOnly = c.lenient(.receiveOnly, false)
        let where_: String? = c.lenient(.backend, nil)
        backend = where_.flatMap(Backend.init(rawValue:)) ?? .git
        storage = c.lenient(.storage, nil)
        descriptions = c.lenient(.descriptions, ItemDescriptions())
        others = c.lenient(.others, [])
        items = c.lenient(.items, nil)
        localOnly = c.lenient(.localOnly, nil)
        incoming = c.lenient(.incoming, 0)
        outgoing = c.lenient(.outgoing, 0)
        conflicts = c.lenient(.conflicts, [])
        blocked = c.lenient(.blocked, [])
        pendingApproval = c.lenient(.pendingApproval, [])
        collisions = c.lenient(.collisions, [])
        overwritten = c.lenient(.overwritten, [])
        words = c.lenient(.words, [])
    }
}

public struct LogEntry: Decodable, Equatable, Sendable, Identifiable {
    public var sha: String
    public var time: Double
    public var pc: String
    public var subject: String
    public var files: [String]
    public var id: String { sha }
    enum CodingKeys: String, CodingKey { case sha, time, pc, subject, files }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sha = c.lenient(.sha, "")
        time = c.lenient(.time, 0)
        pc = c.lenient(.pc, "")
        subject = c.lenient(.subject, "")
        files = c.lenient(.files, [])
    }
}

/// What `join`, `init` and `setup` print.
public struct JoinResult: Decodable, Equatable, Sendable {
    public var result: String?
    public var error: String?
    public var backup: String?
    public var overwritten: [Overwritten]
    public var collisions: [Collision]
    public var missingPlugins: [String]
    public var managedSettingsKeys: [String]
    enum CodingKeys: String, CodingKey {
        case result, error, backup, overwritten, collisions, missingPlugins, managedSettingsKeys
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        result = c.lenient(.result, nil)
        error = c.lenient(.error, nil)
        backup = c.lenient(.backup, nil)
        overwritten = c.lenient(.overwritten, [])
        collisions = c.lenient(.collisions, [])
        missingPlugins = c.lenient(.missingPlugins, [])
        managedSettingsKeys = c.lenient(.managedSettingsKeys, [])
    }
}

/// `{"error": ...}` with the blocklist hits a refused share carries.
public struct EngineErrorReply: Decodable, Equatable, Sendable {
    public var error: String
    public var hits: [Hit]
    enum CodingKeys: String, CodingKey { case error, hits }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        error = try c.decode(String.self, forKey: .error)
        hits = c.lenient(.hits, [])
    }
}

public struct DiffReply: Decodable, Equatable, Sendable {
    public var text: String
}

public struct WordsReply: Decodable, Equatable, Sendable {
    public var words: [String]
}

public struct CandidateKey: Decodable, Equatable, Sendable, Identifiable {
    public var key: String
    public var valuePreview: String?
    public var id: String { key }
}

public struct ClaudeMdInfo: Decodable, Equatable, Sendable {
    public var exists: Bool
    public var lines: Int
}

/// `claude-sync candidates`: what a first PC could share.
public struct Candidates: Decodable, Equatable, Sendable {
    public var skills: [String]
    public var mods: [String]
    public var keys: [CandidateKey]
    public var claudeMd: ClaudeMdInfo?
    enum CodingKeys: String, CodingKey { case skills, mods, keys, claudeMd }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        skills = c.lenient(.skills, [])
        mods = c.lenient(.mods, [])
        keys = c.lenient(.keys, [])
        claudeMd = c.lenient(.claudeMd, nil)
    }
}
