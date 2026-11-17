import ClaudeSyncCore
import Foundation
import Testing

/// Putting an update in place: a full copy beside the app, one atomic swap, then (once the app has quit)
/// app/Resources/install-update.sh removes the old copy and starts the new one.
@Suite(.serialized) struct InstallScriptTests {
    static let script = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("Resources/install-update.sh").path

    final class Place {
        let dir: URL
        init() throws {
            dir = FileManager.default.temporaryDirectory.appendingPathComponent("install-tests-\(UUID().uuidString)")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        deinit { try? FileManager.default.removeItem(at: dir) }
        func path(_ name: String) -> String { dir.appendingPathComponent(name).path }
        func app(_ name: String, says text: String) throws -> URL {
            let contents = dir.appendingPathComponent(name).appendingPathComponent("Contents")
            try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
            try text.write(to: contents.appendingPathComponent("which"), atomically: true, encoding: .utf8)
            let info: NSDictionary = ["CFBundleShortVersionString": text]
            info.write(to: contents.appendingPathComponent("Info.plist"), atomically: true)
            return dir.appendingPathComponent(name)
        }
        func says(_ app: URL) -> String? { try? String(contentsOf: app.appendingPathComponent("Contents/which"), encoding: .utf8) }
        /// A stand-in for `open` that notes how it was called.
        func fakeOpen() throws -> String {
            let p = path("open")
            try "#!/bin/bash\nprintf '%s\\n' \"$*\" > \"\(path("opened"))\"\n".write(toFile: p, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: p)
            return p
        }
    }

    @discardableResult
    static func run(_ executable: String, _ args: [String], env: [String: String] = [:]) throws -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: executable)
        p.arguments = args
        p.environment = ProcessInfo.processInfo.environment.merging(env) { $1 }
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        p.waitUntilExit()
        return p.terminationStatus
    }

    static func sleeper(_ seconds: String) throws -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sleep")
        p.arguments = [seconds]
        try p.run()
        return p
    }

    @Test func theNewCopyIsMadeBesideTheAppWhichStaysAsItIs() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "old")
        let staged = try place.app("staged/Claude Sync.app", says: "new")
        let incoming = try UpdateInstall.prepare(staged, for: target)
        #expect(incoming.path == place.path(".Claude Sync.app.incoming"))
        #expect(place.says(incoming) == "new")
        #expect(place.says(target) == "old")
        #expect(place.says(staged) == "new")
    }

    @Test func theSwapIsOneStep() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "old")
        let incoming = try UpdateInstall.prepare(try place.app("staged/Claude Sync.app", says: "new"), for: target)
        try UpdateInstall.swap(incoming, into: target)
        #expect(place.says(target) == "new")
        #expect(place.says(incoming) == "old", "the old app waits at the incoming name until it quits")
    }

    @Test func aSwapThatCannotHappenChangesNothing() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "old")
        #expect(throws: UpdateFailure.self) { try UpdateInstall.swap(UpdateInstall.incoming(for: target), into: target) }
        #expect(place.says(target) == "old")
    }

    @Test func whatAnInstallCutShortLeftIsCleanedUp() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "old")
        _ = try place.app(".Claude Sync.app.incoming", says: "half")
        UpdateInstall.cleanUp(for: target)
        #expect(!FileManager.default.fileExists(atPath: place.path(".Claude Sync.app.incoming")))
        #expect(place.says(target) == "old")
    }

    @Test func afterTheAppQuitsTheOldCopyGoesAndTheNewOneStarts() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "new")
        let old = try place.app(".Claude Sync.app.incoming", says: "old")
        let running = try Self.sleeper("1.5")
        let started = Date()
        let code = try Self.run("/bin/bash", [Self.script, String(running.processIdentifier), old.path, target.path, "1"],
                                env: ["CLAUDE_SYNC_OPEN": try place.fakeOpen()])
        #expect(code == 0)
        #expect(Date().timeIntervalSince(started) >= 1.0, "it waited for the app to quit")
        #expect(!FileManager.default.fileExists(atPath: old.path))
        #expect(try String(contentsOfFile: place.path("opened"), encoding: .utf8) == "-g \(target.path) --args --after-update\n")
    }

    @Test func aWindowThatWasOpenComesBackAndTheAppInUseComesToTheFront() throws {
        for (mode, expected) in [("window", "-g %@ --args --after-update --show-window\n"),
                                 ("front", "%@ --args --after-update --show-window\n")] {
            let place = try Place()
            let target = try place.app("Claude Sync.app", says: "new")
            let old = try place.app(".Claude Sync.app.incoming", says: "old")
            #expect(try Self.run("/bin/bash", [Self.script, "999999", old.path, target.path, mode],
                                 env: ["CLAUDE_SYNC_OPEN": try place.fakeOpen()]) == 0)
            #expect(try String(contentsOfFile: place.path("opened"), encoding: .utf8)
                    == expected.replacingOccurrences(of: "%@", with: target.path), "\(mode)")
        }
    }

    @Test func noRelaunchWhenTheAppIsQuitting() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "new")
        let old = try place.app(".Claude Sync.app.incoming", says: "old")
        #expect(try Self.run("/bin/bash", [Self.script, "999999", old.path, target.path, "0"],
                             env: ["CLAUDE_SYNC_OPEN": try place.fakeOpen()]) == 0)
        #expect(!FileManager.default.fileExists(atPath: old.path))
        #expect(!FileManager.default.fileExists(atPath: place.path("opened")))
    }

    @Test func anAppThatDoesNotQuitKeepsItsFiles() throws {
        let place = try Place()
        let target = try place.app("Claude Sync.app", says: "new")
        let old = try place.app(".Claude Sync.app.incoming", says: "old")
        let running = try Self.sleeper("30")
        defer { running.terminate() }
        let code = try Self.run("/bin/bash", [Self.script, String(running.processIdentifier), old.path, target.path, "1"],
                                env: ["CLAUDE_SYNC_OPEN": try place.fakeOpen(), "CLAUDE_SYNC_WAIT_SECONDS": "1"])
        #expect(code != 0)
        #expect(place.says(old) == "old", "still running from there")
        #expect(!FileManager.default.fileExists(atPath: place.path("opened")))
    }
}
