// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeRCMenu",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "ClaudeRCMenu"),
        .testTarget(name: "ClaudeRCMenuTests", dependencies: ["ClaudeRCMenu"]),
    ]
)
