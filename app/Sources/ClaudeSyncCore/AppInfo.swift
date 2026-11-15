/// What the app says about itself; the build script stamps the same version into Info.plist.
public enum AppInfo {
    public static let name = "Claude Sync"
    public static let version = "0.6.0"
    /// Where releases come from (GitHub owner/repository).
    public static let repository = "tngusdl3719/claude-config-sync"
    /// The public half of the key that signs releases (scripts/update-key.sh; the private half stays in
    /// the releasing Mac's keychain). An update is installed only when this key checks out its signature.
    public static let updatePublicKey = "zjk51Iwh7e/nchopjje6F9cY0q5Y3YSZbgD/FbmRhMQ="
}
