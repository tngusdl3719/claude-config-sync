import Foundation

public struct EngineOutput: Sendable, Equatable {
    public var exitCode: Int32
    public var stdout: Data
    public var stderr: String
    public var timedOut: Bool
}

/// Runs the engine in a process group of its own with a hard deadline. At the deadline the
/// whole group (python, git, ssh, the https helper) is stopped, killed if it ignores that.
public struct EngineRunner: Sendable {
    public var executable: String

    public init(executable: String) {
        self.executable = executable
    }

    public func run(_ args: [String], stdin: String? = nil, timeout: TimeInterval) async -> EngineOutput {
        let executable = self.executable
        let language = L10n.current  // taken here: the thread below does not see the caller's
        // A thread of its own for the whole run, which mostly waits: Swift's shared threads are as few as the
        // cores, and runs that sat on them kept other runs from starting (and missing their deadlines).
        return await withCheckedContinuation { done in
            Self.onThread {
                done.resume(returning: Self.runBlocking(executable: executable, args: args, stdin: stdin,
                                                        timeout: timeout, language: language))
            }
        }
    }

    /// Readers, the writer and the run each get a thread: the system's shared queue stops starting new work
    /// once enough of its threads are blocked, and a reader that never started lost the engine's output.
    private static func onThread(_ work: @escaping @Sendable () -> Void) {
        let thread = Thread(block: work)
        thread.qualityOfService = .userInitiated
        thread.start()
    }

    private static func runBlocking(executable: String, args: [String], stdin: String?, timeout: TimeInterval,
                                    language: AppLanguage) -> EngineOutput {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/perl")
        process.arguments = ["-e", "setpgrp(0, 0); exec @ARGV or exit 127", "--", executable] + args
        // The engine's messages in the app's language.
        process.environment = ProcessInfo.processInfo.environment.merging(["CLAUDE_SYNC_LANG": language.rawValue]) { $1 }
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        let input = Pipe()
        process.standardInput = stdin == nil ? FileHandle.nullDevice : input
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }

        let stdoutData = DataBox(), stderrData = DataBox()
        let readers = DispatchGroup()
        readers.enter()
        onThread {
            stdoutData.set(out.fileHandleForReading.readDataToEndOfFile())
            readers.leave()
        }
        readers.enter()
        onThread {
            stderrData.set(err.fileHandleForReading.readDataToEndOfFile())
            readers.leave()
        }

        do {
            try process.run()
        } catch {
            return EngineOutput(exitCode: 127, stdout: Data(), stderr: "\(error)", timedOut: false)
        }
        // Fed from the side, so an engine that never reads stdin still meets the deadline below.
        // No SIGPIPE: a write into a killed engine fails as an error instead of ending the app.
        let writer = DispatchGroup()
        let writeFailed = WriteFlag()
        if let stdin {
            let handle = input.fileHandleForWriting
            _ = fcntl(handle.fileDescriptor, F_SETNOSIGPIPE, 1)
            writer.enter()
            onThread {
                do {
                    try handle.write(contentsOf: Data(stdin.utf8))
                } catch {
                    writeFailed.set()
                }
                try? handle.close()
                writer.leave()
            }
        }

        var timedOut = false
        if exited.wait(timeout: .now() + timeout) == .timedOut {
            timedOut = true
            let group = process.processIdentifier
            kill(-group, SIGTERM)
            if exited.wait(timeout: .now() + 1) == .timedOut {
                kill(-group, SIGKILL)
                _ = exited.wait(timeout: .now() + 2)
            } else {
                kill(-group, SIGKILL)
            }
        }
        _ = readers.wait(timeout: .now() + 2)
        var stderrText = String(decoding: stderrData.value, as: UTF8.self)
        if stdin != nil, !timedOut, writer.wait(timeout: .now() + 1) == .success, writeFailed.isSet {
            stderrText += (stderrText.isEmpty ? "" : "\n") + L10n.t("입력을 엔진에 전달하지 못했어요", "Could not pass the input to the engine")
        }
        return EngineOutput(
            exitCode: process.isRunning ? -1 : process.terminationStatus,
            stdout: stdoutData.value,
            stderr: stderrText,
            timedOut: timedOut)
    }
}

private final class DataBox: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    func set(_ new: Data) {
        lock.lock()
        data = new
        lock.unlock()
    }

    var value: Data {
        lock.lock()
        defer { lock.unlock() }
        return data
    }
}

private final class WriteFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    func set() {
        lock.lock()
        value = true
        lock.unlock()
    }

    var isSet: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

/// Where the engine lives: the shared repo's copy (the one the mod runs and syncs keep up to date) when it
/// exists, else the copy inside the app, used before the first setup. The app's copy also runs when it is
/// newer than the shared one: a receive-only PC never puts it in the shared repo itself.
public enum EngineLocator {
    public static func sharedFolder(home: String) -> String {
        home + "/.claude/shared/mods/config-sync"
    }

    public static func sharedEngine(home: String) -> String {
        sharedFolder(home: home) + "/bin/claude-sync"
    }

    public static func isConfigured(home: String) -> Bool {
        FileManager.default.isExecutableFile(atPath: sharedEngine(home: home))
    }

    public static func resolve(home: String, bundledResources: String?) -> String? {
        let shared = sharedEngine(home: home)
        let bundled = bundledResources.map { $0 + "/engine/bin/claude-sync" }
            .flatMap { FileManager.default.isExecutableFile(atPath: $0) ? $0 : nil }
        if FileManager.default.isExecutableFile(atPath: shared) {
            if let bundled, let resources = bundledResources,
               EngineVersion.newer(bundled: resources + "/engine", than: sharedFolder(home: home)) {
                return bundled
            }
            return shared
        }
        return bundled
    }
}
