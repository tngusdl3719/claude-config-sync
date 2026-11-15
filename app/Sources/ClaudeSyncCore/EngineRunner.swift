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
        let language = L10n.current  // taken here: the detached task below does not see the caller's
        return await Task.detached(priority: .userInitiated) {
            Self.runBlocking(executable: executable, args: args, stdin: stdin, timeout: timeout, language: language)
        }.value
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

        var stdoutData = Data(), stderrData = Data()
        let readers = DispatchGroup()
        readers.enter()
        DispatchQueue.global().async {
            stdoutData = out.fileHandleForReading.readDataToEndOfFile()
            readers.leave()
        }
        readers.enter()
        DispatchQueue.global().async {
            stderrData = err.fileHandleForReading.readDataToEndOfFile()
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
            DispatchQueue.global().async {
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
        var stderrText = String(decoding: stderrData, as: UTF8.self)
        if stdin != nil, !timedOut, writer.wait(timeout: .now() + 1) == .success, writeFailed.isSet {
            stderrText += (stderrText.isEmpty ? "" : "\n") + L10n.t("입력을 엔진에 전달하지 못했어요", "Could not pass the input to the engine")
        }
        return EngineOutput(
            exitCode: process.isRunning ? -1 : process.terminationStatus,
            stdout: stdoutData,
            stderr: stderrText,
            timedOut: timedOut)
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

/// Where the engine lives: the shared repo's copy (the one the mod runs and syncs keep up to
/// date) when it exists, else the copy inside the app, used before the first setup.
public enum EngineLocator {
    public static func sharedEngine(home: String) -> String {
        home + "/.claude/shared/mods/config-sync/bin/claude-sync"
    }

    public static func isConfigured(home: String) -> Bool {
        FileManager.default.isExecutableFile(atPath: sharedEngine(home: home))
    }

    public static func resolve(home: String, bundledResources: String?) -> String? {
        let shared = sharedEngine(home: home)
        if FileManager.default.isExecutableFile(atPath: shared) { return shared }
        if let bundledResources {
            let bundled = bundledResources + "/engine/bin/claude-sync"
            if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
        }
        return nil
    }
}
