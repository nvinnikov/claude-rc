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
    private let takeOverRow = NSMenuItem(title: "Take over bot", action: nil, keyEquivalent: "")
    private let setupRow = NSMenuItem(title: "Run setup…", action: nil, keyEquivalent: "")
    private let setupNoteRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var setupError: String?
    private let configRow = NSMenuItem(title: "Reveal config", action: nil, keyEquivalent: "")
    private let loginRow = NSMenuItem(title: "Launch at login", action: nil, keyEquivalent: "")
    private let loginNoteRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var loginItemError: String?
    private var signalSources: [DispatchSourceSignal] = []

    /// Без супервизора (CLI не нашли или это второй экземпляр) `menuNeedsUpdate`
    /// перерисовывает меню на каждое открытие — не только один раз в `applicationDidFinishLaunching`.
    /// Раньше в этом случае она всегда рисовала жёстко зашитое «CLI not found»,
    /// затирая настоящую причину («уже запущен другой экземпляр») уже на первом
    /// открытии меню. Причина решается один раз при старте и живёт здесь.
    private var stalledReason = "CLI not found"

    func applicationDidFinishLaunching(_ notification: Notification) {
        installSignalHandlers()
        Log.app(
            "launch: pid=\(ProcessInfo.processInfo.processIdentifier)"
                + " home=\(NSHomeDirectory())"
                + " path=\(ProcessInfo.processInfo.environment["PATH"] ?? "<nil>")"
        )

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = icon(alive: false)
        item.menu = buildMenu()
        statusItem = item

        // Второй экземпляр (прямой запуск бинаря рядом с уже открытым .app, а не
        // через `open`) не должен поднимать второго поллера — см. SingleInstance.
        guard !SingleInstance.check() else {
            Log.app("launch: другой экземпляр (bundle \(Bundle.main.bundleIdentifier ?? "?")) уже запущен, бот не поднимаем")
            stalledReason = "уже запущен другой экземпляр приложения"
            render(.crashed(reason: stalledReason))
            return
        }

        cli = CLILocator.find(
            searchPaths: CLILocator.defaultSearchPaths,
            environmentPath: ProcessInfo.processInfo.environment["PATH"]
        )
        if let cli {
            Log.app("cli found: \(cli.path)")
            let supervisor = BotSupervisor(cli: cli, logURL: logURL)
            supervisor.onStateChange = { [weak self] state in
                Log.app("bot state -> \(state)")
                self?.render(state)
            }
            self.supervisor = supervisor
            supervisor.start()
        } else {
            Log.app("cli not found; searchPaths=\(CLILocator.defaultSearchPaths)")
            render(.crashed(reason: "CLI not found"))
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Бот живёт внутри приложения — это выбранная модель владения, и иконка
        // всё время честно её показывала. Подтверждения не спрашиваем.
        supervisor?.stop()
    }

    /// `applicationWillTerminate` не срабатывает при завершении сигналом (падение,
    /// `pkill`, выход из системы) — Cocoa просто не успевает добежать до него.
    /// Без этого ребёнок остаётся сиротой с ppid 1 и живёт дальше сам по себе.
    ///
    /// `signal(SIG_IGN)` обязателен перед `DispatchSource.makeSignalSource`:
    /// иначе дефолтный обработчик убивает процесс раньше, чем источник успевает
    /// сработать. SIGKILL сюда не попадает — его нельзя перехватить в принципе,
    /// так что сирота при `kill -9` самого приложения всё ещё возможна. Это
    /// осознанный компромисс: закрываем частые случаи, не все теоретические.
    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in self?.terminateOnSignal(sig) }
            source.resume()
            signalSources.append(source)
        }
    }

    private func terminateOnSignal(_ sig: Int32) {
        Log.app("signal \(sig): гасим бота перед выходом")
        supervisor?.stop()
        exit(0)
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

        setupRow.action = #selector(runSetup)
        setupRow.target = self
        setupRow.isHidden = true
        menu.addItem(setupRow)

        setupNoteRow.isEnabled = false
        setupNoteRow.isHidden = true
        menu.addItem(setupNoteRow)

        takeOverRow.action = #selector(takeOverBot)
        takeOverRow.target = self
        takeOverRow.isHidden = true
        menu.addItem(takeOverRow)

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
        render(supervisor?.state ?? .crashed(reason: stalledReason))
        configRow.isEnabled = cli != nil
        setupRow.isHidden = !isNotConfigured(supervisor?.state)
        renderSetupNote()
        loginRow.state = LoginItem.isEnabled ? .on : .off
        renderLoginNote()
        // Человек мог пройти визард в соседнем Терминале, не возвращаясь в
        // приложение, — не заставляем его ещё и кликать "Start bot" вслепую,
        // чтобы просто узнать, подхватился ли конфиг. Метод сам не запускает
        // бота и не делает ничего, если состояние сейчас не .notConfigured.
        supervisor?.recheckConfigurationIfNeeded()
    }

    /// Показывает причину, по которой попытка открыть визард не сработала. Привязана
    /// к видимости `setupRow`, а не только к наличию ошибки: пункт про визард пропадает,
    /// как только конфиг находится, и застрявшая под ним причина не должна пережить его.
    private func renderSetupNote() {
        guard !setupRow.isHidden, let setupError else {
            setupNoteRow.isHidden = true
            return
        }
        setupNoteRow.title = "⚠ \(setupError)"
        setupNoteRow.isHidden = false
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
        case .foreignBotRunning(let pid):
            statusRow.title = "Bot: уже запущен вне приложения, pid \(pid)"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = cli != nil
        case .notConfigured:
            statusRow.title = "Bot: not configured"
            toggleRow.title = "Start bot"
            // Доступна: start() сам перепроверяет конфиг. Раньше кнопка была
            // недоступна именно тут — единственный выход из .notConfigured был
            // перезапуск приложения, а ради этого весь визард и делался.
            toggleRow.isEnabled = cli != nil
        }
        takeOverRow.isHidden = !showsTakeOverRow(for: state)
        takeOverRow.title = takeOverRowTitle(for: state) ?? takeOverRow.title
        statusItem?.button?.image = icon(alive: isAlive(state))
    }

    private func isAlive(_ state: BotState) -> Bool {
        if case .running = state { return true }
        return false
    }

    /// Отдельно от `isAlive` (та про иконку — «процесс реально поднят»): здесь про
    /// то, что должен делать клик по кнопке. В `.starting` подпись уже «Stop bot»
    /// (см. `render`) — раньше `toggleBot` этого не знал и уводил в повторный
    /// `start()`, потому что `isAlive(.starting) == false`. Видно в окне ожидания
    /// `takeOver` (до 3 с).
    private func shouldStopOnToggle(_ state: BotState) -> Bool {
        switch state {
        case .running, .starting: return true
        default: return false
        }
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
        if shouldStopOnToggle(supervisor.state) {
            supervisor.stop()
        } else {
            supervisor.start()
        }
    }

    @objc private func takeOverBot() {
        supervisor?.takeOver()
    }

    @objc private func runSetup() {
        setupError = nil
        guard let cli else {
            // Не должно случиться — пункт виден только когда cli уже найден при
            // старте (см. render(.notConfigured)) — но молчать при этом хуже, чем
            // сказать очевидное: человек нажал пункт меню и не увидел ничего.
            setupError = "CLI не найден"
            Log.app("runSetup: cli не найден, визард не открываем")
            renderSetupNote()
            return
        }
        // Открываем Терминал, а не запускаем визард внутри: он интерактивный,
        // а у приложения нет ни stdin, ни места, где показать вопросы.
        // shellQuoted, а не просто "\(cli.path)" в кавычках: путь пользователя может
        // содержать пробел или саму одинарную кавычку (`/Users/имя фамилия/...`,
        // `/Users/o'brien/...`) — без честного экранирования это разваливает скрипт.
        let script = "clear; \(shellQuoted(cli.path)) setup"
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        // Имя с UUID — второй клик до того, как первый скрипт дочитан Терминалом,
        // не должен переписать файл, который тот ещё открывает.
        let temp = FileManager.default.temporaryDirectory
            .appendingPathComponent("claude-rc-setup-\(UUID().uuidString).command")
        do {
            try "#!/bin/sh\n\(script)\n".write(to: temp, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: temp.path)
        } catch {
            setupError = "не удалось подготовить скрипт визарда: \(error.localizedDescription)"
            Log.app("runSetup: \(setupError!)")
            renderSetupNote()
            return
        }
        NSWorkspace.shared.open(
            [temp], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration()
        ) { [weak self] _, error in
            DispatchQueue.main.async {
                // Терминал успевает прочитать сам файл сразу при запуске — 2с с
                // запасом на медленный старт приложения, дальше скрипт ему не нужен.
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    try? FileManager.default.removeItem(at: temp)
                }
                guard let self else { return }
                if let error {
                    self.setupError = "не удалось открыть Терминал: \(error.localizedDescription)"
                    Log.app("runSetup: \(self.setupError!)")
                } else {
                    self.setupError = nil
                }
                self.renderSetupNote()
            }
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
            // stderr у GUI-приложения, запущенного через Finder/`open`, уходит в
            // /dev/null (см. Log.swift) — без Log.app этого сообщения не существует.
            let message = "claude-rc: doctor --json не ответил за 5с, показываем каталог по умолчанию"
            FileHandle.standardError.write(Data((message + "\n").utf8))
            Log.app(message)
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
                // человек видит невключившуюся галочку без единой причины. stderr
                // здесь тоже не гарантирует ничего (см. два выше) — основной канал
                // Log.app, stderr оставлен как есть.
                loginItemError = error.localizedDescription
                let message = "claude-rc: не удалось включить автозапуск: \(error.localizedDescription)"
                FileHandle.standardError.write(Data((message + "\n").utf8))
                Log.app(message)
            }
        }
        loginRow.state = LoginItem.isEnabled ? .on : .off
        renderLoginNote()
    }
}

func isNotConfigured(_ state: BotState?) -> Bool {
    if case .notConfigured = state { return true }
    return false
}

/// Честное POSIX-экранирование, а не просто обёртка в кавычки: одинарная кавычка
/// внутри самой строки (`/Users/o'brien/...`) закрыла бы такую обёртку раньше
/// времени. Стандартный приём — закрыть кавычку, экранированная одинарная
/// кавычка снаружи, открыть кавычку заново: `'` → `'\''`.
func shellQuoted(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}
