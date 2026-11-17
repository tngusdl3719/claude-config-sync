import ClaudeSyncCore
import Foundation
import Testing

/// A synced folder as storage: the sync apps' folders this Mac has, how one is shown, and what the engine gets.
@Suite struct FolderStorageTests {
    final class Home {
        let path: String
        init() throws {
            path = FileManager.default.temporaryDirectory.appendingPathComponent("home-\(UUID().uuidString)").path
            try FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true)
        }
        deinit { try? FileManager.default.removeItem(atPath: path) }
        func folder(_ relative: String) throws {
            try FileManager.default.createDirectory(atPath: path + "/" + relative, withIntermediateDirectories: true)
        }
        func file(_ relative: String) throws {
            try Data().write(to: URL(fileURLWithPath: path + "/" + relative))
        }
    }

    @Test func theSyncAppsFoldersThisMacHasAreOffered() throws {
        let home = try Home()
        try home.folder("Library/Mobile Documents/com~apple~CloudDocs")
        try home.folder("Library/CloudStorage/Dropbox")
        try home.folder("Library/CloudStorage/GoogleDrive-me@example.com/My Drive")
        try home.folder("Library/CloudStorage/OneDrive-Personal")
        try home.file("Library/CloudStorage/.DS_Store")
        let found = CloudFolders.find(home: home.path)
        #expect(found.map(\.name) == ["iCloud Drive", "Dropbox", "Google Drive (me@example.com)", "OneDrive (Personal)"])
        #expect(found[0].store == home.path + "/Library/Mobile Documents/com~apple~CloudDocs/Claude Sync")
        #expect(found[2].root == home.path + "/Library/CloudStorage/GoogleDrive-me@example.com/My Drive")
    }

    @Test func anOlderDropboxAndAGoogleDriveInKoreanAreFoundToo() throws {
        let home = try Home()
        try home.folder("Dropbox")
        try home.folder("Library/CloudStorage/GoogleDrive-me@example.com/내 드라이브")
        let found = CloudFolders.find(home: home.path)
        #expect(found.map(\.name) == ["Google Drive (me@example.com)", "Dropbox"])
        #expect(found[0].root.hasSuffix("/내 드라이브"))
        #expect(found[1].root == home.path + "/Dropbox")
        #expect(CloudFolders.find(home: try Home().path).isEmpty)
    }

    @Test func aFolderStorageIsShownByWhereItIs() {
        let home = "/Users/me"
        func shown(_ folder: String) -> StorageText {
            Presentation.storage(Storage(backend: .folder, folder: folder), home: home)
        }
        L10n.$override.withValue(.ko) {
            #expect(shown("/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Claude Sync")
                    == StorageText(title: "동기화 폴더", detail: "iCloud Drive/Claude Sync"))
            #expect(shown("/Users/me/Library/CloudStorage/GoogleDrive-me@example.com/My Drive/Claude Sync").detail
                    == "Google Drive (me@example.com)/Claude Sync")
            #expect(shown("/Users/me/Dropbox/Claude Sync").detail == "Dropbox/Claude Sync")
            #expect(shown("/Users/me/Sync/claude").detail == "~/Sync/claude")
            #expect(shown("/Volumes/nas/claude").detail == "/Volumes/nas/claude")
            #expect(Presentation.storageLink(Storage(backend: .folder, folder: "/x")) == "Finder에서 열기")
        }
    }

    @Test func theFolderIsAStorageKindOfItsOwn() {
        L10n.$override.withValue(.ko) {
            #expect(StorageKind.folder.title == "동기화 폴더")
            #expect(StorageKind.folder.moveTitle == "동기화 폴더로 옮기기")
            #expect(StorageKind.folder.backend == .folder)
        }
        L10n.$override.withValue(.en) {
            #expect(StorageKind.folder.moveTitle == "Move to a sync folder")
        }
        #expect(StorageKind.of(Storage(backend: .folder, folder: "/x")) == .folder)
        #expect(StorageKind.forAddress("https://github.com/me/c.git", chosen: .folder) == .folder)
    }

    @Test func setUpJoinAndMovePassTheFolderAndNothingOnStdin() async throws {
        let f = try FakeEngines()
        let dir = f.dir.path
        let engine = try f.script("record", """
        printf '%s\\n' "$@" >> "\(dir)/argv"
        cat >> "\(dir)/stdin"
        echo '{"result": "ok", "ok": true}'
        """)
        let client = EngineClient(runner: EngineRunner(executable: engine))
        var choice = InitChoice(remote: "", username: "pc-a", pc: "pc-a", skills: [], mods: [], keys: ["theme"],
                                moveClaudeMd: true, requireApproval: false, backend: .folder)
        choice.folder = "/x/Claude Sync"
        _ = await client.initFirstPC(choice, token: "")
        _ = await client.joinFolder(folder: "/x/Claude Sync", pc: "pc-b", words: ["corp"], receiveOnly: true)
        _ = await client.setFolderStorage(folder: "/x/Claude Sync")
        #expect(f.read("argv").components(separatedBy: "\n").dropLast() == [
            "init", "--backend", "folder", "--folder", "/x/Claude Sync", "--pc", "pc-a",
            "--skills", "", "--mods", "", "--keys", "theme", "--claude-md", "move",
            "join", "--backend", "folder", "--folder", "/x/Claude Sync", "--pc", "pc-b", "--words", "corp", "--receive-only",
            "set-remote", "--backend", "folder", "--folder", "/x/Claude Sync"])
        #expect(f.read("stdin") == "")
    }

    @Test func aFolderStorageIsReadFromStatus() throws {
        let s = try EngineJSON.decode(Status.self, from: Data(#"{"storage": {"backend": "folder", "folder": "/x/Claude Sync"}}"#.utf8))
        #expect(s.storage == Storage(backend: .folder, folder: "/x/Claude Sync"))
    }
}
