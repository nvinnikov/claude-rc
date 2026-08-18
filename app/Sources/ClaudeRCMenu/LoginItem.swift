import Foundation
import ServiceManagement

/// Автозапуск при входе.
///
/// `SMAppService` требует подписанного бандла, а у нас ad-hoc подпись — отказ здесь
/// ожидаемая ветка, а не редкость. Запасной путь пишет LaunchAgent руками.
enum LoginItem {
    private static let label = "com.nvinnikov.claude-rc-app"

    enum LoginItemError: LocalizedError {
        case launchctlFailed(arguments: [String], status: Int32?)

        var errorDescription: String? {
            switch self {
            case .launchctlFailed(let arguments, let status):
                let command = "launchctl \(arguments.joined(separator: " "))"
                if let status {
                    return "\(command) завершился кодом \(status)"
                }
                return "\(command) не ответил за 5с"
            }
        }
    }

    /// И `.enabled`, и `.requiresApproval` значат, что штатный путь сработал — просто
    /// в первом случае человек уже подтвердил элемент входа, а во втором ещё нет.
    static var isEnabled: Bool {
        isEnabled(status: SMAppService.mainApp.status, agentFileExists: agentFileExists)
    }

    /// Штатная регистрация прошла, но ждёт подтверждения в System Settings →
    /// Login Items — автозапуска фактически ещё нет.
    static var needsApproval: Bool {
        SMAppService.mainApp.status == .requiresApproval
    }

    static func enable() throws {
        do {
            try SMAppService.mainApp.register()
        } catch {
            // `register()` бросает и тогда, когда регистрация всё же прошла и ждёт
            // подтверждения (`.requiresApproval`) — в этом случае запасной путь не
            // нужен: заведём его поверх штатного, и при следующем входе бот стартует
            // дважды, а два поллера одного токена конфликтуют в Telegram.
            guard needsFallback(status: SMAppService.mainApp.status) else { return }
            try writeAgent()
        }
    }

    static func disable() {
        try? SMAppService.mainApp.unregister()
        removeAgent()
    }

    /// Чистая логика важной части решения — без обращения к `SMAppService` — чтобы
    /// её можно было проверить тестом на всех значениях статуса, а не только на тех,
    /// что реально воспроизводятся на машине разработчика.
    static func isEnabled(status: SMAppService.Status, agentFileExists: Bool) -> Bool {
        switch status {
        case .enabled, .requiresApproval:
            return true
        default:
            return agentFileExists
        }
    }

    static func needsFallback(status: SMAppService.Status) -> Bool {
        switch status {
        case .enabled, .requiresApproval:
            return false
        default:
            return true
        }
    }

    private static var agentFileExists: Bool {
        FileManager.default.fileExists(atPath: agentURL.path)
    }

    private static var agentURL: URL {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    private static func writeAgent() throws {
        let bundlePath = Bundle.main.bundlePath
        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": ["/usr/bin/open", "-a", bundlePath],
            "RunAtLoad": true,
        ]
        let data = try PropertyListSerialization.data(
            fromPropertyList: plist, format: .xml, options: 0
        )
        try FileManager.default.createDirectory(
            at: agentURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try data.write(to: agentURL)

        let arguments = ["bootstrap", "gui/\(getuid())", agentURL.path]
        let status = runLaunchctl(arguments)
        guard status == 0 else {
            // `isEnabled` смотрит только на существование файла плиста — если
            // bootstrap не прошёл, галочка встанет, а автозапуска не будет.
            // Бросаем наверх, чтобы AppDelegate показал причину рядом с галочкой.
            Log.app("writeAgent: launchctl bootstrap не прошёл, status=\(status.map(String.init) ?? "nil")")
            throw LoginItemError.launchctlFailed(arguments: arguments, status: status)
        }
    }

    private static func removeAgent() {
        let arguments = ["bootout", "gui/\(getuid())/\(label)"]
        let status = runLaunchctl(arguments)
        if status != 0 {
            // disable() best-effort и не throws (запасной путь снимается в любом
            // случае), но неуспех не должен пропадать бесследно.
            Log.app("removeAgent: launchctl bootout завершился status=\(status.map(String.init) ?? "nil")")
        }
        try? FileManager.default.removeItem(at: agentURL)
    }

    /// `launchctl` без таймаута на главном потоке — тот же класс дефекта, что и
    /// `pgrep`/`doctor --json` (см. их комментарии в BotSupervisor/AppDelegate):
    /// залипший launchd не должен вешать меню-бар навсегда. Возвращает код
    /// завершения (`nil` — не дождались ответа), а не просто факт "запустился".
    private static func runLaunchctl(_ arguments: [String]) -> Int32? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        task.arguments = arguments
        task.standardOutput = Pipe()
        task.standardError = Pipe()

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return nil }

        guard exited.wait(timeout: .now() + 5) == .success else {
            task.terminate()
            Log.app("launchctl \(arguments.joined(separator: " ")) не ответил за 5с")
            return nil
        }
        return task.terminationStatus
    }
}
