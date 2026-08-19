import Foundation

enum BotState: Equatable {
    case stopped
    case starting
    case running(since: Date)
    case crashed(reason: String)
    /// Токен уже держит чужой процесс — не наш ребёнок. Отдельный случай, а не
    /// текст внутри `crashed`: меню должно уметь показать пункт восстановления
    /// без разбора строки причины.
    case foreignBotRunning(pid: Int32)
    /// Конфига нет или он неполон. Отдельный случай, а не `crashed`: крэш-луп
    /// из трёх попыток не сообщает причину и выглядит как поломка.
    case notConfigured
}

/// Показывать ли в меню пункт «забрать бота себе» — только когда рядом реально
/// работает чужой процесс: в любом другом состоянии забирать нечего.
func showsTakeOverRow(for state: BotState) -> Bool {
    if case .foreignBotRunning = state { return true }
    return false
}

/// Подпись пункта восстановления с pid — человеку нужно видеть, какой именно
/// процесс он гасит, прежде чем нажать. `nil` вне `foreignBotRunning`.
func takeOverRowTitle(for state: BotState) -> String? {
    guard case .foreignBotRunning(let pid) = state else { return nil }
    return "Take over bot (pid \(pid))"
}

/// Что делать в `takeOver()` после свежей проверки чужого pid.
enum TakeOverDecision: Equatable {
    /// Тот же процесс, что и был увиден, — гасим сигналом.
    case killThenStart(pid: Int32)
    /// Чужой процесс уже исчез сам — сигнал слать некому, просто стартуем.
    case startDirectly
    /// На месте запомненного pid теперь другой процесс — это не тот, кого мы
    /// собирались забрать; гасить его нельзя, только показать актуальный pid.
    case updateForeign(pid: Int32)
    /// pid ≤ 0: `kill` в этом случае ушёл бы не одному процессу, а целой группе.
    /// Через `pgrep` недостижимо (он не отдаёт 0 или отрицательные pid), но раз
    /// решение теперь разбирается тестом, а не только глазами в `takeOver()` —
    /// разбор обязан включать и этот случай.
    case refuseInvalidPID(pid: Int32)
}

/// Чистая логика решения takeOver — вынесена отдельно, чтобы её можно было
/// проверить таблицей истинности по всем комбинациям, не поднимая настоящие
/// процессы. `remembered` — pid из состояния `.foreignBotRunning`, `current` —
/// свежий результат `foreignBotPID()`.
func takeOverDecision(remembered pid: Int32, current: Int32?) -> TakeOverDecision {
    guard let current else { return .startDirectly }
    guard current == pid else { return .updateForeign(pid: current) }
    guard pid > 0 else { return .refuseInvalidPID(pid: pid) }
    return .killThenStart(pid: pid)
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
    /// Пока `true`, `start()` не выпускает второй параллельный запрос к `doctor`
    /// (см. её комментарий).
    private var isCheckingConfiguration = false
    /// Внедряемая зависимость, а не прямой вызов `checkConfigured(cli:)`: тесты
    /// подменяют её синхронной заглушкой и гоняют реальные `start()`/`stop()`
    /// БЕЗ настоящего `doctor` и без спавна настоящего бота — второе всё равно
    /// не запустится, `cli` в тестах указывает в никуда.
    private let checkConfigured: @Sendable (URL) -> Bool

    init(
        cli: URL, logURL: URL,
        checkConfigured: @escaping @Sendable (URL) -> Bool = BotSupervisor.checkConfigured
    ) {
        self.cli = cli
        self.logURL = logURL
        self.checkConfigured = checkConfigured
    }

    /// pid бота, запущенного мимо приложения. Два поллера одного токена получают
    /// от Telegram конфликт и работают через раз, поэтому свой мы не поднимаем.
    ///
    /// Шаблон заякорён — не просто "содержит подстроку" и не просто "заканчивается
    /// на подстроку": то и другое по отдельности пропускает чужой текст с той же
    /// фразой. `$` один не спасает — `pgrep -f "-f claude-rc bot"`, то есть
    /// собственная команда мониторинга, ЗАКАНЧИВАЕТСЯ ровно на "claude-rc bot", и
    /// голого `$` было достаточно, чтобы её поймать (проверено: `(^|[ /])claude-rc
    /// bot$` ловил такую подставу). Настоящий вызов бота имеет вид `.../bin/claude-rc
    /// bot` — перед именем исполняемого файла стоит `/`, а не пробел, поэтому левая
    /// граница сужена до начала строки или слэша: это отличает "исполняемый файл
    /// называется claude-rc" от "где-то в командной строке встретились эти слова".
    /// Легаси-путь `-m clauderc.bot` сужен так же — только как аргумент `-m`, не
    /// произвольная подстрока с точкой.
    static func foreignBotPID() -> Int32? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        // Точка в "clauderc.bot" экранирована: без экранирования она подошла бы
        // под любой символ.
        task.arguments = ["-fl", "(^|/)claude-rc bot$|-m clauderc\\.bot$"]
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
            // stderr — не канал, на который можно полагаться: у GUI-приложения,
            // запущенного через Finder/`open`, он идёт в /dev/null (см. Log.swift).
            // А это решение важное — таймаут значит «считаем, что чужого бота нет»
            // и ведёт прямиком к запуску второго поллера, — поэтому и в Log.app.
            let message = "claude-rc: pgrep не ответил за 2с, считаем что чужого бота нет"
            FileHandle.standardError.write(Data((message + "\n").utf8))
            Log.app(message)
            return nil
        }

        let output = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        ) ?? ""
        // На macOS "-l" в связке с "-f" — это не «имя процесса», а ПОЛНАЯ командная
        // строка (см. `man pgrep`: "If used in conjunction with -f, print the process
        // ID and the full argument list"). Раньше это шло прямиком в лог — а командная
        // строка произвольного процесса может содержать токен или пароль (`TOKEN=xxx
        // ./script`), и `ps`/`pgrep` покажут его целиком. Поэтому из вывода pgrep
        // берём только pid; безопасную для лога диагностику собирает
        // `diagnosticLine(forPID:)` отдельным запросом к `ps`.
        let match = output
            .split(separator: "\n")
            .compactMap { line -> Int32? in
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                let firstToken = trimmed.split(separator: " ", maxSplits: 1).first ?? trimmed[...]
                return Int32(firstToken)
            }
            .first { $0 != ProcessInfo.processInfo.processIdentifier }

        if let match {
            Log.app("foreignBotPID: matched \(diagnosticLine(forPID: match))")
        }
        return match
    }

    /// Безопасная для лога диагностика о процессе: pid, ppid и **только имя**
    /// исполняемого файла (`ps -o comm=` на macOS отдаёт полный путь — берём его
    /// последний компонент). Аргументов командной строки здесь намеренно нет: они
    /// могут содержать секреты (см. комментарий в `foreignBotPID`), а pid/ppid/имя
    /// уже достаточно, чтобы при ложном срабатывании понять, что за процесс приняли
    /// за бота, — не поднимая читаемость лога до "виден любой чужой пароль".
    private static func diagnosticLine(forPID pid: Int32) -> String {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/ps")
        task.arguments = ["-o", "pid=,ppid=,comm=", "-p", String(pid)]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return "pid \(pid)" }

        guard exited.wait(timeout: .now() + 2) == .success else {
            task.terminate()
            return "pid \(pid) (ps не ответил за 2с)"
        }

        let output = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let fields = output.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
        guard fields.count == 3, let ppid = Int32(fields[1]) else { return "pid \(pid)" }
        let name = URL(fileURLWithPath: String(fields[2])).lastPathComponent
        return "pid \(pid) ppid \(ppid) comm \(name)"
    }

    /// Спрашивает `claude-rc doctor --json`. `nonisolated static`, а не метод
    /// экземпляра: `start()` зовёт её с фонового потока (см. ниже), а `Process` и
    /// `DispatchSemaphore.wait` под main actor изолировать нельзя — она не трогает
    /// состояние `self`. Таймаут 5с — как у остальных внешних вызовов, но здесь
    /// именно поэтому он и не блокирует главный поток, в отличие от `foreignBotPID`.
    nonisolated private static func checkConfigured(cli: URL) -> Bool {
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return false }
        guard exited.wait(timeout: .now() + 5) == .success else {
            task.terminate()
            Log.app("isConfigured: doctor не ответил за 5с")
            return false
        }
        return Doctor.isConfigured(in: Doctor.parse(pipe.fileHandleForReading.readDataToEndOfFile()))
    }

    func start() {
        guard process == nil else {
            // `stop()` намеренно не зануляет `process` сразу (см. её комментарий) —
            // окно между «стоп нажали» и `handleTermination` миллисекундное, но
            // повторный клик `Start bot` в это окно не должен молчать: раньше
            // здесь не оставалось ни следа в логе, ни изменения состояния.
            Log.app("start: бот уже поднят (pid \(process?.processIdentifier ?? -1)), повторный запуск игнорируем")
            return
        }
        guard !isCheckingConfiguration else {
            // Проверка `doctor` уже летит (предыдущий вызов `start()`) — вторая
            // параллельно ничего не ускорит, а два ответа могут прийти в любом
            // порядке и перезаписать состояние друг за другом.
            Log.app("start: проверка конфига уже идёт, повторный запуск игнорируем")
            return
        }
        // Сброс здесь, а не в continueStart перед фактическим запуском: тот guard
        // ниже по `stopRequested` должен ловить только стоп, нажатый во время ЭТОЙ
        // проверки. Раньше сброс жил только на пути реального запуска процесса —
        // любой прошлый `stop()` навсегда взводил флаг, и следующий, никак с тем
        // стопом не связанный `start()` доходил до `continueStart`, видел его и
        // молча оседал в `.stopped` без единой попытки и без лога.
        stopRequested = false
        // Проверку показываем как starting, а не молчим пять секунд: иначе клик по
        // кнопке выглядит так, будто ничего не произошло.
        state = .starting
        isCheckingConfiguration = true
        let cli = self.cli
        let checkConfigured = self.checkConfigured
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let configured = checkConfigured(cli)
            // `Task { @MainActor in }`, а не `DispatchQueue.main.async`: возврат на
            // main actor через структурированную конкурентность не завязан на то,
            // крутится ли где-то настоящий run loop главного потока — это же делает
            // переход воспроизводимым в тестах.
            Task { @MainActor [weak self] in
                self?.continueStart(configured: configured)
            }
        }
    }

    /// Вторая половина `start()` после ответа `doctor`, снова на main actor.
    /// Спрашиваем про конфиг только когда своего процесса ещё нет: если бот уже
    /// жив, он с этим конфигом и поднялся, спрашивать незачем. А спросить и не
    /// дождаться ответа `doctor` за 5с означало бы объявить «не настроено» поверх
    /// работающего бота — меню соврало бы о том, что человек видит своими глазами.
    private func continueStart(configured: Bool) {
        isCheckingConfiguration = false
        guard !stopRequested else {
            // Стоп нажали, пока `doctor` отвечал, — не поднимаем бота вопреки этому.
            state = .stopped
            return
        }
        guard configured else {
            Log.app("start: конфига нет, бота не поднимаем")
            state = .notConfigured
            return
        }
        guard process == nil else {
            // Кто-то другой (например, таймер перезапуска после падения) успел
            // поднять бота, пока мы ждали `doctor`.
            return
        }
        if let foreign = BotSupervisor.foreignBotPID() {
            state = .foreignBotRunning(pid: foreign)
            return
        }

        // `stopRequested` уже false — сброшен в start() перед этой проверкой, и
        // ранний guard выше вернул бы нас раньше, если бы стоп пришёл во время неё.
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
        // Сам handle открыт через Log.openAppending с O_APPEND — см. его комментарий:
        // без этого флага и записи приложения (Log.app) в тот же файл затирались бы.
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

    /// Человек явно решил забрать бота у чужого процесса — это единственный способ
    /// его погасить. При старте мы этого не делаем сами: чужой процесс мог быть
    /// запущен намеренно, и гасить его без спроса нельзя (см. диалог доверия
    /// каталогу в CLI — тот же принцип).
    ///
    /// `pid` в состоянии запомнен с момента, когда `foreignBotPID()` его увидел —
    /// это могло быть сколько угодно давно, `menuNeedsUpdate` состояние не
    /// перепроверяет. macOS переиспользует номера процессов по кругу: слепой
    /// `kill(pid, …)` рискует попасть не в тот процесс, а в того, кто успел занять
    /// этот номер с тех пор, — на этой машине с равной вероятностью это `claude`
    /// внутри tmux-панели. Поэтому перед сигналом опрашиваем `foreignBotPID()`
    /// заново и гасим только если это буквально тот же pid.
    func takeOver() {
        guard case .foreignBotRunning(let pid) = state else { return }
        let current = BotSupervisor.foreignBotPID()
        switch takeOverDecision(remembered: pid, current: current) {
        case .killThenStart(let pid):
            Log.app("takeOver: SIGTERM чужому pid \(pid)")
            kill(pid, SIGTERM)
            state = .starting
            waitForForeignExit(pid: pid, attempt: 0)
        case .refuseInvalidPID(let pid):
            Log.app("takeOver: pid \(pid) <= 0, сигнал не шлём")
            state = .stopped
        case .updateForeign(let newPID):
            Log.app("takeOver: запомненный pid \(pid) больше не тот процесс (сейчас \(newPID)), сигнал не шлём")
            state = .foreignBotRunning(pid: newPID)
        case .startDirectly:
            Log.app("takeOver: чужой pid \(pid) уже исчез сам, стартуем без сигнала")
            state = .stopped
            start()
        }
    }

    /// SIGTERM не убивает мгновенно — если запустить своего бота раньше, чем чужой
    /// правда исчез, получим двух поллеров одного токена. `kill(pid, 0)` сигнала не
    /// шлёт, только проверяет, жив ли pid (ESRCH — нет).
    ///
    /// Стоп в начале, а не только в конце: `stop()` во время ожидания ставит
    /// `stopRequested` и `state = .stopped`, но сам таймер об этом не знал — он
    /// либо звал `start()` по факту смерти чужого (тот сам сбрасывает
    /// `stopRequested`, и клик «Стоп» проигрывал), либо через 3 с перетирал
    /// состояние обратно на `.foreignBotRunning`. Проверка нужна на каждом шаге
    /// рекурсии, а не только при входе, — `stop()` могли нажать в любой момент
    /// ожидания.
    private func waitForForeignExit(pid: Int32, attempt: Int) {
        guard !stopRequested else {
            state = .stopped
            return
        }
        guard kill(pid, 0) == 0 else {
            start()
            return
        }
        guard attempt < 10 else {
            Log.app("takeOver: чужой pid \(pid) всё ещё жив после SIGTERM, сдаёмся")
            state = .foreignBotRunning(pid: pid)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.waitForForeignExit(pid: pid, attempt: attempt + 1)
        }
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
    /// Создание каталога/файла и ужатие прав (0700/0600) — в одном месте,
    /// `Log.openAppending`, чтобы правило было одно на всех писателей (см. её
    /// комментарий и M-3 в финальном ревью).
    private func appendingHandle() throws -> FileHandle {
        try Log.openAppending(at: logURL)
    }
}
