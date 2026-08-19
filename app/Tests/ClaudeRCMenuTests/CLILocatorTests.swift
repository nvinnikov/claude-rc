import Foundation
import Testing
@testable import ClaudeRCMenu

// XCTest недоступен: на машине только Command Line Tools, без Xcode.app,
// XCTest.framework физически отсутствует. Перевод на swift-testing — та же
// логика тестов, тот же набор случаев, без установки Xcode.
@Suite struct CLILocatorTests {
    private func makeRoot() throws -> URL {
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("clilocator-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func makeExecutable(in root: URL, _ directory: String, name: String = "claude-rc") throws -> URL {
        let dir = root.appendingPathComponent(directory)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent(name)
        try Data().write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
        return file
    }

    @Test func findsInFirstSearchPath() throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let expected = try makeExecutable(in: root, "first")
        _ = try makeExecutable(in: root, "second")
        let found = CLILocator.find(
            searchPaths: [root.appendingPathComponent("first").path,
                          root.appendingPathComponent("second").path],
            environmentPath: nil
        )
        #expect(found?.path == expected.path)
    }

    @Test func skipsMissingDirectories() throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let expected = try makeExecutable(in: root, "real")
        let found = CLILocator.find(
            searchPaths: [root.appendingPathComponent("nope").path,
                          root.appendingPathComponent("real").path],
            environmentPath: nil
        )
        #expect(found?.path == expected.path)
    }

    @Test func skipsNonExecutableFile() throws {
        // Файл с тем же именем, но без бита исполнения, — не наша тулза.
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let dir = root.appendingPathComponent("plain")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data().write(to: dir.appendingPathComponent("claude-rc"))
        let expected = try makeExecutable(in: root, "real")
        let found = CLILocator.find(
            searchPaths: [dir.path, root.appendingPathComponent("real").path],
            environmentPath: nil
        )
        #expect(found?.path == expected.path)
    }

    @Test func fallsBackToEnvironmentPath() throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let expected = try makeExecutable(in: root, "viapath")
        let found = CLILocator.find(
            searchPaths: [],
            environmentPath: "/nonexistent:\(root.appendingPathComponent("viapath").path)"
        )
        #expect(found?.path == expected.path)
    }

    @Test func returnsNilWhenNothingFound() throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        #expect(CLILocator.find(searchPaths: [root.path], environmentPath: nil) == nil)
    }

    @Test func childEnvironmentAlwaysCarriesToolDirectories() {
        // Приложение из автозапуска получает голый PATH, и бот не найдёт ни tmux,
        // ни claude. На эти грабли уже наступали в launchd-плисте части 1.
        let env = CLILocator.childEnvironment(base: [:])
        let path = env["PATH"] ?? ""
        #expect(path.contains("/opt/homebrew/bin"))
        #expect(path.contains("/usr/bin"))
        #expect(path.contains(".local/bin"))
    }

    @Test func childEnvironmentKeepsExistingPathEntries() {
        let env = CLILocator.childEnvironment(base: ["PATH": "/custom/tools"])
        #expect((env["PATH"] ?? "").contains("/custom/tools"))
    }

    @Test func childEnvironmentDoesNotDuplicateEntries() {
        let env = CLILocator.childEnvironment(base: ["PATH": "/opt/homebrew/bin"])
        let entries = (env["PATH"] ?? "").split(separator: ":").filter { $0 == "/opt/homebrew/bin" }
        #expect(entries.count == 1)
    }

    @Test func childEnvironmentPreservesOtherVariables() {
        let env = CLILocator.childEnvironment(base: ["HOME": "/Users/test"])
        #expect(env["HOME"] == "/Users/test")
    }
}
