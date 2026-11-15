import AppKit
import ClaudeSyncCore
import Foundation
import Observation

/// Takes updates from GitHub quietly (the person chose this): checks soon after launch and once a day,
/// downloads the release and checks its signature, unpacks the new app beside this one, and swaps it in
/// when nothing is going on (no engine work, no window, the app not in front) or when the app quits.
/// A Mac that cannot reach GitHub (a work Mac) only notes it. Turned off in the settings.
@MainActor @Observable
final class Updater {
    private(set) var phase: UpdatePhase = .idle
    var enabled: Bool {
        didSet {
            UserDefaults.standard.set(enabled, forKey: Self.enabledKey)
            if enabled { installIfIdle() }
        }
    }
    private var staged: (version: String, app: URL)?
    private var installing = false
    private var lastCheck: Date?
    private var timer: Timer?
    private let isIdle: () -> Bool

    static let enabledKey = "autoUpdate"
    static let checkEvery: TimeInterval = 24 * 3600
    /// Diagnostics: `--update-feed <file URL>` reads a release from a local file instead of GitHub, to try an
    /// update from start to end before publishing it. Its files must still carry the release key's signature.
    static let testFeed: URL? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--update-feed"), i + 1 < args.count,
              let url = URL(string: args[i + 1]), url.isFileURL else { return nil }
        return url
    }()

    init(isIdle: @escaping () -> Bool) {
        self.isIdle = isIdle
        enabled = UserDefaults.standard.object(forKey: Self.enabledKey) as? Bool ?? true
    }

    /// Where updates are downloaded and unpacked.
    static var folder: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(Bundle.main.bundleIdentifier ?? "com.suhyeon.claudesync")
            .appendingPathComponent("Updates")
    }

    func start() {
        UpdateInstall.cleanUp(for: Bundle.main.bundleURL)  // what an install cut short left beside this app
        takeEarlierDownload()
        Task {
            try? await Task.sleep(for: .seconds(20))
            if enabled { await check() }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        guard enabled else { return }
        if let lastCheck, Date().timeIntervalSince(lastCheck) < Self.checkEvery {
            installIfIdle()
        } else {
            Task { await check() }
        }
    }

    // MARK: checking and downloading

    private var busy: Bool {
        switch phase {
        case .checking, .downloading: return true
        default: return false
        }
    }

    /// Asks GitHub for the latest release; a newer one is downloaded, checked and unpacked, then installed
    /// when the time is right (or with the settings' button).
    func check() async {
        guard !busy else { return }
        phase = .checking
        lastCheck = Date()
        do {
            var request = URLRequest(url: Self.testFeed ?? UpdateCheck.latestRelease, timeoutInterval: 20)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, response) = try await URLSession.shared.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? (Self.testFeed != nil ? 200 : 0)
            guard code != 404 else {  // no release yet
                phase = .upToDate
                return
            }
            guard code == 200 else { throw UpdateFailure.http(code) }
            guard let offer = UpdateCheck.offer(data, current: AppInfo.version, allowFiles: Self.testFeed != nil) else {
                phase = staged.map { .ready($0.version) } ?? .upToDate
                return
            }
            if let staged, staged.version == offer.version {
                phase = .ready(offer.version)
            } else {
                phase = .downloading(offer.version)
                staged = (offer.version, try await Self.stage(offer, in: Self.folder))
                phase = .ready(offer.version)
            }
            installIfIdle()
        } catch let failure as UpdateFailure {
            phase = .failed(failure)
        } catch let error as URLError {
            phase = .failed(error.code == .notConnectedToInternet || error.code == .cannotFindHost
                            || error.code == .cannotConnectToHost || error.code == .timedOut
                            || error.code == .networkConnectionLost || error.code == .secureConnectionFailed
                            ? .offline : .broken(error.localizedDescription))
        } catch {
            phase = .failed(.broken(error.localizedDescription))
        }
    }

    /// Downloads the dmg and its signature into `<folder>/<version>/` and unpacks it there. Both files stay,
    /// so a later start checks the signature again instead of trusting what it finds.
    nonisolated private static func stage(_ offer: UpdateOffer, in folder: URL) async throws -> URL {
        let fm = FileManager.default
        let dir = folder.appendingPathComponent(offer.version)
        try? fm.removeItem(at: dir)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        do {
            let (downloaded, dmgResponse) = try await URLSession.shared.download(from: offer.dmg)
            guard offer.dmg.isFileURL || (dmgResponse as? HTTPURLResponse)?.statusCode == 200 else {
                throw UpdateFailure.http((dmgResponse as? HTTPURLResponse)?.statusCode ?? 0)
            }
            try fm.moveItem(at: downloaded, to: dir.appendingPathComponent(dmgName))
            let (signature, sigResponse) = try await URLSession.shared.data(from: offer.signature)
            guard offer.signature.isFileURL || (sigResponse as? HTTPURLResponse)?.statusCode == 200 else {
                throw UpdateFailure.http((sigResponse as? HTTPURLResponse)?.statusCode ?? 0)
            }
            try signature.write(to: dir.appendingPathComponent(dmgName + ".sig"))
            return try await unpack(dir, version: offer.version)
        } catch {
            try? fm.removeItem(at: dir)
            throw error
        }
    }

    nonisolated private static let dmgName = "ClaudeSync.dmg"

    /// The dmg and signature kept in `dir`, checked with the release key and unpacked afresh.
    nonisolated private static func unpack(_ dir: URL, version: String) async throws -> URL {
        let dmg = dir.appendingPathComponent(dmgName)
        let signature = String(decoding: try Data(contentsOf: dir.appendingPathComponent(dmgName + ".sig")), as: UTF8.self)
        let bundleID = Bundle.main.bundleIdentifier ?? ""
        return try await onThread {
            try UpdatePackage.unpack(dmg: dmg, signature: signature, version: version, bundleID: bundleID,
                                     publicKey: AppInfo.updatePublicKey, into: dir)
        }
    }

    /// A newer download kept from before the app last quit is checked and unpacked again; the rest goes.
    private func takeEarlierDownload() {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: Self.folder.path),
              let mine = SemVer(AppInfo.version) else { return }
        let kept = names.compactMap { name -> (SemVer, String)? in
            guard let version = SemVer(name), version > mine,
                  fm.fileExists(atPath: Self.folder.appendingPathComponent(name).appendingPathComponent(Self.dmgName + ".sig").path)
            else { return nil }
            return (version, name)
        }.max { $0.0 < $1.0 }
        for name in names where SemVer(name) != nil && name != kept?.1 {
            try? fm.removeItem(at: Self.folder.appendingPathComponent(name))
        }
        guard let (_, name) = kept else { return }
        let dir = Self.folder.appendingPathComponent(name)
        phase = .downloading(name)
        Task {
            do {
                staged = (name, try await Self.unpack(dir, version: name))
                phase = .ready(name)
                installIfIdle()
            } catch {
                try? FileManager.default.removeItem(at: dir)
                phase = .idle
            }
        }
    }

    // MARK: installing

    /// Installs the unpacked update while the person is not using the app: no engine work, no window open,
    /// the app not in front. Checked every few minutes and after each download.
    func installIfIdle() {
        guard enabled, staged != nil, !installing, isIdle(), !NSApp.isActive,
              !NSApp.windows.contains(where: { $0.isVisible && $0.styleMask.contains(.titled) }) else { return }
        install(relaunch: true)
    }

    /// The settings' button: now, whatever is open.
    func installNow() {
        guard isIdle() else { return }
        install(relaunch: true)
    }

    /// The app is quitting: a waiting update goes in, and the app stays closed.
    func installOnQuit() {
        guard enabled, staged != nil, !installing else { return }
        install(relaunch: false)
    }

    private func install(relaunch: Bool) {
        guard !installing, let staged else { return }
        let target = Bundle.main.bundleURL
        let parent = target.deletingLastPathComponent().path
        guard FileManager.default.isWritableFile(atPath: parent), FileManager.default.isWritableFile(atPath: target.path) else {
            phase = .failed(.noPermission(parent))
            return
        }
        // The finishing script runs from beside the downloads, not from inside an app being replaced.
        let script = Self.folder.appendingPathComponent("install-update.sh")
        let log = Self.folder.appendingPathComponent("update.log")
        let incoming: URL
        do {
            guard let bundled = Bundle.main.url(forResource: "install-update", withExtension: "sh") else {
                throw UpdateFailure.broken(L10n.t("앱 안에 설치 스크립트가 없어요", "The app has no install script"))
            }
            try? FileManager.default.removeItem(at: script)
            try FileManager.default.copyItem(at: bundled, to: script)
            // A whole copy beside this app first; then one exchange puts it in place (this one waits there).
            incoming = try UpdateInstall.prepare(staged.app, for: target)
            try UpdateInstall.swap(incoming, into: target)
        } catch {
            UpdateInstall.cleanUp(for: target)
            phase = .failed(error as? UpdateFailure ?? .broken(error.localizedDescription))
            return
        }
        installing = true  // the new app is in place now, whatever happens below
        do {
            if !FileManager.default.fileExists(atPath: log.path) {
                FileManager.default.createFile(atPath: log.path, contents: nil)
            }
            let logHandle = try FileHandle(forWritingTo: log)
            logHandle.seekToEndOfFile()
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [script.path, String(ProcessInfo.processInfo.processIdentifier), incoming.path,
                                 target.path, relaunch ? "1" : "0"]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = FileHandle.nullDevice
            process.standardError = logHandle
            try process.run()
        } catch {
            // Installed but not restarted: the new app opens next time, and that start clears the old copy.
            phase = .failed(.broken(error.localizedDescription))
            return
        }
        if relaunch { NSApp.terminate(nil) }
    }
}

/// Blocking work (hdiutil, ditto) on a thread of its own, off Swift's few shared threads.
private func onThread<T: Sendable>(_ work: @escaping @Sendable () throws -> T) async throws -> T {
    try await withCheckedThrowingContinuation { (done: CheckedContinuation<T, Error>) in
        let thread = Thread { done.resume(with: Result { try work() }) }
        thread.start()
    }
}
