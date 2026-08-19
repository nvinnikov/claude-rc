import Foundation

/// Где лежит `claude-rc` и с каким окружением его звать.
enum CLILocator {
    /// Каталоги, куда тулза попадает при обычных способах установки.
    static let defaultSearchPaths: [String] = [
        NSHomeDirectory() + "/.local/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]

    /// Каталоги, без которых бот не найдёт tmux и claude. Приложение из автозапуска
    /// получает голый PATH — те же грабли, что были в launchd-плисте.
    private static let requiredPathEntries: [String] = [
        NSHomeDirectory() + "/.local/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]

    static func find(searchPaths: [String], environmentPath: String?) -> URL? {
        let fromEnvironment = (environmentPath ?? "")
            .split(separator: ":", omittingEmptySubsequences: true)
            .map(String.init)

        for directory in searchPaths + fromEnvironment {
            let candidate = URL(fileURLWithPath: directory).appendingPathComponent("claude-rc")
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func childEnvironment(base: [String: String]) -> [String: String] {
        var environment = base
        let existing = (base["PATH"] ?? "")
            .split(separator: ":", omittingEmptySubsequences: true)
            .map(String.init)

        var seen = Set<String>()
        let merged = (existing + requiredPathEntries).filter { seen.insert($0).inserted }
        environment["PATH"] = merged.joined(separator: ":")
        return environment
    }
}
