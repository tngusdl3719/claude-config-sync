import Foundation

/// A sync app's folder on this Mac (iCloud Drive, Dropbox, Google Drive, OneDrive…): where a folder storage
/// can live. The storage itself is a "Claude Sync" folder inside it.
public struct CloudFolder: Equatable, Identifiable, Sendable {
    public var name: String
    public var root: String
    public var store: String { root + "/Claude Sync" }
    public var id: String { root }
}

public enum CloudFolders {
    static let iCloud = "Library/Mobile Documents/com~apple~CloudDocs"
    static let cloudStorage = "Library/CloudStorage"
    /// Google Drive keeps the person's files one level down, under a name in the system's language.
    static let myDrive = ["My Drive", "내 드라이브"]

    /// The sync apps' folders this Mac has: iCloud Drive, then those under ~/Library/CloudStorage, then an
    /// older Dropbox in the home folder.
    public static func find(home: String, fm: FileManager = .default) -> [CloudFolder] {
        func isFolder(_ path: String) -> Bool {
            var dir: ObjCBool = false
            return fm.fileExists(atPath: path, isDirectory: &dir) && dir.boolValue
        }
        var found: [CloudFolder] = []
        if isFolder(home + "/" + iCloud) { found.append(CloudFolder(name: "iCloud Drive", root: home + "/" + iCloud)) }
        let storage = home + "/" + cloudStorage
        for entry in ((try? fm.contentsOfDirectory(atPath: storage)) ?? []).sorted() where !entry.hasPrefix(".") {
            let path = storage + "/" + entry
            guard isFolder(path) else { continue }
            let root = myDrive.map { path + "/" + $0 }.first(where: isFolder) ?? path
            found.append(CloudFolder(name: name(ofEntry: entry), root: root))
        }
        if isFolder(home + "/Dropbox") && !found.contains(where: { $0.name.hasPrefix("Dropbox") }) {
            found.append(CloudFolder(name: "Dropbox", root: home + "/Dropbox"))
        }
        return found
    }

    /// "GoogleDrive-me@example.com" → "Google Drive (me@example.com)", "Dropbox" → "Dropbox".
    static func name(ofEntry entry: String) -> String {
        let parts = entry.split(separator: "-", maxSplits: 1).map(String.init)
        let app = ["GoogleDrive": "Google Drive", "OneDrive": "OneDrive", "Dropbox": "Dropbox", "Box": "Box"][parts[0]] ?? parts[0]
        guard parts.count == 2, !parts[1].isEmpty, app != "Box" else { return app }
        return "\(app) (\(parts[1]))"
    }

    /// Where a folder is, for people: "iCloud Drive/Claude Sync", "~/Sync/claude", or the path itself.
    public static func describe(_ path: String, home: String) -> String {
        let roots: [(String, String)] = {
            var r = [(home + "/" + iCloud, "iCloud Drive"), (home + "/Dropbox", "Dropbox")]
            let storage = home + "/" + cloudStorage + "/"
            if path.hasPrefix(storage) {
                let entry = String(path.dropFirst(storage.count).prefix { $0 != "/" })
                let base = storage + entry
                r += myDrive.map { (base + "/" + $0, name(ofEntry: entry)) } + [(base, name(ofEntry: entry))]
            }
            return r
        }()
        for (root, name) in roots where path == root || path.hasPrefix(root + "/") {
            return name + String(path.dropFirst(root.count))
        }
        if path == home || path.hasPrefix(home + "/") { return "~" + String(path.dropFirst(home.count)) }
        return path
    }
}
