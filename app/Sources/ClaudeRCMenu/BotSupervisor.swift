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

/// Владеет процессом бота: запускает, гасит, переживает его падения.
///
/// Состояние «жив» — это `Process.isRunning`, а не догадка по сокету: бот запущен
/// нами, и другого источника правды не нужно.
///
/// `@unchecked Sendable`: вся мутация состояния идёт через `DispatchQueue.main`
/// (терминатор процесса и есть точка серилизации), строгую проверку компилятора
/// это не отражает.
final class BotSupervisor: @unchecked Sendable {
    var onStateChange: ((BotState) -> Void)?
    private(set) var state: BotState = .stopped {
        didSet { onStateChange?(state) }
    }

    private let cli: URL
    private let logURL: URL
    private var process: Process?
    private var stopRequested = false
    private var restartAttempt = 0

    init(cli: URL, logURL: URL) {
        self.cli = cli
        self.logURL = logURL
    }

    /// pid бота, запущенного мимо приложения. Два поллера одного токена получают
    /// от Telegram конфликт и работают через раз, поэтому свой мы не поднимаем.
    static func foreignBotPID(excluding ownPID: Int32?) -> Int32? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        task.arguments = ["-f", "claude-rc bot|clauderc.bot"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        guard (try? task.run()) != nil else { return nil }
        task.waitUntilExit()

        let output = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        ) ?? ""
        return output
            .split(separator: "\n")
            .compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }
            .first { $0 != ownPID && $0 != ProcessInfo.processInfo.processIdentifier }
    }

    func start() {
        guard process == nil else { return }
        if let foreign = BotSupervisor.foreignBotPID(excluding: process?.processIdentifier) {
            state = .crashed(reason: "бот уже запущен вне приложения, pid \(foreign)")
            return
        }

        stopRequested = false
        state = .starting

        let task = Process()
        task.executableURL = cli
        task.arguments = ["bot"]
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        do {
            task.standardOutput = try appendingHandle()
            task.standardError = try appendingHandle()
        } catch {
            state = .crashed(reason: "лог не открывается: \(error.localizedDescription)")
            return
        }

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
        restartAttempt = 0
        state = .running(since: Date())
    }

    func stop() {
        stopRequested = true
        process?.terminate()
        process = nil
        state = .stopped
    }

    private func handleTermination(_ finished: Process) {
        process = nil
        if stopRequested {
            state = .stopped
            return
        }

        restartAttempt += 1
        guard let delay = backoffDelay(attempt: restartAttempt) else {
            state = .crashed(reason: "упал \(restartAttempt) раза подряд, код \(finished.terminationStatus)")
            return
        }

        state = .crashed(reason: "упал, перезапуск через \(Int(delay)) с")
        let attempt = restartAttempt
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.process == nil, !self.stopRequested else { return }
            self.start()
            self.restartAttempt = attempt  // start() обнуляет счётчик, а серию надо помнить
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
