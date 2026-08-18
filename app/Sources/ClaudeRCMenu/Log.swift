import Foundation

/// Диагностика приложения, а не бота: пишет в тот же `~/.claude-rc/claude-rc.log`
/// с отдельным префиксом, а не в stdout/stderr процесса.
///
/// Через `open` (LaunchServices) вывод приложения никуда не подключён и не виден —
/// единственный канал наружу, который переживает такой запуск, это файл. Остаётся
/// в коде насовсем: без него у приложения нет способа рассказать о своей проблеме
/// тому, кто не смотрит в меню.
enum Log {
    private static let url = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent(".claude-rc/claude-rc.log")

    static func app(_ message: String) {
        let timestamp = ISO8601DateFormatter().string(from: Date())
        write("[app \(timestamp)] \(message)\n")
    }

    private static func write(_ line: String) {
        guard let data = line.data(using: .utf8) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        if !manager.fileExists(atPath: url.path) {
            manager.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    }
}

extension BotState: CustomStringConvertible {
    var description: String {
        switch self {
        case .stopped: return "stopped"
        case .starting: return "starting"
        case .running(let since): return "running(since: \(since))"
        case .crashed(let reason): return "crashed(\(reason))"
        case .foreignBotRunning(let pid): return "foreignBotRunning(pid: \(pid))"
        }
    }
}
