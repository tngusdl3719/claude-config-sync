import ClaudeSyncCore
import CryptoKit
import Foundation
import Testing

private func release(_ tag: String, assets: [String], draft: Bool = false, prerelease: Bool = false,
                     base: String = "https://github.com/me/app/releases/download") -> Data {
    let list = assets.map { #"{"name": "\#($0)", "browser_download_url": "\#(base)/\#(tag)/\#($0)"}"# }
    return Data(#"{"tag_name": "\#(tag)", "draft": \#(draft), "prerelease": \#(prerelease), "assets": [\#(list.joined(separator: ","))]}"#.utf8)
}

@Suite struct UpdateTests {
    @Test func versionsCompareNumberByNumber() throws {
        #expect(try #require(SemVer("0.10.0")) > #require(SemVer("0.9.1")))
        #expect(try #require(SemVer("v0.6.0")) == #require(SemVer("0.6")))
        #expect(try #require(SemVer("1.0.0")) > #require(SemVer("0.99.99")))
        for bad in ["", "x", "1..2", "-1", "1.2.3-beta", "v", "1.2.3.4.5"] {
            #expect(SemVer(bad) == nil, "\(bad)")
        }
    }

    @Test func aNewerReleaseOffersItsDmgAndSignature() throws {
        let json = release("v0.7.0", assets: ["ClaudeSync-0.7.0.dmg", "ClaudeSync-0.7.0.dmg.sig", "claude-sync-install.command"])
        let offer = try #require(UpdateCheck.offer(json, current: "0.6.0"))
        #expect(offer.version == "0.7.0")
        #expect(offer.dmg.absoluteString == "https://github.com/me/app/releases/download/v0.7.0/ClaudeSync-0.7.0.dmg")
        #expect(offer.signature.absoluteString.hasSuffix("/ClaudeSync-0.7.0.dmg.sig"))
    }

    @Test func nothingIsOfferedUnlessItIsNewerAndComplete() {
        let full = ["ClaudeSync-0.7.0.dmg", "ClaudeSync-0.7.0.dmg.sig"]
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full), current: "0.7.0") == nil)
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full), current: "0.8.0") == nil)
        #expect(UpdateCheck.offer(release("v0.7.0", assets: ["ClaudeSync-0.7.0.dmg"]), current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full, draft: true), current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full, prerelease: true), current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full, base: "http://github.com/me/app/releases/download"), current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(release("latest", assets: full), current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(Data("not json".utf8), current: "0.6.0") == nil)
    }

    @Test func localFilesOnlyForATestFeed() {
        let full = ["ClaudeSync-0.7.0.dmg", "ClaudeSync-0.7.0.dmg.sig"]
        let local = release("v0.7.0", assets: full, base: "file:///tmp/feed")
        #expect(UpdateCheck.offer(local, current: "0.6.0") == nil)
        #expect(UpdateCheck.offer(local, current: "0.6.0", allowFiles: true)?.dmg.absoluteString == "file:///tmp/feed/v0.7.0/ClaudeSync-0.7.0.dmg")
        #expect(UpdateCheck.offer(release("v0.7.0", assets: full, base: "http://x/y"), current: "0.6.0", allowFiles: true) == nil)
    }

    @Test func onlyTheReleaseKeysSignatureIsAccepted() {
        let key = Curve25519.Signing.PrivateKey()
        let publicKey = key.publicKey.rawRepresentation.base64EncodedString()
        let data = Data("the dmg".utf8)
        let signature = (try? key.signature(for: data))?.base64EncodedString() ?? ""
        #expect(UpdateSignature.isValid(data, signature: signature + "\n", publicKey: publicKey))
        #expect(!UpdateSignature.isValid(Data("the dmg, changed".utf8), signature: signature, publicKey: publicKey))
        let other = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation.base64EncodedString()
        #expect(!UpdateSignature.isValid(data, signature: signature, publicKey: other))
        #expect(!UpdateSignature.isValid(data, signature: "not base64!", publicKey: publicKey))
        #expect(!UpdateSignature.isValid(data, signature: signature, publicKey: "AAAA"))
    }

    @Test func updateStatusSpeaksBothLanguages() {
        L10n.$override.withValue(.ko) {
            #expect(Presentation.update(.upToDate) == "최신 버전이에요 (\(AppInfo.version))")
            #expect(Presentation.update(.downloading("0.7.0", nil)) == "0.7.0 받는 중…")
            #expect(Presentation.update(.downloading("0.7.0", 0.456)) == "0.7.0 받는 중… 45%")
            #expect(Presentation.update(.preparing("0.7.0")) == "0.7.0 서명을 확인하고 준비하는 중…")
            #expect(Presentation.update(.ready("0.7.0")) == "새 버전(0.7.0)을 받아 두었어요. 하던 작업이 끝나면 설치하고 다시 시작해요")
            #expect(Presentation.update(.ready("0.7.0"), automatic: false) == "새 버전(0.7.0)을 받아 두었어요. '설치하고 다시 시작'을 누르면 설치해요")
            #expect(Presentation.update(.installing("0.7.0")) == "0.7.0 설치하는 중… 끝나면 다시 시작해요")
            #expect(Presentation.update(.failed(.offline)) == "업데이트를 확인하지 못했어요: GitHub에 연결할 수 없어요")
            #expect(Presentation.update(.failed(.badSignature)).contains("서명"))
        }
        L10n.$override.withValue(.en) {
            #expect(Presentation.update(.idle) == "Not checked yet")
            #expect(Presentation.update(.checking) == "Checking for updates…")
            #expect(Presentation.update(.downloading("0.7.0", 1)) == "Downloading 0.7.0… 100%")
            #expect(Presentation.update(.preparing("0.7.0")) == "Checking the signature of 0.7.0…")
            #expect(Presentation.update(.installing("0.7.0")) == "Installing 0.7.0… the app restarts when it is done")
            #expect(Presentation.update(.ready("0.7.0")) == "0.7.0 is ready. It installs and restarts once the running work is done")
            #expect(Presentation.update(.ready("0.7.0"), automatic: false) == "0.7.0 is ready. Press Install and restart to install it")
            #expect(Presentation.update(.failed(.noPermission("/Applications"))) == "Could not update: no permission to write in /Applications")
            #expect(Presentation.update(.failed(.http(503))) == "Could not check for updates: GitHub answered 503")
            #expect(Presentation.update(.failed(.notThisApp)).contains("not this app"))
        }
    }

    @Test func aBarShowsWhileAnUpdateIsUnderWay() {
        #expect(UpdatePhase.checking.bar == .busy)
        #expect(UpdatePhase.downloading("0.7.0", nil).bar == .busy)
        #expect(UpdatePhase.downloading("0.7.0", 0.25).bar == .part(0.25))
        #expect(UpdatePhase.preparing("0.7.0").bar == .busy)
        #expect(UpdatePhase.installing("0.7.0").bar == .busy)
        for phase in [UpdatePhase.idle, .upToDate, .ready("0.7.0"), .failed(.offline)] {
            #expect(phase.bar == nil)
        }
    }

    @Test func aDownloadSaysHowFarItHasGot() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("download-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let source = dir.appendingPathComponent("release.dmg")
        let data = Data((0..<1_000_000).map { UInt8(truncatingIfNeeded: $0 &* 31) })
        try data.write(to: source)
        let seen = Seen()
        let (file, _) = try await UpdateDownload.fetch(source) { seen.add($0) }
        #expect(try Data(contentsOf: file) == data)
        let parts = seen.values.compactMap { $0 }
        #expect(parts.last == 1)
        #expect(parts == parts.sorted(), "it only goes forward")
        try? FileManager.default.removeItem(at: file)
    }

    final class Seen: @unchecked Sendable {
        private let lock = NSLock()
        private var kept: [Double?] = []
        func add(_ value: Double?) { lock.withLock { kept.append(value) } }
        var values: [Double?] { lock.withLock { kept } }
    }

    @Test func theAppsReleaseKeyIsAKey() {
        let key = Data(base64Encoded: AppInfo.updatePublicKey)
        #expect(key != nil && (try? Curve25519.Signing.PublicKey(rawRepresentation: key!)) != nil)
        #expect(UpdateCheck.latestRelease.absoluteString == "https://api.github.com/repos/\(AppInfo.repository)/releases/latest")
    }

    @Test func theEngineInsideTheAppGoesOutOnlyWhenNewer() throws {
        let f = try FakeEngines()
        func engine(_ name: String, _ version: String?) throws -> String {
            let dir = f.dir.appendingPathComponent(name).appendingPathComponent("engine/claude_sync")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let text = "\"\"\"engine\"\"\"\n" + (version.map { "__version__ = \"\($0)\"\n" } ?? "")
            try text.write(to: dir.appendingPathComponent("__init__.py"), atomically: true, encoding: .utf8)
            return f.dir.appendingPathComponent(name).path
        }
        let bundled = try engine("bundled", "0.6.0")
        #expect(EngineVersion.read(bundled) == "0.6.0")
        #expect(EngineVersion.read(try engine("old", nil)) == nil)
        #expect(EngineVersion.read(f.dir.appendingPathComponent("none").path) == nil)
        #expect(EngineVersion.newer(bundled: bundled, than: try engine("old2", nil)))
        #expect(EngineVersion.newer(bundled: bundled, than: try engine("older", "0.5.9")))
        #expect(!EngineVersion.newer(bundled: bundled, than: try engine("same", "0.6.0")))
        #expect(!EngineVersion.newer(bundled: bundled, than: try engine("newer", "0.10.0")))
        #expect(!EngineVersion.newer(bundled: try engine("nobundled", nil), than: try engine("old3", nil)))
    }
}
