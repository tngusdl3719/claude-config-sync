import ClaudeSyncCore
import Foundation
import Testing

@Suite struct LocalizationTests {
    @Test func picksTheTextOfTheChosenLanguage() {
        L10n.$override.withValue(.ko) { #expect(L10n.t("지금 동기화", "Sync now") == "지금 동기화") }
        L10n.$override.withValue(.en) { #expect(L10n.t("지금 동기화", "Sync now") == "Sync now") }
    }

    @Test func firstChoiceFollowsTheSystem() {
        #expect(AppLanguage.fromSystem(["ko-KR", "en-US"]) == .ko)
        #expect(AppLanguage.fromSystem(["en-US", "ko-KR"]) == .en)
        #expect(AppLanguage.fromSystem(["ja-JP"]) == .en)
        #expect(AppLanguage.fromSystem([]) == .en)
        #expect(AppLanguage.allCases.map(\.displayName) == ["한국어", "English"])
    }

    @Test func presentationSpeaksEnglish() throws {
        try L10n.$override.withValue(.en) {
            #expect(Presentation.ago(30) == "just now")
            #expect(Presentation.ago(120) == "2 min ago")
            #expect(Presentation.label(LastResult.ok) == "● OK")
            #expect(Presentation.label(ItemState.incoming) == "↓ Incoming")
            #expect(Presentation.settingHelp("theme") == "Theme")
            #expect(Presentation.settingHelp("skillOverrides.brand") == "Turn skill brand on or off")
            #expect(Presentation.settingHelp("somethingNew") == "Claude Code setting somethingNew")
            #expect(Presentation.storage(Storage(backend: .notion, page: "0123456789abcdef0123456789abcdef", auth: "oauth"))
                    == StorageText(title: "Notion", detail: "Signed in with Notion · page 01234567…"))
            #expect(Presentation.storage(nil) == StorageText(title: "No storage info", detail: ""))
            for kind in ["claude_md", "settings", "skills", "mods", "local"] {
                #expect(!Presentation.groupHelp(kind).contains(where: { ("가"..."힣").contains($0) }))
            }
            let s = try EngineJSON.decode(Status.self, from: Data(#"{"last_result": "never"}"#.utf8))
            #expect(Presentation.menuBar(s, syncing: false, failure: nil).headline == "Not synced yet")
            var cal = Calendar(identifier: .gregorian)
            cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
            let now = 1_792_480_000.0
            #expect(Presentation.clock(now - 3600, now: now, calendar: cal) == "today 15:06")
        }
    }

    @Test func descriptionsInTheOtherLanguageAreTranslated() {
        let english = "Brand voice, visual identity, messaging frameworks, asset management, brand consistency."
        let korean = "회사 브랜드의 색과 글꼴, 말투를 정리한 지침이에요."
        #expect(DescriptionLanguage.needsTranslation(english, to: .ko))
        #expect(!DescriptionLanguage.needsTranslation(korean, to: .ko))
        #expect(DescriptionLanguage.needsTranslation(korean, to: .en))
        #expect(!DescriptionLanguage.needsTranslation(english, to: .en))
        #expect(!DescriptionLanguage.needsTranslation("", to: .ko))
        let japanese = "会社のブランドの色とフォント、話し方をまとめた指針です。"
        #expect(DescriptionLanguage.needsTranslation(japanese, to: .ko))
        #expect(DescriptionLanguage.needsTranslation(japanese, to: .en))
        #expect(DescriptionLanguage.source(of: japanese) == "ja")
        #expect(DescriptionLanguage.source(of: english) == "en")
    }

    @Test func translationsAreKeptPerLanguage() {
        var cache = TranslationCache()
        #expect(!cache.has("Brand voice", in: .ko))
        cache.store("Brand voice", "브랜드 말투", in: .ko)
        #expect(cache.has("Brand voice", in: .ko))
        #expect(cache.text(for: "Brand voice", in: .ko) == "브랜드 말투")
        #expect(cache.text(for: "Brand voice", in: .en) == "Brand voice")
        #expect(TranslationCache(dictionary: cache.dictionary) == cache)
    }
}
