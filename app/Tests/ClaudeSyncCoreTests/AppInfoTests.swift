import ClaudeSyncCore
import Testing

@Test func versionMatchesBuildScript() {
    #expect(AppInfo.version == "0.6.2")
    #expect(AppInfo.name == "Claude Sync")
}
