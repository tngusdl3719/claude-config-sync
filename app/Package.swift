// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ClaudeSync",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ClaudeSync", targets: ["ClaudeSync"]),
        .library(name: "ClaudeSyncCore", targets: ["ClaudeSyncCore"]),
    ],
    targets: [
        .target(name: "ClaudeSyncCore", path: "Sources/ClaudeSyncCore"),
        .executableTarget(name: "ClaudeSync", dependencies: ["ClaudeSyncCore"], path: "Sources/ClaudeSync"),
        .testTarget(name: "ClaudeSyncCoreTests", dependencies: ["ClaudeSyncCore"], path: "Tests/ClaudeSyncCoreTests"),
    ]
)
