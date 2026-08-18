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
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        guard let handle = try? openAppending(at: url) else { return }
        defer { try? handle.close() }
        try? handle.write(contentsOf: data)
    }

    /// Открывает файл лога на дозапись с флагом `O_APPEND`, создавая его при
    /// необходимости с правами 0600. Общая точка входа для приложения (этот файл)
    /// и для `BotSupervisor`, который тем же дескриптором пишет stdout/stderr бота.
    ///
    /// В файл пишут два независимых процесса-писателя одновременно (бот и
    /// приложение о себе), а `FileHandle(forWritingTo:)` курсора не двигает — у
    /// каждого открытого дескриптора своё смещение, и параллельная запись одного
    /// перезаписывает поверх другого. Теряются именно диагностические строки, ради
    /// которых лог и заведён (см. потерю записи о гашении бота по сигналу).
    /// `O_APPEND` заставляет ядро атомарно переносить указатель в конец файла перед
    /// каждым `write(2)`, и это работает независимо от того, сколько процессов
    /// пишут одновременно — в отличие от ручного `seekToEnd()`, между которым и
    /// записью может вклиниться другой писатель. Если решишь вернуть
    /// `FileHandle(forWritingTo:)` — не надо, это тот самый баг.
    static func openAppending(at url: URL) throws -> FileHandle {
        let fd = open(url.path, O_WRONLY | O_CREAT | O_APPEND, 0o600)
        guard fd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
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
