import CryptoKit
import Foundation

/// A version like 0.6.0 (a leading "v" is fine), compared number by number: 0.10.0 is newer than 0.9.1.
public struct SemVer: Comparable, Sendable, CustomStringConvertible {
    public let parts: [Int]

    public init?(_ text: String) {
        var s = Substring(text.trimmingCharacters(in: .whitespaces))
        if s.hasPrefix("v") { s = s.dropFirst() }
        let pieces = s.split(separator: ".", omittingEmptySubsequences: false)
        guard (1...4).contains(pieces.count) else { return nil }
        var parts: [Int] = []
        for piece in pieces {
            guard !piece.isEmpty, piece.allSatisfy({ $0.isASCII && $0.isNumber }), let n = Int(piece) else { return nil }
            parts.append(n)
        }
        self.parts = parts
    }

    public var description: String { parts.map(String.init).joined(separator: ".") }

    private func padded(to count: Int) -> [Int] { parts + Array(repeating: 0, count: max(0, count - parts.count)) }

    public static func == (a: SemVer, b: SemVer) -> Bool {
        let n = max(a.parts.count, b.parts.count)
        return a.padded(to: n) == b.padded(to: n)
    }

    public static func < (a: SemVer, b: SemVer) -> Bool {
        let n = max(a.parts.count, b.parts.count)
        return a.padded(to: n).lexicographicallyPrecedes(b.padded(to: n))
    }
}

/// A newer release this app can install: its dmg and the dmg's signature.
public struct UpdateOffer: Equatable, Sendable {
    public let version: String
    public let dmg: URL
    public let signature: URL
}

/// Reads GitHub's latest release and decides whether it is an update for this app.
public enum UpdateCheck {
    public static let latestRelease = URL(string: "https://api.github.com/repos/\(AppInfo.repository)/releases/latest")!

    private struct Release: Decodable {
        struct Asset: Decodable {
            let name: String
            let browser_download_url: URL
        }
        let tag_name: String
        let draft: Bool?
        let prerelease: Bool?
        let assets: [Asset]
    }

    /// The offer when the release is newer than `current` and carries `ClaudeSync-<version>.dmg` and its
    /// `.sig` over https; nil otherwise (a draft, a pre-release, something unreadable). `allowFiles` also takes
    /// local files, for a test feed given on purpose (the signature is checked all the same).
    public static func offer(_ json: Data, current: String, allowFiles: Bool = false) -> UpdateOffer? {
        guard let release = try? JSONDecoder().decode(Release.self, from: json),
              release.draft != true, release.prerelease != true,
              let latest = SemVer(release.tag_name), let mine = SemVer(current), latest > mine else { return nil }
        let version = release.tag_name.hasPrefix("v") ? String(release.tag_name.dropFirst()) : release.tag_name
        let name = "ClaudeSync-\(version).dmg"
        func url(_ asset: String) -> URL? {
            release.assets.first {
                $0.name == asset && ($0.browser_download_url.scheme == "https" || allowFiles && $0.browser_download_url.isFileURL)
            }?.browser_download_url
        }
        guard let dmg = url(name), let signature = url(name + ".sig") else { return nil }
        return UpdateOffer(version: version, dmg: dmg, signature: signature)
    }
}

/// Ed25519: the release key's signature over the whole dmg.
public enum UpdateSignature {
    public static func isValid(_ data: Data, signature: String, publicKey: String) -> Bool {
        guard let raw = Data(base64Encoded: publicKey),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw),
              let sig = Data(base64Encoded: signature.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return key.isValidSignature(sig, for: data)
    }
}

/// The version an engine folder (the app's Resources/engine, or the shared mods/config-sync) says it is.
public enum EngineVersion {
    public static func read(_ root: String) -> String? {
        guard let text = try? String(contentsOfFile: root + "/engine/claude_sync/__init__.py", encoding: .utf8),
              let pattern = try? Regex(#"(?m)^__version__ = "([0-9]+(?:\.[0-9]+)*)""#),
              let match = text.firstMatch(of: pattern),
              let version = match.output[1].substring else { return nil }
        return String(version)
    }

    /// The app's engine is newer than the shared one (one too old to say its version counts as older).
    public static func newer(bundled: String, than shared: String) -> Bool {
        guard let mine = read(bundled).flatMap(SemVer.init) else { return false }
        guard let theirs = read(shared).flatMap(SemVer.init) else { return true }
        return mine > theirs
    }
}

/// Why an update did not go ahead.
public enum UpdateFailure: Error, Equatable, Sendable {
    case offline, http(Int), badSignature, notThisApp, noPermission(String), broken(String)

    /// Asking GitHub failed (as against taking the update).
    public var whileChecking: Bool {
        switch self {
        case .offline, .http: return true
        default: return false
        }
    }

    public var text: String {
        switch self {
        case .offline: return L10n.t("GitHub에 연결할 수 없어요", "cannot reach GitHub")
        case .http(let code): return L10n.t("GitHub가 \(code)로 답했어요", "GitHub answered \(code)")
        case .badSignature: return L10n.t("서명이 맞지 않아 설치하지 않았어요", "the signature did not match, so it was not installed")
        case .notThisApp: return L10n.t("받은 파일이 이 앱이 아니라 설치하지 않았어요", "the download was not this app, so it was not installed")
        case .noPermission(let place): return L10n.t("\(place)에 쓸 권한이 없어요", "no permission to write in \(place)")
        case .broken(let what): return what
        }
    }
}

/// Where taking an update stands.
public enum UpdatePhase: Equatable, Sendable {
    /// `downloading` carries how much of the dmg has come (0…1), nil while its size is not known.
    case idle, checking, upToDate, downloading(String, Double?), preparing(String), ready(String), installing(String),
         failed(UpdateFailure)

    /// The bar under the status: none, one that only shows work going on, or how far the download has got.
    public var bar: UpdateBar? {
        switch self {
        case .checking, .downloading(_, .none), .preparing, .installing: return .busy
        case .downloading(_, let part?): return .part(part)
        case .idle, .upToDate, .ready, .failed: return nil
        }
    }
}

public enum UpdateBar: Equatable, Sendable {
    case busy, part(Double)
}

extension Presentation {
    /// `automatic`: whether a downloaded update goes in by itself (the settings' switch).
    public static func update(_ phase: UpdatePhase, automatic: Bool = true) -> String {
        switch phase {
        case .idle: return L10n.t("아직 확인하지 않았어요", "Not checked yet")
        case .checking: return L10n.t("업데이트 확인 중…", "Checking for updates…")
        case .upToDate: return L10n.t("최신 버전이에요 (\(AppInfo.version))", "Up to date (\(AppInfo.version))")
        case .downloading(let v, let part):
            let done = part.map { " \(Int(($0 * 100).rounded(.down)))%" } ?? ""
            return L10n.t("\(v) 받는 중…", "Downloading \(v)…") + done
        case .preparing(let v): return L10n.t("\(v) 서명을 확인하고 준비하는 중…", "Checking the signature of \(v)…")
        case .ready(let v):
            return automatic
                ? L10n.t("새 버전(\(v))을 받아 두었어요. 하던 작업이 끝나면 설치하고 다시 시작해요",
                         "\(v) is ready. It installs and restarts once the running work is done")
                : L10n.t("새 버전(\(v))을 받아 두었어요. '설치하고 다시 시작'을 누르면 설치해요",
                         "\(v) is ready. Press Install and restart to install it")
        case .installing(let v): return L10n.t("\(v) 설치하는 중… 끝나면 다시 시작해요", "Installing \(v)… the app restarts when it is done")
        case .failed(let f):
            return f.whileChecking
                ? L10n.t("업데이트를 확인하지 못했어요: ", "Could not check for updates: ") + f.text
                : L10n.t("업데이트하지 못했어요: ", "Could not update: ") + f.text
        }
    }
}

/// A download that says how far it has got: `progress` gets the part done (0…1, or nil once when the size is
/// not known), only going forward, from the download's own thread. The file returned is the caller's to keep.
public enum UpdateDownload {
    public static func fetch(_ url: URL, progress: @escaping @Sendable (Double?) -> Void) async throws -> (URL, URLResponse) {
        let watcher = DownloadWatcher(progress: progress)
        let session = URLSession(configuration: .ephemeral, delegate: watcher, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        return try await withCheckedThrowingContinuation { done in
            watcher.done = done
            session.downloadTask(with: url).resume()
        }
    }
}

/// Called on the session's one delegate queue, one call at a time.
private final class DownloadWatcher: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let progress: @Sendable (Double?) -> Void
    var done: CheckedContinuation<(URL, URLResponse), Error>?
    private var shown: Double?
    private var toldUnknown = false

    init(progress: @escaping @Sendable (Double?) -> Void) { self.progress = progress }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten written: Int64, totalBytesExpectedToWrite expected: Int64) {
        guard expected > 0 else {
            if !toldUnknown { toldUnknown = true; progress(nil) }
            return
        }
        let part = min(1, Double(written) / Double(expected))
        if let shown, part < 1 && part - shown < 0.01 || part <= shown { return }  // a step of 1% at least
        shown = part
        progress(part)
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // The session removes its file once this returns.
        let kept = FileManager.default.temporaryDirectory.appendingPathComponent("ClaudeSync-\(UUID().uuidString).download")
        do {
            try FileManager.default.moveItem(at: location, to: kept)
            done?.resume(returning: (kept, downloadTask.response ?? URLResponse()))
        } catch {
            done?.resume(throwing: error)
        }
        done = nil
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        done?.resume(throwing: error ?? URLError(.unknown))
        done = nil
    }
}

/// A downloaded release: its dmg checked with the release key, then the app inside copied out, kept only
/// when it is this app (bundle id) at the promised version. Blocking (hdiutil, ditto): run it off the main thread.
public enum UpdatePackage {
    public static func unpack(dmg: URL, signature: String, version: String, bundleID: String, publicKey: String,
                              into dir: URL) throws -> URL {
        let fm = FileManager.default
        guard UpdateSignature.isValid(try Data(contentsOf: dmg, options: .mappedIfSafe), signature: signature,
                                      publicKey: publicKey) else { throw UpdateFailure.badSignature }
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let app = dir.appendingPathComponent("Claude Sync.app")
        try? fm.removeItem(at: app)
        let mount = dir.appendingPathComponent("mount")
        try fm.createDirectory(at: mount, withIntermediateDirectories: true)
        try runTool("/usr/bin/hdiutil", ["attach", "-quiet", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount.path, dmg.path])
        do {
            defer { _ = try? runTool("/usr/bin/hdiutil", ["detach", "-quiet", "-force", mount.path]) }
            try runTool("/usr/bin/ditto", [mount.appendingPathComponent("Claude Sync.app").path, app.path])
        }
        try? fm.removeItem(at: mount)
        let info = NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
        guard info?["CFBundleIdentifier"] as? String == bundleID, info?["CFBundleShortVersionString"] as? String == version else {
            try? fm.removeItem(at: app)
            throw UpdateFailure.notThisApp
        }
        return app
    }
}

/// Putting an unpacked update in place of the installed app: a whole copy is made beside it (same disk),
/// then the two are exchanged in one step, so the place never holds half an app.
public enum UpdateInstall {
    /// The hidden name beside the installed app where the new copy is made (and the old one waits after the swap).
    public static func incoming(for target: URL) -> URL {
        target.deletingLastPathComponent().appendingPathComponent("." + target.lastPathComponent + ".incoming")
    }

    /// Copies the staged app beside the installed one, which stays as it is.
    public static func prepare(_ staged: URL, for target: URL) throws -> URL {
        let incoming = incoming(for: target)
        try? FileManager.default.removeItem(at: incoming)
        do {
            try runTool("/usr/bin/ditto", [staged.path, incoming.path])
        } catch {
            try? FileManager.default.removeItem(at: incoming)
            throw error
        }
        return incoming
    }

    /// Exchanges the new copy and the installed app in one step (APFS): the new app is in place and the old
    /// one at the incoming name, or, when it fails, nothing changed.
    public static func swap(_ incoming: URL, into target: URL) throws {
        guard renamex_np(incoming.path, target.path, UInt32(RENAME_SWAP)) == 0 else {
            throw UpdateFailure.broken(String(cString: strerror(errno)))
        }
    }

    /// What an install cut short left beside the app: a half-made new copy, or the old one.
    public static func cleanUp(for target: URL) {
        try? FileManager.default.removeItem(at: incoming(for: target))
    }
}

/// hdiutil and ditto, waited for; a failure names the tool and its exit code.
private func runTool(_ executable: String, _ args: [String]) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = args
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        throw UpdateFailure.broken("\((executable as NSString).lastPathComponent) \(process.terminationStatus)")
    }
}
