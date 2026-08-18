import AppKit

/// `@MainActor`: все методы трогают `NSStatusItem`/`NSMenu`, а замыкание из
/// `BotSupervisor.onStateChange` без изоляции класса компилятор Swift 6 не пропускал.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem?
    private var supervisor: BotSupervisor?
    private var cli: URL?

    private let statusRow = NSMenuItem(title: "Bot: stopped", action: nil, keyEquivalent: "")
    private let toggleRow = NSMenuItem(title: "Start bot", action: nil, keyEquivalent: "")
    private let loginRow = NSMenuItem(title: "Launch at login", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = icon(alive: false)
        item.menu = buildMenu()
        statusItem = item

        cli = CLILocator.find(
            searchPaths: CLILocator.defaultSearchPaths,
            environmentPath: ProcessInfo.processInfo.environment["PATH"]
        )
        if let cli {
            let supervisor = BotSupervisor(cli: cli, logURL: logURL)
            supervisor.onStateChange = { [weak self] state in self?.render(state) }
            self.supervisor = supervisor
            supervisor.start()
        } else {
            render(.crashed(reason: "CLI not found"))
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Бот живёт внутри приложения — это выбранная модель владения, и иконка
        // всё время честно её показывала. Подтверждения не спрашиваем.
        supervisor?.stop()
    }

    private var logURL: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".claude-rc/claude-rc.log")
    }

    private func buildMenu() -> NSMenu {
        let menu = NSMenu()
        menu.delegate = self

        statusRow.isEnabled = false
        menu.addItem(statusRow)

        toggleRow.action = #selector(toggleBot)
        toggleRow.target = self
        menu.addItem(toggleRow)

        menu.addItem(.separator())

        let log = NSMenuItem(title: "Open log", action: #selector(openLog), keyEquivalent: "")
        log.target = self
        menu.addItem(log)

        let config = NSMenuItem(title: "Reveal config", action: #selector(revealConfig), keyEquivalent: "")
        config.target = self
        menu.addItem(config)

        menu.addItem(.separator())

        loginRow.action = #selector(toggleLoginItem)
        loginRow.target = self
        menu.addItem(loginRow)

        let quit = NSMenuItem(
            title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"
        )
        menu.addItem(quit)
        return menu
    }

    /// Пока меню закрыто, обновлять нечего — поэтому пересборка здесь, а не по таймеру.
    func menuNeedsUpdate(_ menu: NSMenu) {
        render(supervisor?.state ?? .crashed(reason: "CLI not found"))
        loginRow.state = LoginItem.isEnabled ? .on : .off
    }

    private func render(_ state: BotState) {
        switch state {
        case .stopped:
            statusRow.title = "Bot: stopped"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = cli != nil
        case .starting:
            statusRow.title = "Bot: starting…"
            toggleRow.title = "Stop bot"
            toggleRow.isEnabled = true
        case .running(let since):
            statusRow.title = "Bot: running · \(uptime(since: since))"
            toggleRow.title = "Stop bot"
            toggleRow.isEnabled = true
        case .crashed(let reason):
            statusRow.title = "Bot: \(reason)"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = cli != nil
        }
        statusItem?.button?.image = icon(alive: isAlive(state))
    }

    private func isAlive(_ state: BotState) -> Bool {
        if case .running = state { return true }
        return false
    }

    private func uptime(since: Date) -> String {
        let seconds = Int(Date().timeIntervalSince(since))
        if seconds < 60 { return "\(seconds)s" }
        if seconds < 3600 { return "\(seconds / 60)m" }
        return "\(seconds / 3600)h"
    }

    private func icon(alive: Bool) -> NSImage? {
        let name = alive ? "bolt.fill" : "bolt"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "claude-rc")
        image?.isTemplate = true
        return image
    }

    @objc private func toggleBot() {
        guard let supervisor else { return }
        if isAlive(supervisor.state) {
            supervisor.stop()
        } else {
            supervisor.start()
        }
    }

    @objc private func openLog() {
        NSWorkspace.shared.open(logURL)
    }

    @objc private func revealConfig() {
        guard let cli else { return }
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)
        guard (try? task.run()) != nil else { return }
        task.waitUntilExit()

        let checks = Doctor.parse(pipe.fileHandleForReading.readDataToEndOfFile())
        if let path = Doctor.configPath(in: checks) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        } else {
            // Конфига ещё нет — показываем каталог, куда его класть.
            let directory = URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent(".config/claude-rc")
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            NSWorkspace.shared.open(directory)
        }
    }

    @objc private func toggleLoginItem() {
        if LoginItem.isEnabled {
            LoginItem.disable()
        } else {
            try? LoginItem.enable()
        }
        loginRow.state = LoginItem.isEnabled ? .on : .off
    }
}
