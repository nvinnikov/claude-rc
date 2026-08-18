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
    private let configRow = NSMenuItem(title: "Reveal config", action: nil, keyEquivalent: "")
    private let loginRow = NSMenuItem(title: "Launch at login", action: nil, keyEquivalent: "")
    private let loginNoteRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var loginItemError: String?

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

        configRow.action = #selector(revealConfig)
        configRow.target = self
        menu.addItem(configRow)

        menu.addItem(.separator())

        loginRow.action = #selector(toggleLoginItem)
        loginRow.target = self
        menu.addItem(loginRow)

        loginNoteRow.isEnabled = false
        loginNoteRow.isHidden = true
        menu.addItem(loginNoteRow)

        let quit = NSMenuItem(
            title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"
        )
        menu.addItem(quit)
        return menu
    }

    /// Пока меню закрыто, обновлять нечего — поэтому пересборка здесь, а не по таймеру.
    func menuNeedsUpdate(_ menu: NSMenu) {
        render(supervisor?.state ?? .crashed(reason: "CLI not found"))
        configRow.isEnabled = cli != nil
        loginRow.state = LoginItem.isEnabled ? .on : .off
        renderLoginNote()
    }

    /// Показывает причину, по которой галочка не значит «автозапуск точно работает»:
    /// либо явная ошибка запасного пути, либо ожидание подтверждения человеком.
    private func renderLoginNote() {
        if let loginItemError {
            loginNoteRow.title = "⚠ \(loginItemError)"
            loginNoteRow.isHidden = false
        } else if LoginItem.needsApproval {
            loginNoteRow.title = "⚠ Confirm in System Settings → Login Items"
            loginNoteRow.isHidden = false
        } else {
            loginNoteRow.isHidden = true
        }
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
        guard let output = runDoctorJSON(cli: cli) else {
            revealConfigFallback()
            return
        }

        let checks = Doctor.parse(output)
        if let path = Doctor.configPath(in: checks) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        } else {
            revealConfigFallback()
        }
    }

    /// `doctor --json` под таймаутом на главном потоке: то же соображение, что и у
    /// `pgrep` в `BotSupervisor` — зависший внешний вызов не должен вешать меню-бар
    /// целиком. `nil` — не дождались ответа, дальше действуем как при пустом выводе.
    private func runDoctorJSON(cli: URL) -> Data? {
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return nil }

        guard exited.wait(timeout: .now() + 5) == .success else {
            task.terminate()
            FileHandle.standardError.write(
                Data("claude-rc: doctor --json не ответил за 5с, показываем каталог по умолчанию\n".utf8)
            )
            return nil
        }
        return pipe.fileHandleForReading.readDataToEndOfFile()
    }

    /// Конфига ещё нет (или `doctor` не ответил вовремя) — показываем каталог, куда его класть.
    private func revealConfigFallback() {
        let directory = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent(".config/claude-rc")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(directory)
    }

    @objc private func toggleLoginItem() {
        if LoginItem.isEnabled {
            LoginItem.disable()
            loginItemError = nil
        } else {
            do {
                try LoginItem.enable()
                loginItemError = nil
            } catch {
                // Запасной путь (LaunchAgent) не смог включиться — молчать нельзя:
                // человек видит невключившуюся галочку без единой причины.
                loginItemError = error.localizedDescription
                FileHandle.standardError.write(
                    Data("claude-rc: не удалось включить автозапуск: \(error.localizedDescription)\n".utf8)
                )
            }
        }
        loginRow.state = LoginItem.isEnabled ? .on : .off
        renderLoginNote()
    }
}
