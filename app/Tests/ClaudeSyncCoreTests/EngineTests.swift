import ClaudeSyncCore
import Foundation
import Testing

/// A temp directory with fake engines written as shell scripts.
final class FakeEngines {
    let dir: URL

    init() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("claude-sync-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: dir) }

    func script(_ name: String, _ body: String) throws -> String {
        let url = dir.appendingPathComponent(name)
        try ("#!/bin/bash\n" + body + "\n").write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url.path
    }

    func read(_ name: String) -> String {
        (try? String(contentsOf: dir.appendingPathComponent(name), encoding: .utf8)) ?? ""
    }
}

private func leftovers(_ marker: String) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
    p.arguments = ["-f", "sleep \(marker)"]
    let out = Pipe()
    p.standardOutput = out
    try? p.run()
    p.waitUntilExit()
    return String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

@Suite struct EngineClientTests {
    @Test func successDecodesStatus() async throws {
        let f = try FakeEngines()
        let engine = try f.script("ok", #"echo '{"pc": "pc-a", "last_result": "ok", "receive_only": true}'"#)
        let result = await EngineClient(runner: EngineRunner(executable: engine)).status()
        let status = try result.get()
        #expect(status.pc == "pc-a")
        #expect(status.receiveOnly)
    }

    @Test func manyRunsAtOnceAllKeepTheirOutput() async throws {
        let f = try FakeEngines()
        let engine = try f.script("slow", #"sleep 0.3; echo "{\"pc\": \"$1\"}"; exit 1"#)
        let runner = EngineRunner(executable: engine)
        let outputs = await withTaskGroup(of: (Int, String).self) { group in
            for i in 0..<100 {
                group.addTask { (i, String(decoding: await runner.run([String(i)], timeout: 20).stdout, as: UTF8.self)) }
            }
            var all: [Int: String] = [:]
            for await (i, text) in group { all[i] = text }
            return all
        }
        let lost = (0..<100).filter { outputs[$0] != "{\"pc\": \"\($0)\"}\n" }
        #expect(lost.isEmpty, "runs that lost their output: \(lost)")
    }

    @Test func theAppsEngineIsHandedToTheSharedRepo() async throws {
        let f = try FakeEngines()
        let engine = try f.script("adopt", #"printf '%s\n' "$@" > "$(dirname "$0")/argv"; echo '{"adopted": true, "from": null, "to": "0.6.0"}'"#)
        let result = await EngineClient(runner: EngineRunner(executable: engine)).adoptEngine(from: "/Apps/Claude Sync.app/Contents/Resources/engine")
        #expect(try result.get() == AdoptResult(adopted: true, from: nil, to: "0.6.0", reason: nil))
        #expect(f.read("argv") == "adopt-engine\n/Apps/Claude Sync.app/Contents/Resources/engine\n")
        let refused = try f.script("ro", #"echo '{"adopted": false, "from": "0.5.0", "to": "0.6.0", "reason": "receive_only"}'"#)
        #expect(try await EngineClient(runner: EngineRunner(executable: refused)).adoptEngine(from: "/x").get().reason == "receive_only")
        #expect(EngineLocator.sharedFolder(home: "/Users/me") == "/Users/me/.claude/shared/mods/config-sync")
    }

    @Test func theEngineSpeaksTheAppsLanguage() async throws {
        let f = try FakeEngines()
        let engine = try f.script("lang", #"printf '%s' "${CLAUDE_SYNC_LANG-unset}" > "$(dirname "$0")/lang.out"; echo '{}'"#)
        let runner = EngineRunner(executable: engine)
        for language in AppLanguage.allCases {
            _ = await L10n.$override.withValue(language) { await runner.run(["status"], timeout: 10) }
            #expect(f.read("lang.out") == language.rawValue)
        }
    }

    @Test func errorReplyCarriesMessageAndHits() async throws {
        let f = try FakeEngines()
        let engine = try f.script("err", #"echo '{"error": "금지어가 있어요", "hits": [{"file": "a", "line": 1, "text": "x", "word": "corp"}]}'; exit 1"#)
        let result = await EngineClient(runner: EngineRunner(executable: engine)).share(kind: "skills", name: "notes")
        #expect(result == .failure(.failed("금지어가 있어요", hits: [try hit("a", 1, "x", "corp")])))
    }

    @Test func nonJSONFailureShowsStderrTail() async throws {
        let f = try FakeEngines()
        let engine = try f.script("crash", "echo 'Traceback (most recent call last):' >&2; echo 'ValueError: boom' >&2; exit 1")
        let result = await EngineClient(runner: EngineRunner(executable: engine)).status()
        guard case .failure(.badOutput(let text)) = result else { Issue.record("expected badOutput, got \(result)"); return }
        #expect(text.contains("boom"))
    }

    @Test func emptyOutputIsBadOutput() async throws {
        let f = try FakeEngines()
        let engine = try f.script("empty", "exit 0")
        let result = await EngineClient(runner: EngineRunner(executable: engine)).status()
        guard case .failure(.badOutput) = result else { Issue.record("expected badOutput, got \(result)"); return }
    }

    @Test func missingEngineIsNotFound() async throws {
        let result = await EngineClient(runner: EngineRunner(executable: "/nonexistent/claude-sync")).status()
        guard case .failure(.notFound) = result else { Issue.record("expected notFound, got \(result)"); return }
    }

    @Test func stdinThatIsNeverReadStillEndsAtTheDeadline() async throws {
        let f = try FakeEngines()
        let marker = "43.\(ProcessInfo.processInfo.processIdentifier)"
        let engine = try f.script("deaf", "sleep \(marker)")
        let started = Date()
        let huge = String(repeating: "x", count: 2_000_000)
        let out = await EngineRunner(executable: engine).run(["join"], stdin: huge, timeout: 1)
        #expect(out.timedOut)
        #expect(Date().timeIntervalSince(started) < 6)
        try await Task.sleep(nanoseconds: 300_000_000)
        #expect(leftovers(marker) == "")
    }

    @Test func initPassesTheBlocklistWords() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        cat > /dev/null
        echo '{"result": "ok"}'
        """)
        var choice = InitChoice(remote: "https://gitlab.com/me/c.git", username: "pc-a", pc: "pc-a", skills: [], mods: [],
                                keys: ["theme"], moveClaudeMd: true, requireApproval: false)
        choice.words = ["corp", "internal-host"]
        _ = await EngineClient(runner: EngineRunner(executable: engine)).initFirstPC(choice, token: "t")
        let argv = f.read("argv").components(separatedBy: "\n")
        guard let at = argv.firstIndex(of: "--words") else { Issue.record("no --words in \(argv)"); return }
        #expect(argv[at + 1] == "corp,internal-host")
    }

    @Test func hangIsKilledAtTheDeadlineWithItsChildren() async throws {
        let f = try FakeEngines()
        let marker = "41.\(ProcessInfo.processInfo.processIdentifier)"
        let engine = try f.script("hang", "trap '' TERM\nsleep \(marker) &\nsleep \(marker)\nwait")
        let started = Date()
        let out = await EngineRunner(executable: engine).run(["sync"], timeout: 1)
        #expect(out.timedOut)
        #expect(Date().timeIntervalSince(started) < 5)
        try await Task.sleep(nanoseconds: 300_000_000)
        #expect(leftovers(marker) == "")
    }

    @Test func tokenTravelsOnStdinOnly() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        env > "\(dir)/env"
        cat > "\(dir)/stdin"
        echo '{"result": "ok"}'
        """)
        let token = "glpat-SECRET-\(UUID().uuidString)"
        let result = await EngineClient(runner: EngineRunner(executable: engine)).join(
            remote: "https://gitlab.com/me/claude-config.git", username: "pc-b", pc: "pc-b", words: ["corp", "x"], token: token)
        #expect(try result.get().result == "ok")
        #expect(!f.read("argv").contains(token))
        #expect(!f.read("env").contains(token))
        #expect(f.read("stdin") == token + "\n")
        #expect(f.read("argv").split(separator: "\n").map(String.init) ==
            ["join", "--remote", "https://gitlab.com/me/claude-config.git", "--username", "pc-b", "--pc", "pc-b", "--words", "corp,x"])
    }

    @Test func notionJoinSendsSecretOnStdinOnly() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        env > "\(dir)/env"
        cat > "\(dir)/stdin"
        echo '{"result": "ok"}'
        """)
        let secret = "ntn_SECRET_\(UUID().uuidString)"
        let result = await EngineClient(runner: EngineRunner(executable: engine)).joinNotion(
            page: "0123456789abcdef0123456789abcdef", pc: "pc-b", words: ["corp"], receiveOnly: true, secret: " \(secret)\n")
        #expect(try result.get().result == "ok")
        #expect(!f.read("argv").contains(secret))
        #expect(!f.read("env").contains(secret))
        #expect(f.read("stdin") == secret + "\n")
        #expect(f.read("argv").split(separator: "\n").map(String.init) ==
            ["join", "--backend", "notion", "--page", "0123456789abcdef0123456789abcdef", "--pc", "pc-b",
             "--words", "corp", "--receive-only"])
    }

    @Test func gitJoinCanBeReceiveOnly() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        cat > /dev/null
        echo '{"result": "ok"}'
        """)
        _ = await EngineClient(runner: EngineRunner(executable: engine)).join(
            remote: "https://gitlab.com/me/c.git", username: "pc-b", pc: "pc-b", words: [], receiveOnly: true, token: "t")
        #expect(f.read("argv").split(separator: "\n").map(String.init).last == "--receive-only")
    }

    @Test func notionInitPassesChoices() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        cat > "\(dir)/stdin"
        echo '{"result": "ok"}'
        """)
        let choice = InitChoice(remote: "", username: "pc-a", pc: "pc-a", skills: ["brand"], mods: [], keys: ["theme"],
                                moveClaudeMd: false, requireApproval: false, backend: .notion,
                                notionPage: "0123456789abcdef0123456789abcdef")
        _ = await EngineClient(runner: EngineRunner(executable: engine)).initFirstPC(choice, token: "ntn_x")
        #expect(f.read("stdin") == "ntn_x\n")
        #expect(f.read("argv").components(separatedBy: "\n").dropLast() ==
            ["init", "--backend", "notion", "--page", "0123456789abcdef0123456789abcdef", "--pc", "pc-a",
             "--skills", "brand", "--mods", "", "--keys", "theme", "--claude-md", "keep"])
    }

    @Test func notionLoginSendsTheConnectionOnStdinOnly() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        env > "\(dir)/env"
        cat > "\(dir)/stdin"
        echo '{"pages": [{"id": "0123456789abcdef0123456789abcdef", "title": "Claude Sync"}], "workspace": "내 공간"}'
        """)
        let secret = "secret_OAUTH_\(UUID().uuidString)"
        let client = NotionOAuthClient(clientID: "client-1", clientSecret: secret)
        let result = await EngineClient(runner: EngineRunner(executable: engine)).notionLogin(client)
        let login = try result.get()
        #expect(login.pages == [NotionPage(id: "0123456789abcdef0123456789abcdef", title: "Claude Sync")])
        #expect(login.workspace == "내 공간")
        #expect(f.read("argv").split(separator: "\n").map(String.init) == ["notion-login"])
        #expect(!f.read("argv").contains(secret))
        #expect(!f.read("env").contains(secret))
        let sent = try JSONSerialization.jsonObject(with: Data(f.read("stdin").utf8)) as? [String: String]
        #expect(sent == ["client_id": "client-1", "client_secret": secret])
    }

    @Test func aBrokeredSignInSendsNoSecret() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("login", """
        cat > "\(dir)/stdin"
        echo '{"pages": [], "workspace": "w"}'
        """)
        let client = NotionOAuthClient(clientID: "client-1", broker: "https://broker.example")
        _ = await EngineClient(runner: EngineRunner(executable: engine)).notionLogin(client)
        let sent = try JSONSerialization.jsonObject(with: Data(f.read("stdin").utf8)) as? [String: String]
        #expect(sent == ["client_id": "client-1", "broker": "https://broker.example"])
    }

    @Test func everyMacCanSignInWithNotion() throws {
        let own = NotionOAuthClient(clientID: "mine", clientSecret: "secret_x")
        #expect(NotionOAuthClient.choose(keychain: own) == own, "a Mac with its own connection keeps it")
        let builtIn = try #require(NotionOAuthClient.choose(keychain: nil), "the others sign in through the broker")
        #expect(builtIn == NotionOAuthClient(clientID: AppInfo.notionClientID, broker: AppInfo.notionBroker))
        #expect(builtIn.clientSecret.isEmpty)
        let url = try #require(URL(string: AppInfo.notionBroker))
        #expect(url.scheme == "https" && url.host != nil)
    }

    @Test func signedInNotionJoinAndInitUseTheLoginNotASecret() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" >> "\(dir)/argv"
        cat >> "\(dir)/stdin"
        echo '{"result": "ok"}'
        """)
        let client = EngineClient(runner: EngineRunner(executable: engine))
        _ = await client.joinNotion(page: "p1", pc: "pc-b", words: [], receiveOnly: false, secret: nil)
        var choice = InitChoice(remote: "", username: "pc-a", pc: "pc-a", skills: [], mods: [], keys: ["theme"],
                                moveClaudeMd: true, requireApproval: false, backend: .notion, notionPage: "p1")
        choice.notionSignedIn = true
        _ = await client.initFirstPC(choice, token: "")
        let argv = f.read("argv").components(separatedBy: "\n")
        #expect(argv.filter { $0 == "--oauth" }.count == 2)
        #expect(f.read("stdin") == "")
    }

    @Test func storageChangesSendSecretsOnStdinOnly() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" >> "\(dir)/argv"
        echo "--" >> "\(dir)/argv"
        cat >> "\(dir)/stdin"
        echo '{"ok": true, "backend": "git"}'
        """)
        let client = EngineClient(runner: EngineRunner(executable: engine))
        let token = "glpat-\(UUID().uuidString)"
        let moved = await client.setGitStorage(remote: "https://gitlab.com/me/c.git", username: "pc-a", token: " \(token)\n")
        #expect((try? moved.get()) != nil)
        _ = await client.setNotionStorage(page: "p1", secret: "ntn_x")
        _ = await client.setNotionStorage(page: "p1", secret: nil)
        _ = await client.setReceiveOnly(true)
        _ = await client.setReceiveOnly(false)
        let calls = f.read("argv").components(separatedBy: "--\n").map { $0.components(separatedBy: "\n").dropLast() }
        #expect(calls.prefix(5).map(Array.init) == [
            ["set-remote", "https://gitlab.com/me/c.git", "--username", "pc-a"],
            ["set-remote", "--backend", "notion", "--page", "p1"],
            ["set-remote", "--backend", "notion", "--page", "p1", "--oauth"],
            ["mode", "receive-only"],
            ["mode", "normal"],
        ])
        #expect(!f.read("argv").contains(token))
        #expect(f.read("stdin") == token + "\n" + "ntn_x\n")
    }

    @Test func connectionInfoComesFromThisMacsKeychain() throws {
        let f = try FakeEngines()
        // Like /usr/bin/security: the item's attributes, or with -w only its secret; 44 when there is no such item.
        let security = try f.script("security", #"""
            [ "$2" = "-s" ] && [ "$3" = "claude-sync-notion-oauth" ] || exit 44
            if [ "${4:-}" = "-w" ]; then echo "secret_x"; else
              printf 'keychain: "/x/login.keychain-db"\nattributes:\n    "acct"<blob>="abc"\n    "svce"<blob>="claude-sync-notion-oauth"\n'
            fi
            """#)
        #expect(NotionOAuthClient.fromKeychain(security: security) == NotionOAuthClient(clientID: "abc", clientSecret: "secret_x"))
        #expect(NotionOAuthClient.fromKeychain(security: security, item: "none") == nil)
        let empty = try f.script("empty-secret", #"[ "${4:-}" = "-w" ] && echo "" || echo '    "acct"<blob>="abc"'"#)
        #expect(NotionOAuthClient.fromKeychain(security: empty) == nil)
        #expect(NotionOAuthClient.fromKeychain(security: f.dir.appendingPathComponent("missing").path) == nil)
    }

    @Test func initPassesTokenOnStdinAndChoices() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" > "\(dir)/argv"
        cat > "\(dir)/stdin"
        echo '{"result": "ok"}'
        """)
        let choice = InitChoice(remote: "https://gitlab.com/me/c.git", username: "pc-a", pc: "pc-a",
                                skills: ["brand"], mods: ["usage-band"], keys: ["theme", "effortLevel"],
                                moveClaudeMd: true, requireApproval: true)
        let result = await EngineClient(runner: EngineRunner(executable: engine)).initFirstPC(choice, token: "tok\n")
        #expect(try result.get().result == "ok")
        #expect(f.read("stdin") == "tok\n")
        #expect(f.read("argv").split(separator: "\n").map(String.init) ==
            ["init", "--remote", "https://gitlab.com/me/c.git", "--username", "pc-a", "--pc", "pc-a",
             "--skills", "brand", "--mods", "usage-band", "--keys", "theme,effortLevel", "--claude-md", "move",
             "--require-approval", "--token-stdin"])
    }
}

@Suite struct EngineLocatorTests {
    @Test func prefersSharedThenBundledThenNothing() throws {
        let f = try FakeEngines()
        let home = f.dir.appendingPathComponent("home").path
        let bundled = f.dir.appendingPathComponent("Resources").path
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == nil)
        let bundledEngine = bundled + "/engine/bin/claude-sync"
        try FileManager.default.createDirectory(atPath: bundled + "/engine/bin", withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: bundledEngine, contents: Data("#!/bin/sh\n".utf8), attributes: [.posixPermissions: 0o755])
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == bundledEngine)
        let sharedEngine = home + "/.claude/shared/mods/config-sync/bin/claude-sync"
        try FileManager.default.createDirectory(atPath: home + "/.claude/shared/mods/config-sync/bin", withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: sharedEngine, contents: Data("#!/bin/sh\n".utf8), attributes: [.posixPermissions: 0o755])
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == sharedEngine)
        #expect(EngineLocator.isConfigured(home: home))

        // A newer engine inside the app runs instead of an older shared one (a receive-only PC never updates
        // the shared engine itself, and an old one cannot sign in through the broker).
        func stamp(_ root: String, _ version: String?) throws {
            try FileManager.default.createDirectory(atPath: root + "/engine/claude_sync", withIntermediateDirectories: true)
            let text = "\"\"\"engine\"\"\"\n" + (version.map { "__version__ = \"\($0)\"\n" } ?? "")
            try text.write(toFile: root + "/engine/claude_sync/__init__.py", atomically: true, encoding: .utf8)
        }
        let sharedRoot = home + "/.claude/shared/mods/config-sync"
        try stamp(bundled + "/engine", "0.6.1")
        try stamp(sharedRoot, nil)
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == bundledEngine)
        try stamp(sharedRoot, "0.6.0")
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == bundledEngine)
        try stamp(sharedRoot, "0.6.1")
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == sharedEngine)
        try stamp(sharedRoot, "0.7.0")
        #expect(EngineLocator.resolve(home: home, bundledResources: bundled) == sharedEngine)
    }
}

private func hit(_ file: String, _ line: Int, _ text: String, _ word: String) throws -> Hit {
    try EngineJSON.decode(Hit.self, from: Data(#"{"file": "\#(file)", "line": \#(line), "text": "\#(text)", "word": "\#(word)"}"#.utf8))
}
