import Foundation

enum BotState {
    case stopped
    case starting
    case running(since: Date)
    case crashed(reason: String)
}

/// Паузы перед перезапуском упавшего бота. `nil` — больше не пытаемся.
func backoffDelay(attempt: Int) -> TimeInterval? {
    switch attempt {
    case 1: return 2
    case 2: return 5
    case 3: return 15
    default: return nil
    }
}

/// Бухгалтерия серии падений подряд.
///
/// «Подряд» — это не просто счётчик стартов: бот, упавший раз в месяц, не должен
/// упереться в лимит на четвёртом падении, если между ними он стабильно работал.
/// Поэтому серия сбрасывается, если процесс перед смертью прожил дольше порога.
struct RestartTally {
    /// Дольше — падение не в серии: бот успел пожить, значит проблема не та же самая.
    static let resetThreshold: TimeInterval = 60

    private(set) var attempt = 0

    /// Зафиксировать смерть процесса, прожившего `uptime` секунд перед падением.
    mutating func recordDeath(afterUptime uptime: TimeInterval) {
        if uptime >= Self.resetThreshold {
            attempt = 0
        }
        attempt += 1
    }

    /// Пауза до следующей попытки после последней зафиксированной смерти.
    /// `nil` — попыток больше не осталось, сдаёмся.
    var nextDelay: TimeInterval? { backoffDelay(attempt: attempt) }
}

/// Владеет процессом бота: запускает, гасит, переживает его падения.
///
/// Состояние «жив» — это `Process.isRunning`, а не догадка по сокету: бот запущен
/// нами, и другого источника правды не нужно.
///
/// `@MainActor`: инвариант «start/stop только с главного потока» раньше ничем не
/// удерживался (класс был помечен `@unchecked Sendable`, что глушит проверку, а не
/// подтверждает отсутствие гонки). Изоляция актёром делает его проверяемым компилятором.
@MainActor
final class BotSupervisor {
    var onStateChange: ((BotState) -> Void)?
    private(set) var state: BotState = .stopped {
        didSet { onStateChange?(state) }
    }

    private let cli: URL
    private let logURL: URL
    private var process: Process?
    private var startedAt: Date?
    private var stopRequested = false
    private var tally = RestartTally()

    init(cli: URL, logURL: URL) {
        self.cli = cli
        self.logURL = logURL
    }

    /// pid бота, запущенного мимо приложения. Два поллера одного токена получают
    /// от Telegram конфликт и работают через раз, поэтому свой мы не поднимаем.
    static func foreignBotPID() -> Int32? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        // Точка в "clauderc.bot" экранирована: pgrep матчит паттерн как regex,
        // и без экранирования она подошла бы под любой символ.
        task.arguments = ["-f", "claude-rc bot|clauderc\\.bot"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return nil }

        // pgrep отвечает мгновенно; таймаут — подстраховка, чтобы внешний вызов
        // не мог зависнуть на главном потоке без объяснения причины.
        guard exited.wait(timeout: .now() + 2) == .success else {
            task.terminate()
            FileHandle.standardError.write(Data("claude-rc: pgrep не ответил за 2с, считаем что чужого бота нет\n".utf8))
            return nil
        }

        let output = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        ) ?? ""
        return output
            .split(separator: "\n")
            .compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }
            .first { $0 != ProcessInfo.processInfo.processIdentifier }
    }

    func start() {
        guard process == nil else { return }
        if let foreign = BotSupervisor.foreignBotPID() {
            state = .crashed(reason: "бот уже запущен вне приложения, pid \(foreign)")
            return
        }

        stopRequested = false
        state = .starting

        let task = Process()
        task.executableURL = cli
        task.arguments = ["bot"]
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let handle: FileHandle
        do {
            handle = try appendingHandle()
        } catch {
            state = .crashed(reason: "лог не открывается: \(error.localizedDescription)")
            return
        }
        // Один handle на оба потока: два независимых FileHandle на один файл делят
        // смещение так, что stdout и stderr затирают друг друга при чередовании записей.
        task.standardOutput = handle
        task.standardError = handle

        task.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.handleTermination(finished) }
        }

        do {
            try task.run()
        } catch {
            state = .crashed(reason: "не запускается: \(error.localizedDescription)")
            return
        }

        process = task
        let now = Date()
        startedAt = now
        state = .running(since: now)
    }

    func stop() {
        stopRequested = true
        process?.terminate()
        // process зануляем в handleTermination, а не здесь: пока ребёнок не умер
        // фактически, повторный start() должен видеть его и не поднимать второго
        // поллера того же токена.
        state = .stopped
    }

    private func handleTermination(_ finished: Process) {
        process = nil
        if stopRequested {
            state = .stopped
            return
        }

        let uptime = startedAt.map { Date().timeIntervalSince($0) } ?? 0
        tally.recordDeath(afterUptime: uptime)

        guard let delay = tally.nextDelay else {
            state = .crashed(reason: "упал \(tally.attempt) раза подряд, код \(finished.terminationStatus)")
            return
        }

        state = .crashed(reason: "упал, перезапуск через \(Int(delay)) с")
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.process == nil, !self.stopRequested else { return }
            self.start()
        }
    }

    /// Дозапись, а не перезапись: причина падения не должна теряться при перезапуске.
    private func appendingHandle() throws -> FileHandle {
        let manager = FileManager.default
        try manager.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        if !manager.fileExists(atPath: logURL.path) {
            manager.createFile(atPath: logURL.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        return handle
    }
}
