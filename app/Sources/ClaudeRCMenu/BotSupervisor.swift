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
    /// Конфига нет или он неполон — `doctor` ответил и сказал это прямо. Отдельный
    /// случай, а не `crashed`: крэш-луп из трёх попыток не сообщает причину и
    /// выглядит как поломка.
    case notConfigured
    /// `doctor` не запустился или не ответил за отведённое время — саму проверку
    /// провести не удалось. Отдельно от `notConfigured`: там доктор ответил и
    /// сказал прямо, что конфига нет, — там уместно предложение пройти визард.
    /// Здесь конфиг мог быть в полном порядке, а проблема в tmux/claude/машине —
    /// отправлять человека проходить визард заново означало бы врать о причине.
    case configurationCheckFailed(reason: String)
}

/// Результат проверки `doctor` — не голый `Bool`: «конфига нет» и «проверить не
/// удалось» смешивались в одно `false`, хотя это разные вещи и реагировать на них
/// нужно по-разному (см. `BotState.notConfigured` / `.configurationCheckFailed`).
enum ConfigurationCheck: Equatable {
    case configured
    case notConfigured
    case checkFailed(reason: String)
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
    /// Клик `Start bot`, пришедший, пока уже летит чья-то ещё проверка конфига
    /// (например, пассивная `recheckConfigurationIfNeeded` при открытии меню) —
    /// без этого флага такой клик просто терялся. Одноразовый: разбирается и
    /// сбрасывается ровно там, где эта летящая проверка завершается, — см.
    /// `dispatchConfigurationCheck`.
    private var pendingStart = false
    /// Внедряемая зависимость, а не прямой вызов `checkConfigured(cli:)`: тесты
    /// подменяют её синхронной заглушкой и гоняют реальные `start()`/`stop()`
    /// БЕЗ настоящего `doctor` и без спавна настоящего бота — второе всё равно
    /// не запустится, `cli` в тестах указывает в никуда.
    private let checkConfigured: @Sendable (URL) -> ConfigurationCheck
    /// Та же логика, что и у `checkConfigured`: без подмены тест на переход в
    /// `.running` недетерминирован на машине, где уже работает настоящий бот —
    /// реальный `pgrep` находит его первым, и продукт (правильно!) уходит в
    /// `.foreignBotRunning`, ни разу не дойдя до фактического запуска процесса.
    private let detectForeignBot: @MainActor () -> Int32?

    init(
        cli: URL, logURL: URL,
        checkConfigured: @escaping @Sendable (URL) -> ConfigurationCheck = BotSupervisor.checkConfigured,
        detectForeignBot: @escaping @MainActor () -> Int32? = BotSupervisor.foreignBotPID
    ) {
        self.cli = cli
        self.logURL = logURL
        self.checkConfigured = checkConfigured
        self.detectForeignBot = detectForeignBot
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
    nonisolated private static func checkConfigured(cli: URL) -> ConfigurationCheck {
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else {
            return .checkFailed(reason: "doctor не запустился")
        }
        guard exited.wait(timeout: .now() + 5) == .success else {
            task.terminate()
            return .checkFailed(reason: "doctor не ответил за 5с")
        }
        let checks = Doctor.parse(pipe.fileHandleForReading.readDataToEndOfFile())
        return Doctor.isConfigured(in: checks) ? .configured : .notConfigured
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
            // Проверка `doctor` уже летит — своя или чужая (например, пассивная
            // `recheckConfigurationIfNeeded` при открытии меню). Раньше клик тут
            // просто терялся: ни следа в логе, ни изменения на экране. Теперь —
            // видимая реакция (тот же `.starting`, что и у обычного запуска) и
            // намерение, которое доведёт до конца летящая проверка, когда ответит:
            // см. `dispatchConfigurationCheck`.
            pendingStart = true
            state = .starting
            Log.app("start: проверка конфига уже идёт, запомнили клик — доведём до конца, когда она ответит")
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
        dispatchConfigurationCheck { [weak self] result in
            self?.continueStart(result: result)
        }
    }

    /// Пассивная перепроверка при открытии меню в `.notConfigured` — человек мог
    /// пройти визард в соседнем Терминале и должен увидеть перемену, просто открыв
    /// меню, а не разбираться, почему кнопка бездействует, пока не кликнет её вслепую.
    ///
    /// В отличие от `start()`, она НЕ поднимает бота, даже если конфиг оказался в
    /// порядке, — только снимает `.notConfigured`, а решение «запускать или нет»
    /// по-прежнему за явным кликом `Start bot` (если только он не пришёл, пока эта
    /// проверка летела, — тогда `dispatchConfigurationCheck` доведёт его до конца
    /// сам). Тот же `isCheckingConfiguration`, что и у `start()`, — второй
    /// параллельный запрос к `doctor` не нужен ни там, ни здесь, поэтому флаг
    /// общий, а не заведён отдельно.
    func recheckConfigurationIfNeeded() {
        guard case .notConfigured = state else { return }
        guard !isCheckingConfiguration else { return }
        dispatchConfigurationCheck { [weak self] result in
            guard let self else { return }
            // `.notConfigured` — обычный случай, никто её не трогал. `.starting` —
            // клик `Start bot`, проглоченный ПОКА ЛЕТЕЛА ИМЕННО ЭТА проверка (см.
            // `start()`): раз мы вообще дошли до этого замыкания, а не до короткого
            // пути в `dispatchConfigurationCheck`, `pendingStart` уже разобран и
            // не был про «настроено» — эту видимую реакцию нужно снять таким же
            // честным состоянием, а не оставлять подвешенной навсегда. Любое
            // другое состояние — работа кого-то другого (например, настоящий
            // launch где-то ещё успел случиться), его не перетираем.
            guard self.state == .notConfigured || self.state == .starting else { return }
            switch result {
            case .configured:
                self.state = .stopped
            case .notConfigured, .checkFailed:
                // Пассивная перепроверка не должна пугать нежданной ошибкой —
                // `.checkFailed` тут не поднимаем до отдельного состояния, тот
                // же исход, что и «конфига по-прежнему нет»: следующее открытие
                // меню попробует снова.
                self.state = .notConfigured
            }
        }
    }

    /// Общий механизм постановки проверки `doctor` в фон с возвратом ответа на main
    /// actor — используется и в `start()`, и в пассивной перепроверке; `completion`
    /// вызывается уже после того, как `isCheckingConfiguration` сброшен обратно.
    private func dispatchConfigurationCheck(completion: @escaping @MainActor (ConfigurationCheck) -> Void) {
        isCheckingConfiguration = true
        let cli = self.cli
        let checkConfigured = self.checkConfigured
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = checkConfigured(cli)
            // `Task { @MainActor in }`, а не `DispatchQueue.main.async`: возврат на
            // main actor через структурированную конкурентность не завязан на то,
            // крутится ли где-то настоящий run loop главного потока — это же делает
            // переход воспроизводимым в тестах.
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.isCheckingConfiguration = false
                // Клик, запомненный в start() пока эта проверка летела: доводим до
                // конца именно её результатом, не спрашивая `doctor` заново. Если
                // результат не «настроено» — обычный `completion` его и покажет
                // (notConfigured/checkFailed), запуск сам собой не понадобится.
                if self.pendingStart {
                    self.pendingStart = false
                    if case .configured = result {
                        Log.app("start: конфиг нашёлся, доводим запомненный клик до конца")
                        self.continueStart(result: result)
                        return
                    }
                }
                completion(result)
            }
        }
    }

    /// Вторая половина `start()` после ответа `doctor`, снова на main actor.
    /// Спрашиваем про конфиг только когда своего процесса ещё нет: если бот уже
    /// жив, он с этим конфигом и поднялся, спрашивать незачем. А спросить и не
    /// дождаться ответа `doctor` за 5с означало бы объявить «не настроено» поверх
    /// работающего бота — меню соврало бы о том, что человек видит своими глазами.
    private func continueStart(result: ConfigurationCheck) {
        guard !stopRequested else {
            // Стоп нажали, пока `doctor` отвечал, — не поднимаем бота вопреки этому.
            state = .stopped
            return
        }
        switch result {
        case .configured:
            break
        case .notConfigured:
            Log.app("start: конфига нет, бота не поднимаем")
            state = .notConfigured
            return
        case .checkFailed(let reason):
            Log.app("start: проверка конфига не удалась (\(reason)), бота не поднимаем")
            state = .configurationCheckFailed(reason: reason)
            return
        }
        guard process == nil else {
            // Кто-то другой (например, таймер перезапуска после падения) успел
            // поднять бота, пока мы ждали `doctor`.
            return
        }
        if let foreign = detectForeignBot() {
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
        let current = detectForeignBot()
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
