import ClaudeSyncCore
import Foundation
import Testing

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try EngineJSON.decode(type, from: Data(json.utf8))
}

@Suite struct StatusDecoding {
    @Test func realStatusFromThisMac() throws {
        let s = try decode(Status.self, realStatusJSON)
        #expect(s.pc == "personal-mac")
        #expect(s.lastResult == .ok)
        #expect(s.items?.skills.count == 8)
        #expect(s.items?.mods.map(\.name).contains("config-sync") == true)
        #expect(s.items?.settings?.count == 20)
        #expect(s.items?.claudeMd?.state == .ok)
        #expect(s.receiveOnly == false)
        #expect(s.requireApproval == true)
        #expect(s.localOnly?.settings.contains("hooks") == true)
        #expect(s.lastPull != nil)
    }

    @Test func missingFieldsFallBackToEmpty() throws {
        let s = try decode(Status.self, #"{"pc": "x"}"#)
        #expect(s.lastResult == .never)
        #expect(s.incoming == 0)
        #expect(s.conflicts.isEmpty)
        #expect(s.blocked.isEmpty)
        #expect(s.items == nil)
        #expect(s.receiveOnly == false)
        #expect(s.autoEnabled == true)
    }

    @Test func backendDefaultsToGit() throws {
        #expect(try decode(Status.self, #"{"pc": "x"}"#).backend == .git)
        #expect(try decode(Status.self, #"{"backend": "notion"}"#).backend == .notion)
        #expect(try decode(Status.self, #"{"backend": "carrier-pigeon"}"#).backend == .git)
    }

    @Test func storageSaysWhereTheHistoryLives() throws {
        let git = try decode(Status.self, #"{"storage": {"backend": "git", "remote": "https://gitlab.com/me/c.git", "username": "pc-a"}}"#)
        #expect(git.storage == Storage(backend: .git, remote: "https://gitlab.com/me/c.git", username: "pc-a"))
        let notion = try decode(Status.self, #"{"storage": {"backend": "notion", "page": "0123456789abcdef0123456789abcdef", "auth": "oauth", "url": "https://www.notion.so/0123456789abcdef0123456789abcdef"}}"#)
        #expect(notion.storage == Storage(backend: .notion, page: "0123456789abcdef0123456789abcdef", auth: "oauth",
                                          url: "https://www.notion.so/0123456789abcdef0123456789abcdef"))
        #expect(try decode(Status.self, #"{"pc": "x"}"#).storage == nil)
    }

    @Test func descriptionsForTooltips() throws {
        let s = try decode(Status.self, #"{"descriptions": {"shared": {"skills": {"brand": "공용 것"}, "mods": {"usage-band": "사용량 띠"}}, "local": {"skills": {"brand": "이 PC 것"}}}}"#)
        #expect(s.descriptions.of("skills", "brand", local: false) == "공용 것")
        #expect(s.descriptions.of("skills", "brand", local: true) == "이 PC 것")
        #expect(s.descriptions.of("mods", "usage-band", local: false) == "사용량 띠")
        #expect(s.descriptions.of("mods", "usage-band", local: true) == nil)
        #expect(try decode(Status.self, #"{"pc": "x"}"#).descriptions.of("skills", "brand", local: false) == nil)
    }

    @Test func unknownResultIsAnError() throws {
        let s = try decode(Status.self, #"{"last_result": "something-new"}"#)
        #expect(s.lastResult == .error)
    }

    @Test func receiveOnlyAndProblems() throws {
        let json = #"""
        {"last_result": "conflict", "receive_only": true,
         "conflicts": [{"id": "setting:theme", "kind": "setting", "path": ["theme"], "local": "dark", "remote": 3},
                       {"id": "file:CLAUDE.md", "kind": "file", "file": "CLAUDE.md"}],
         "blocked": [{"file": "CLAUDE.md", "line": 2, "text": "corp-host", "word": "corp"}],
         "pending_approval": [{"remote": "abc", "files": ["mods/x/run"], "settings": [["permissions", "defaultMode"]],
                               "commits": [{"sha": "abc", "pc": "company-mac", "time": 1, "subject": "s"}]}],
         "collisions": [{"kind": "skills", "name": "brand", "reason": "exists"}],
         "items": {"skills": [{"name": "a", "state": "weird"}]}}
        """#
        let s = try decode(Status.self, json)
        #expect(s.receiveOnly == true)
        #expect(s.conflicts.count == 2)
        #expect(s.conflicts[0].local?.display == "\"dark\"")
        #expect(s.conflicts[0].remote?.display == "3")
        #expect(s.conflicts[1].file == "CLAUDE.md")
        #expect(s.blocked.first?.word == "corp")
        #expect(s.pendingApproval.first?.commits.first?.pc == "company-mac")
        #expect(s.pendingApproval.first?.settings == [["permissions", "defaultMode"]])
        #expect(s.collisions.first?.name == "brand")
        #expect(s.items?.skills.first?.state == .ok)
    }
}

@Suite struct OtherDecoding {
    @Test func logEntries() throws {
        let log = try decode([LogEntry].self, realLogJSON)
        #expect(log.count == 2)
        #expect(log[0].pc == "personal-mac")
        #expect(log[0].sha.count == 40)
        #expect(!log[0].files.isEmpty)
    }

    @Test func joinResult() throws {
        let json = #"""
        {"result": "ok", "error": null, "backup": "/b", "overwritten": [{"path": ["theme"], "local": "light", "shared": "dark"}],
         "collisions": [], "missing_plugins": ["swift-lsp@claude-plugins-official"], "managed_settings_keys": ["env"]}
        """#
        let r = try decode(JoinResult.self, json)
        #expect(r.result == "ok")
        #expect(r.overwritten.first?.path == ["theme"])
        #expect(r.overwritten.first?.local?.display == "\"light\"")
        #expect(r.missingPlugins == ["swift-lsp@claude-plugins-official"])
        #expect(r.managedSettingsKeys == ["env"])
    }

    @Test func engineErrorReply() throws {
        let e = try decode(EngineErrorReply.self, #"{"error": "금지어", "hits": [{"file": "a", "line": 0, "text": "a", "word": "w"}]}"#)
        #expect(e.error == "금지어")
        #expect(e.hits.count == 1)
    }

    @Test func candidates() throws {
        let json = #"""
        {"skills": ["brand", "notes"], "mods": ["usage-band"],
         "keys": [{"key": "theme", "value_preview": "\"dark\""}], "claude_md": {"exists": true, "lines": 48}}
        """#
        let c = try decode(Candidates.self, json)
        #expect(c.skills == ["brand", "notes"])
        #expect(c.keys.first?.valuePreview == "\"dark\"")
        #expect(c.claudeMd?.lines == 48)
    }
}
