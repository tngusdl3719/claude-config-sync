import ClaudeSyncCore
import CryptoKit
import Foundation
import Testing

/// A downloaded release dmg: checked, then the app inside it copied out, only when it is this app.
@Suite(.serialized) struct UpdatePackageTests {
    static let makeDmg = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("scripts/make-dmg.sh").path

    final class Release {
        let dir: URL
        let key = Curve25519.Signing.PrivateKey()
        var publicKey: String { key.publicKey.rawRepresentation.base64EncodedString() }

        init() throws {
            dir = FileManager.default.temporaryDirectory.appendingPathComponent("update-package-\(UUID().uuidString)")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        deinit { try? FileManager.default.removeItem(at: dir) }

        /// A dmg holding "Claude Sync.app" with this bundle id and version, and its signature.
        func dmg(bundleID: String = "com.example.sync", version: String = "0.7.0") throws -> (URL, String) {
            let app = dir.appendingPathComponent("src-\(UUID().uuidString)/Claude Sync.app")
            try FileManager.default.createDirectory(at: app.appendingPathComponent("Contents/MacOS"), withIntermediateDirectories: true)
            let info: NSDictionary = ["CFBundleIdentifier": bundleID, "CFBundleShortVersionString": version]
            info.write(to: app.appendingPathComponent("Contents/Info.plist"), atomically: true)
            let dmg = dir.appendingPathComponent("ClaudeSync-\(UUID().uuidString).dmg")
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [UpdatePackageTests.makeDmg, app.path, dmg.path]
            p.standardOutput = FileHandle.nullDevice
            try p.run()
            p.waitUntilExit()
            #expect(p.terminationStatus == 0)
            let signature = try key.signature(for: Data(contentsOf: dmg)).base64EncodedString()
            return (dmg, signature)
        }

        func place() -> URL { dir.appendingPathComponent("staged-\(UUID().uuidString)") }
    }

    @Test func aSignedDmgOfThisAppIsUnpacked() throws {
        let r = try Release()
        let (dmg, signature) = try r.dmg()
        let into = r.place()
        let app = try UpdatePackage.unpack(dmg: dmg, signature: signature, version: "0.7.0", bundleID: "com.example.sync",
                                           publicKey: r.publicKey, into: into)
        #expect(app.path == into.appendingPathComponent("Claude Sync.app").path)
        let info = NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
        #expect(info?["CFBundleShortVersionString"] as? String == "0.7.0")
        let mount = into.appendingPathComponent("mount")
        #expect((try? FileManager.default.contentsOfDirectory(atPath: mount.path))?.isEmpty ?? true, "the dmg is detached")
    }

    @Test func aWrongSignatureUnpacksNothing() throws {
        let r = try Release()
        let (dmg, _) = try r.dmg()
        let (_, otherSignature) = try r.dmg(version: "0.7.1")
        let into = r.place()
        #expect(throws: UpdateFailure.badSignature) {
            try UpdatePackage.unpack(dmg: dmg, signature: otherSignature, version: "0.7.0", bundleID: "com.example.sync",
                                     publicKey: r.publicKey, into: into)
        }
        #expect(!FileManager.default.fileExists(atPath: into.appendingPathComponent("Claude Sync.app").path))
    }

    @Test func anotherAppOrVersionIsRefused() throws {
        let r = try Release()
        for (bundleID, version) in [("com.example.other", "0.7.0"), ("com.example.sync", "0.6.9")] {
            let (dmg, signature) = try r.dmg(bundleID: bundleID, version: version)
            let into = r.place()
            #expect(throws: UpdateFailure.notThisApp) {
                try UpdatePackage.unpack(dmg: dmg, signature: signature, version: "0.7.0", bundleID: "com.example.sync",
                                         publicKey: r.publicKey, into: into)
            }
            #expect(!FileManager.default.fileExists(atPath: into.appendingPathComponent("Claude Sync.app").path))
        }
    }
}
