// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeRCMenu",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "ClaudeRCMenu"),
        .testTarget(name: "ClaudeRCMenuTests", dependencies: ["ClaudeRCMenu"]),
    ]
)
