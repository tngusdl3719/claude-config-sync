import ClaudeSyncCore
import Foundation
import Testing

@Suite struct WizardTests {
    @Test func repoURLAcceptsHTTPSAndAddsGit() {
        #expect(Wizard.checkRepoURL("  https://gitlab.com/me/claude-config.git \n") == .ok("https://gitlab.com/me/claude-config.git"))
        #expect(Wizard.checkRepoURL("https://gitlab.com/me/claude-config") == .ok("https://gitlab.com/me/claude-config.git"))
        #expect(Wizard.checkRepoURL("https://gitlab.com/me/claude-config/") == .ok("https://gitlab.com/me/claude-config.git"))
    }

    @Test(arguments: [
        "git@gitlab.com:me/claude-config.git",
        "http://gitlab.com/me/claude-config.git",
        "https://gitlab.com/me/claude config.git",
        "https://gitlab.com",
        "https://user:pass@gitlab.com/me/c.git",
        "",
    ])
    func repoURLRejects(raw: String) {
        guard case .invalid = Wizard.checkRepoURL(raw) else {
            Issue.record("accepted \(raw)")
            return
        }
    }

    @Test func accessTokensPage() {
        #expect(Wizard.accessTokensPage("https://gitlab.com/a/b.git")?.absoluteString == "https://gitlab.com/a/b/-/settings/access_tokens")
        #expect(Wizard.accessTokensPage("https://gitlab.example.org/group/sub/proj.git")?.absoluteString
                == "https://gitlab.example.org/group/sub/proj/-/settings/access_tokens")
        #expect(Wizard.accessTokensPage("not a url") == nil)
        #expect(Wizard.accessTokensPage("https://github.com/me/claude-config.git")?.absoluteString
                == "https://github.com/settings/personal-access-tokens/new")
    }

    @Test func eachStorageInTheListSaysWhatItIs() {
        L10n.$override.withValue(.ko) {
            #expect(StorageKind.allCases.map(\.blurb) == ["GitLab 저장소에 액세스 토큰으로 올리고 받아요",
                                                           "GitHub 비공개 저장소에 토큰으로 올리고 받아요",
                                                           "Notion 페이지에 로그인해서 올리고 받아요"])
            #expect(StorageKind.allCases.map(\.moveTitle) == ["GitLab으로 옮기기", "GitHub으로 옮기기", "Notion으로 옮기기"])
        }
        L10n.$override.withValue(.en) {
            #expect(StorageKind.notion.blurb == "A Notion page you sign in to")
            #expect(StorageKind.github.moveTitle == "Move to GitHub")
        }
    }

    @Test func storageKinds() {
        #expect(StorageKind.allCases.map(\.title) == ["GitLab", "GitHub", "Notion"])
        #expect(StorageKind.allCases.map(\.backend) == [.git, .git, .notion])
        #expect(StorageKind.of(Storage(backend: .git, remote: "https://github.com/me/c.git")) == .github)
        #expect(StorageKind.of(Storage(backend: .git, remote: "https://gitlab.com/me/c.git")) == .gitlab)
        #expect(StorageKind.of(Storage(backend: .git, remote: "https://git.example.com/me/c.git")) == .gitlab)
        #expect(StorageKind.of(Storage(backend: .git, remote: "git@github.com:me/c.git")) == .github)
        #expect(StorageKind.of(Storage(backend: .git, remote: "ssh://git@github.com/me/c.git")) == .github)
        #expect(StorageKind.of(Storage(backend: .git, remote: "git@gitlab.com:me/c.git")) == .gitlab)
        #expect(Wizard.gitHost("git@github.com:me/c.git") == "github.com")
        #expect(Wizard.gitHost("https://pc@gitlab.com/me/c.git") == "gitlab.com")
        #expect(Wizard.gitHost("claude-sync-server:claude-config.git") == "claude-sync-server")
        for github in ["https://GitHub.com/me/c.git", "ssh://git@ssh.github.com:443/me/c.git", "ssh://git@github.com:22/me/c.git"] {
            #expect(Wizard.isGitHub(github), "\(github)")
            #expect(StorageKind.of(Storage(backend: .git, remote: github)) == .github)
            #expect(Presentation.storage(Storage(backend: .git, remote: github)).title == "GitHub")
        }
        for other in ["https://notgithub.com/me/c.git", "https://github.mycorp.com/me/c.git", "git@github.com-work:me/c.git"] {
            #expect(!Wizard.isGitHub(other), "\(other)")
            #expect(StorageKind.of(Storage(backend: .git, remote: other)) == .gitlab)
            #expect(Presentation.storage(Storage(backend: .git, remote: other)).title != "GitHub")
        }
        #expect(Wizard.accessTokensPage("https://GitHub.com/me/c.git")?.host == "github.com")
        #expect(StorageKind.of(Storage(backend: .notion, page: "p")) == .notion)
        #expect(StorageKind.of(nil) == .gitlab)
        #expect(Wizard.newRepoPage(.github).absoluteString == "https://github.com/new")
        // The token help follows the address typed; until one is typed, the tab chosen.
        #expect(StorageKind.forAddress("https://github.com/me/c.git", chosen: .gitlab) == .github)
        #expect(StorageKind.forAddress("https://gitlab.com/me/c.git", chosen: .github) == .gitlab)
        #expect(StorageKind.forAddress("https://git.example.com/me/c.git", chosen: .github) == .gitlab)
        #expect(StorageKind.forAddress("", chosen: .github) == .github)
        #expect(StorageKind.forAddress("not an address", chosen: .github) == .github)
        #expect(StorageKind.forAddress("https://github.com/me/c.git", chosen: .notion) == .notion)
        L10n.$override.withValue(.ko) {
            #expect(Presentation.storageLink(Storage(backend: .git, remote: "https://github.com/me/c.git")) == "GitHub에서 열기")
            #expect(Presentation.storageLink(Storage(backend: .git, remote: "https://gitlab.com/me/c.git")) == "GitLab에서 열기")
            #expect(Presentation.storageLink(Storage(backend: .git, remote: "https://git.example.com/me/c.git")) == "브라우저에서 열기")
            #expect(Presentation.storageLink(Storage(backend: .notion, page: "p")) == "Notion에서 페이지 열기")
        }
        L10n.$override.withValue(.en) {
            #expect(Presentation.storageLink(Storage(backend: .git, remote: "https://git.example.com/me/c.git")) == "Open in the browser")
        }
        #expect(Wizard.newRepoPage(.gitlab).absoluteString == "https://gitlab.com/projects/new")
    }

    @Test func pcNames() {
        #expect(Wizard.sanitizePCName("Gildong's MacBook Pro") == "gildongs-macbook-pro")
        #expect(Wizard.sanitizePCName("회사 맥") == "my-mac")
        #expect(Wizard.sanitizePCName("work--mac__2") == "work-mac-2")
        #expect(Wizard.sanitizePCName(String(repeating: "a", count: 80)).count == 40)
        #expect(Wizard.isValidPCName("company-mac"))
        #expect(!Wizard.isValidPCName("회사"))
        #expect(!Wizard.isValidPCName(""))
    }

    @Test func wordsAndToken() {
        #expect(Wizard.words(" corp , internal-host\nProject-Z,, corp ") == ["corp", "internal-host", "Project-Z"])
        #expect(Wizard.words("") == [])
        #expect(Wizard.cleanToken(" glpat-abc\n") == "glpat-abc")
    }

    @Test func notionPageAddresses() {
        let id = "0123456789abcdef0123456789abcdef"
        #expect(Wizard.checkNotionPage("https://www.notion.so/me/Claude-Sync-\(id)?pvs=4") == .ok(id))
        #expect(Wizard.checkNotionPage(" https://www.notion.so/\(id)#x \n") == .ok(id))
        #expect(Wizard.checkNotionPage("01234567-89ab-cdef-0123-456789ABCDEF") == .ok(id))
        #expect(Wizard.checkNotionPage(id) == .ok(id))
        for bad in ["", "https://www.notion.so/no-id-here", "https://gitlab.com/a/b.git", "1234"] {
            guard case .invalid = Wizard.checkNotionPage(bad) else {
                Issue.record("accepted \(bad)")
                continue
            }
        }
        #expect(Wizard.notionIntegrationsPage.absoluteString == "https://www.notion.so/profile/integrations")
    }

    @Test func recommendedKeysMatchTheFirstDesign() {
        #expect(Wizard.recommendedKeys.count == 12)
        #expect(Wizard.recommendedKeys.contains("permissions.defaultMode"))
        #expect(!Wizard.recommendedKeys.contains("hooks"))
    }
}
