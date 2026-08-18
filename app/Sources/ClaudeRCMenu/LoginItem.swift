import Foundation
import ServiceManagement

/// Автозапуск при входе.
///
/// `SMAppService` требует подписанного бандла, а у нас ad-hoc подпись — отказ здесь
/// ожидаемая ветка, а не редкость. Запасной путь пишет LaunchAgent руками.
enum LoginItem {
    private static let label = "com.nvinnikov.claude-rc-app"

    static var isEnabled: Bool {
        if SMAppService.mainApp.status == .enabled { return true }
        return FileManager.default.fileExists(atPath: agentURL.path)
    }

    static func enable() throws {
        do {
            try SMAppService.mainApp.register()
        } catch {
            try writeAgent()
        }
    }

    static func disable() {
        try? SMAppService.mainApp.unregister()
        removeAgent()
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
