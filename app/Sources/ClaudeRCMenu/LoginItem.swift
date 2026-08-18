import Foundation
import ServiceManagement

/// Автозапуск при входе.
///
/// `SMAppService` требует подписанного бандла, а у нас ad-hoc подпись — отказ здесь
/// ожидаемая ветка, а не редкость. Запасной путь пишет LaunchAgent руками.
enum LoginItem {
    private static let label = "com.nvinnikov.claude-rc-app"

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

        let load = Process()
        load.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        load.arguments = ["bootstrap", "gui/\(getuid())", agentURL.path]
        try? load.run()
        load.waitUntilExit()
    }

    private static func removeAgent() {
        let unload = Process()
        unload.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        unload.arguments = ["bootout", "gui/\(getuid())/\(label)"]
        try? unload.run()
        unload.waitUntilExit()
        try? FileManager.default.removeItem(at: agentURL)
    }
}
