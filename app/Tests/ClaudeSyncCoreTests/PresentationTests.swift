import ClaudeSyncCore
import Foundation
import Testing

private func status(_ json: String) throws -> Status {
    try EngineJSON.decode(Status.self, from: Data(json.utf8))
}

@Suite struct PresentationTests {
    @Test func relativeTime() {
        #expect(Presentation.ago(10) == "방금")
        #expect(Presentation.ago(180) == "3분 전")
        #expect(Presentation.ago(7200) == "2시간 전")
        #expect(Presentation.ago(200_000) == "2일 전")
    }

    @Test func okShowsLastSyncQuietly() throws {
        let s = try status(#"{"last_result": "ok", "now": 1000, "last_pull": 820}"#)
        let m = Presentation.menuBar(s, syncing: false, failure: nil)
        #expect(m.tint == .normal)
        #expect(m.headline == "동기화 3분 전 ✓")
        #expect(m.symbol == "arrow.triangle.2.circlepath")
        #expect(m.modeLabel == nil)
    }

    @Test(arguments: [
        ("blocked", Tint.alert, "금지어 때문에 올리기를 멈췄어요"),
        ("offline", Tint.alert, "서버에 연결하지 못했어요"),
        ("error", Tint.alert, "동기화 오류"),
        ("needs_approval", Tint.attention, "확인 필요"),
        ("never", Tint.normal, "아직 동기화 전"),
    ])
    func problemStates(raw: String, tint: Tint, headline: String) throws {
        let s = try status(#"{"last_result": "\#(raw)", "now": 1000, "last_pull": 990}"#)
        let m = Presentation.menuBar(s, syncing: false, failure: nil)
        #expect(m.tint == tint)
        #expect(m.headline.hasPrefix(headline))
        if tint == .alert { #expect(m.symbol == "exclamationmark.triangle.fill") }
    }

    @Test func conflictCountsAndIncoming() throws {
        let c = try status(#"{"last_result": "conflict", "conflicts": [{"id": "a"}, {"id": "b"}]}"#)
        #expect(Presentation.menuBar(c, syncing: false, failure: nil).headline == "충돌 2건")
        let i = try status(#"{"last_result": "ok", "incoming": 3, "now": 10, "last_pull": 5}"#)
        let m = Presentation.menuBar(i, syncing: false, failure: nil)
        #expect(m.tint == .attention)
        #expect(m.headline == "받을 것 3개")
    }

    @Test func syncingAndEngineFailure() throws {
        let s = try status(#"{"last_result": "ok"}"#)
        #expect(Presentation.menuBar(s, syncing: true, failure: nil).headline == "동기화 중…")
        let m = Presentation.menuBar(nil, syncing: false, failure: .timedOut)
        #expect(m.tint == .alert)
        #expect(m.headline == "동기화 엔진 오류")
        #expect(Presentation.menuBar(nil, syncing: false, failure: nil).headline == "상태를 읽는 중…")
    }

    @Test func latestEngineFailureWinsOverAnOlderGoodStatus() throws {
        let s = try status(#"{"last_result": "ok", "receive_only": true, "now": 10, "last_pull": 5}"#)
        let m = Presentation.menuBar(s, syncing: false, failure: .badOutput("Traceback: boom"))
        #expect(m.tint == .alert)
        #expect(m.headline == "동기화 엔진 오류")
        #expect(m.modeLabel == "받기 전용")
    }

    @Test func receiveOnlyShowsModeAndHidesSendingActions() throws {
        let s = try status(#"{"last_result": "ok", "receive_only": true, "now": 10, "last_pull": 5}"#)
        let m = Presentation.menuBar(s, syncing: false, failure: nil)
        #expect(m.modeLabel == "받기 전용")
        #expect(Presentation.canSend(s) == false)
        let normal = try status(#"{"last_result": "ok"}"#)
        #expect(Presentation.canSend(normal))
    }

    @Test func storageText() {
        #expect(Presentation.storage(Storage(backend: .git, remote: "https://gitlab.com/me/c.git", username: "pc-a"))
                == StorageText(title: "GitLab", detail: "gitlab.com/me/c.git · 토큰 이름 pc-a"))
        #expect(Presentation.storage(Storage(backend: .git, remote: "https://github.com/me/c.git", username: "pc-a"))
                == StorageText(title: "GitHub", detail: "github.com/me/c.git · 토큰 이름 pc-a"))
        #expect(Presentation.storage(Storage(backend: .git, remote: "git@github.com:me/c.git")).title == "GitHub")
        #expect(Presentation.storage(Storage(backend: .git, remote: "https://git.example.com/c.git"))
                == StorageText(title: "git 저장소", detail: "git.example.com/c.git"))
        #expect(Presentation.storage(Storage(backend: .notion, page: "0123456789abcdef0123456789abcdef", auth: "oauth"))
                == StorageText(title: "Notion", detail: "Notion 로그인으로 연결 · 페이지 01234567…"))
        #expect(Presentation.storage(Storage(backend: .notion, page: "0123456789abcdef0123456789abcdef", auth: "secret"))
                == StorageText(title: "Notion", detail: "비밀 키로 연결 · 페이지 01234567…"))
        #expect(Presentation.storage(nil) == StorageText(title: "저장소 정보 없음", detail: ""))
    }

    @Test func settingKeysAreExplained() {
        #expect(Presentation.settingHelp("theme") == "화면 테마")
        #expect(Presentation.settingHelp("enabledPlugins.superpowers@superpowers-marketplace")
                == "켜 둔 플러그인: superpowers (마켓 superpowers-marketplace)")
        #expect(Presentation.settingHelp("skillOverrides.brand") == "스킬 brand 켜기·끄기")
        #expect(Presentation.settingHelp("modelSettings.claude-opus-5-5") == "모델 claude-opus-5-5의 세부 설정")
        #expect(Presentation.settingHelp("env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") == "대화를 자동으로 줄이기(압축) 시작하는 비율")
        #expect(Presentation.settingHelp("env.MY_VAR") == "Claude Code에 넘기는 환경 변수 MY_VAR")
        #expect(Presentation.settingHelp("somethingNew") == "Claude Code 설정 somethingNew")
        for kind in ["claude_md", "settings", "skills", "mods", "local"] {
            #expect(!Presentation.groupHelp(kind).isEmpty)
        }
        #expect(Presentation.groupHelp("other") == "")
    }

    @Test func labels() {
        #expect(Presentation.label(ItemState.ok) == "✓ 최신")
        #expect(Presentation.label(ItemState.local) == "↑ 이 PC에서 바뀜")
        #expect(Presentation.label(ItemState.incoming) == "↓ 받을 것 있음")
        #expect(Presentation.label(ItemState.conflict) == "⚠ 충돌")
        #expect(Presentation.label(LastResult.ok) == "● 정상")
    }

    @Test func clockText() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
        let now = 1_792_480_000.0  // 2026-10-20 16:06:40 KST
        #expect(Presentation.clock(now - 3600, now: now, calendar: cal) == "오늘 15:06")
        #expect(Presentation.clock(now - 86_400, now: now, calendar: cal) == "10/19 16:06")
    }
}

@Suite struct SchedulerTests {
    let t0 = Date(timeIntervalSince1970: 1_000_000)

    @Test func neverTwoAtOnce() {
        var s = SyncScheduler(intervalMinutes: 15)
        let first = s.begin(now: t0)
        let second = s.begin(now: t0.addingTimeInterval(1))
        s.end()
        let third = s.begin(now: t0.addingTimeInterval(2))
        #expect(first)
        #expect(!second)
        #expect(third)
    }

    @Test func dueByIntervalAndAutoSwitch() {
        var s = SyncScheduler(intervalMinutes: 15)
        #expect(s.isDue(now: t0, autoEnabled: true))
        #expect(!s.isDue(now: t0, autoEnabled: false))
        _ = s.begin(now: t0)
        s.end()
        #expect(!s.isDue(now: t0.addingTimeInterval(14 * 60), autoEnabled: true))
        #expect(s.isDue(now: t0.addingTimeInterval(15 * 60), autoEnabled: true))
    }

    @Test func changingTheIntervalKeepsARunningSyncRunning() {
        var s = SyncScheduler(intervalMinutes: 15)
        let first = s.begin(now: t0)
        s.setInterval(5)
        let second = s.begin(now: t0.addingTimeInterval(1))
        #expect(first)
        #expect(s.intervalMinutes == 5)
        #expect(!second, "a second sync must not start")
        s.end()
        #expect(!s.isDue(now: t0.addingTimeInterval(4 * 60), autoEnabled: true))
        #expect(s.isDue(now: t0.addingTimeInterval(5 * 60), autoEnabled: true))
        s.setInterval(7)
        #expect(s.intervalMinutes == 15)
    }

    @Test func oneEngineChangeAtATime() {
        var g = WorkGate()
        let sync = g.begin(WorkGate.sync)
        let syncing = g.syncing
        let actionDuringSync = g.begin("되돌리는 중")
        g.end()
        let setup = g.begin("설정하는 중")
        let syncDuringSetup = g.begin(WorkGate.sync)
        #expect(sync && syncing)
        #expect(!actionDuringSync, "an action waits for the sync")
        #expect(setup)
        #expect(!syncDuringSetup, "a timer sync waits for setup")
        #expect(g.current == "설정하는 중")
        #expect(!g.syncing)
        g.end()
        #expect(g.current == nil)
    }

    @Test func onlyKnownIntervals() {
        #expect(SyncScheduler.intervals == [5, 15, 30, 60])
        #expect(SyncScheduler(intervalMinutes: 7).intervalMinutes == 15)
        #expect(SyncScheduler(intervalMinutes: 60).intervalMinutes == 60)
    }
}
