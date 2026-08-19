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
        guard let handle = try? openAppending(at: url) else { return }
        defer { try? handle.close() }
        try? handle.write(contentsOf: data)
    }

    /// Открывает файл лога на дозапись с флагом `O_APPEND`, создавая каталог и
    /// файл при необходимости с правами 0700/0600. Общая точка входа для
    /// приложения (этот файл) и для `BotSupervisor`, который тем же дескриптором
    /// пишет stdout/stderr бота.
    ///
    /// Права ужимаются здесь безусловно, а не только при первом создании, и это
    /// единственное место, где это делается: раньше каталог создавал ещё и
    /// `Log.write` — без атрибутов, маской по умолчанию (обычно 755) — а права
    /// файла ужимал только `BotSupervisor.appendingHandle`, которого не было, если
    /// CLI не нашли или бота ни разу не запускали. Унаследованный от прошлой жизни
    /// лог с правами 0644 в такой конфигурации продолжал бы молча принимать
    /// записи. Раз правило одно — ему тут и место, а не в каждом писателе отдельно.
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
        let manager = FileManager.default
        let directory = url.deletingLastPathComponent()
        try manager.createDirectory(
            at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)

        let fd = open(url.path, O_WRONLY | O_CREAT | O_APPEND, 0o600)
        guard fd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)

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
