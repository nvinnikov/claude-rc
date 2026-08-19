import Foundation
import Testing

@testable import ClaudeRCMenu

/// Регресс из финального ревью: `stopRequested` сбрасывался в `false` только на пути
/// фактического запуска процесса, глубоко внутри `continueStart`. Guard по этому флагу
/// в начале `continueStart` (его завели, чтобы стоп во время проверки конфигурации не
/// давал боту подняться) ловил не только СВОЙ стоп, но и любой прошлый: после первого
/// же `stop()` флаг оставался `true` навсегда, и следующий, никак с тем стопом не
/// связанный `start()` доходил до `continueStart`, видел протухший флаг и молча оседал
/// в `.stopped` — ни лога, ни попытки запуска. Лечилось только перезапуском приложения.
///
/// `checkConfigured` и `detectForeignBot` подменяются синхронными заглушками через
/// внедряемые зависимости — настоящий `doctor` и настоящий `pgrep` не зовём, поведение
/// не зависит от того, работает ли на машине живой бот. `cli` в тестах, где процесс
/// запускаться не должен, указывает в никуда: реальный запуск (`task.run()`) синхронно
/// падает в `.crashed`, не порождая ни одного настоящего процесса. Там, где важен именно
/// переход в `.running`, `cli` — временный скрипт-заглушка (`sleep`), а не настоящий бот.
@MainActor
@Suite struct BotSupervisorLifecycleTests {
    @Test func startAfterStopReachesAnAttempt() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in .configured })

        supervisor.start()
        try await settle(supervisor)
        #expect(supervisor.state != .stopped)

        supervisor.stop()
        #expect(supervisor.state == .stopped)

        // Регресс: без починки stopRequested остаётся true после stop() выше,
        // и этот второй, никак с тем стопом не связанный start() молча оседает
        // в .stopped, хотя ничего не мешает попытке.
        supervisor.start()
        try await settle(supervisor)
        #expect(supervisor.state != .stopped)
    }

    /// Прежняя версия этого теста проверяла состояние сразу после `release.signal()`,
    /// когда `state` уже было `.stopped` (от ручного `stop()`) и просто НЕ УСПЕВАЛО
    /// измениться обратно на что-то другое — `settle()` выходил первой же итерацией,
    /// не дождавшись, чтобы `continueStart` вообще исполнился. Тест был бы зелёным и
    /// без guard'а по `stopRequested` внутри `continueStart`: он не проверял ничего.
    /// Здесь вместо этого считаем сами переходы `state` через `TransitionRecorder` и
    /// явно ждём ТРЕТИЙ (тот, что делает — или не делает — сам guard), а не полагаемся
    /// на совпадение итогового значения с тем, что было ещё до завершения проверки.
    @Test func stopDuringConfigurationCheckPreventsStart() async throws {
        let release = DispatchSemaphore(value: 0)
        let supervisor = makeSupervisor(checkConfigured: { _ in
            release.wait()
            return .configured
        })
        let recorder = TransitionRecorder()
        recorder.attach(to: supervisor)

        supervisor.start()
        try await recorder.wait(forCount: 1)
        #expect(recorder.states == [.starting])

        supervisor.stop()
        #expect(recorder.states == [.starting, .stopped])
        #expect(supervisor.state == .stopped)

        release.signal()
        // Третий переход — это то, что решает guard внутри continueStart. Без него
        // (или если бы guard был снят) этот вызов либо никогда не дождался бы своего
        // элемента и упал по таймауту с сообщением, либо дождался бы перехода в
        // .foreignBotRunning/.crashed — и тогда упала бы проверка ниже.
        try await recorder.wait(forCount: 3)
        #expect(supervisor.state == .stopped)
        #expect(recorder.states == [.starting, .stopped, .stopped])
    }

    @Test func configurationCheckFailureGivesNotConfigured() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in .notConfigured })

        supervisor.start()
        try await settle(supervisor)
        #expect(isNotConfigured(supervisor.state))
    }

    /// Ревью PR: `checkConfigured == false` смешивало «доктор ответил — конфига нет»
    /// и «доктор не поднялся / не ответил вовремя» в один и тот же исход — а это
    /// разные вещи. Второе не должно отправлять человека проходить визард заново:
    /// конфиг мог быть в полном порядке, проблема в tmux/claude/машине.
    @Test func checkFailedGivesConfigurationCheckFailedNotNotConfigured() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in .checkFailed(reason: "doctor не запустился") })

        supervisor.start()
        try await settle(supervisor)

        guard case .configurationCheckFailed(let reason) = supervisor.state else {
            Issue.record("ожидали .configurationCheckFailed, получили \(supervisor.state)")
            return
        }
        #expect(reason == "doctor не запустился")
        // Не .notConfigured — иначе меню снова предложило бы визард без всякой пользы.
        #expect(!isNotConfigured(supervisor.state))
    }

    @Test func repeatedStartDuringCheckDoesNotDoubleCheck() async throws {
        let counter = CallCounter()
        let release = DispatchSemaphore(value: 0)
        let supervisor = makeSupervisor(checkConfigured: { _ in
            counter.increment()
            release.wait()
            return .configured
        })

        supervisor.start()
        // Второй клик, пока первая проверка ещё летит: isCheckingConfiguration
        // должен молча его проглотить — оба вызова синхронны и идут подряд без
        // точки приостановки, гонки здесь нет.
        supervisor.start()

        release.signal()
        try await settle(supervisor)
        #expect(counter.value == 1)
    }

    /// Ранее не проверялось вообще: у `.running` не было ни одного теста. `detectForeignBot`
    /// подменён на `{ nil }` — иначе на машине с уже работающим ботом реальный `pgrep`
    /// нашёл бы его первым, и `continueStart` честно (и правильно) ушёл бы в
    /// `.foreignBotRunning`, ни разу не дойдя до фактического запуска процесса.
    @Test func startReachesRunningWhenLaunchSucceeds() async throws {
        let script = try makeFakeBotScript()
        defer { try? FileManager.default.removeItem(at: script) }
        let supervisor = makeSupervisor(cli: script, checkConfigured: { _ in .configured }, detectForeignBot: { nil })

        supervisor.start()
        try await settle(supervisor)

        guard case .running = supervisor.state else {
            Issue.record("ожидали .running, получили \(supervisor.state)")
            return
        }
        supervisor.stop()
    }

    /// `handleTermination` и таймер перезапуска относительно `stopRequested`: стоп
    /// живого бота не должен читаться как падение и планировать перезапуск. Этот же
    /// флаг двигал регрессию выше, поэтому граница «свой стоп vs настоящее падение»
    /// здесь проверяется отдельно, на реальном (хоть и безобидном) дочернем процессе —
    /// без него `handleTermination` никогда не позвался бы по-настоящему.
    @Test func stopWhileRunningDoesNotScheduleRestart() async throws {
        let script = try makeFakeBotScript(duration: 5)
        defer { try? FileManager.default.removeItem(at: script) }
        let supervisor = makeSupervisor(cli: script, checkConfigured: { _ in .configured }, detectForeignBot: { nil })
        let recorder = TransitionRecorder()
        recorder.attach(to: supervisor)

        supervisor.start()
        // .starting (start) → .starting (повтор в continueStart) → .running.
        try await recorder.wait(forCount: 3)
        guard case .running = supervisor.state else {
            Issue.record("ожидали .running перед stop(), получили \(supervisor.state)")
            return
        }

        supervisor.stop()
        // 4-й переход — сам stop() (.stopped); 5-й — handleTermination настоящего
        // процесса, получившего SIGTERM. Если бы stopRequested не удержался к этому
        // моменту, handleTermination принял бы смерть процесса за настоящее падение
        // и получили бы .crashed вместо повторного .stopped.
        try await recorder.wait(forCount: 5)
        #expect(supervisor.state == .stopped)
        #expect(recorder.states.suffix(2) == [.stopped, .stopped])

        // Самый быстрый backoff — 2с (см. backoffDelay(attempt: 1)). Если бы
        // handleTermination всё-таки запланировал перезапуск, он проявился бы новым
        // переходом здесь; отсутствие перехода — доказательство, что таймер не заведён.
        try await Task.sleep(nanoseconds: 2_500_000_000)
        #expect(recorder.states.count == 5)
    }

    /// Регресс из ревью PR: `.notConfigured` был тупиком — кнопка недоступна, а
    /// само состояние никогда не пересчитывалось, и единственным выходом был
    /// перезапуск приложения. `flag` имитирует человека, прошедшего визард в
    /// соседнем Терминале уже ПОСЛЕ того, как приложение однажды увидело
    /// `.notConfigured`, — ровно тот сценарий, ради которого весь этот пункт
    /// меню и делался.
    @Test func recheckClearsNotConfiguredWhenNowConfigured() async throws {
        let flag = ConfiguredFlag(false)
        let supervisor = makeSupervisor(checkConfigured: { _ in flag.value ? .configured : .notConfigured })

        supervisor.start()
        try await settle(supervisor)
        #expect(isNotConfigured(supervisor.state))

        flag.value = true
        let recorder = TransitionRecorder()
        recorder.attach(to: supervisor)

        supervisor.recheckConfigurationIfNeeded()
        try await recorder.wait(forCount: 1)
        // Именно .stopped, а не что-то ещё: перепроверка обязана только снять
        // .notConfigured, а не сама поднять бота — запуск по-прежнему за явным
        // кликом Start bot (кроме случая с запомненным кликом — см. тесты ниже).
        #expect(supervisor.state == .stopped)
    }

    @Test func recheckLeavesNotConfiguredWhenStillNotConfigured() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in .notConfigured })

        supervisor.start()
        try await settle(supervisor)
        #expect(isNotConfigured(supervisor.state))

        let recorder = TransitionRecorder()
        recorder.attach(to: supervisor)
        supervisor.recheckConfigurationIfNeeded()

        // Перепроверка честно переустанавливает .notConfigured (см. её комментарий —
        // тем же путём снимается .starting от возможного проглоченного клика), так
        // что переход происходит, просто ведёт туда же, откуда начали.
        try await recorder.wait(forCount: 1)
        #expect(isNotConfigured(supervisor.state))
    }

    /// Ревью PR: клик `Start bot`, пришедший пока летит пассивная перепроверка
    /// (заводится при КАЖДОМ открытии меню в `.notConfigured` — то есть попасть в
    /// это окно человеку легко: открыл меню, сразу нажал), терялся молча — ни следа
    /// в логе, ни изменения на экране. Три вещи проверяются здесь по отдельности.
    ///
    /// Во всех трёх `checkConfigured` разводит две фазы через `blocking`: сперва
    /// нужен обычный синхронный `.notConfigured`, чтобы дойти до этого состояния
    /// через настоящий `start()` (там `state` идёт через `.starting`, и `settle()`
    /// это отследит); затем — управляемая семафором фаза для собственно проверки.
    /// Один и тот же блокирующийся с самого начала стаб уже один раз подводил
    /// (см. `startAfterStopReachesAnAttempt` выше по истории правок) — первый же
    /// `start()` навсегда виснул на `release.wait()`, которого некому было снять.
    @MainActor
    @Suite struct StartDuringPassiveRecheck {
        /// 1. Видимая реакция — немедленно, синхронно, до того как сама проверка
        /// вообще успела куда-то сходить.
        @Test func showsVisibleReactionImmediately() async throws {
            let release = DispatchSemaphore(value: 0)
            let blocking = ConfiguredFlag(false)
            let supervisor = makeSupervisor(checkConfigured: { _ in
                guard blocking.value else { return .notConfigured }
                release.wait()
                return .configured
            })

            supervisor.start()
            try await settle(supervisor)
            #expect(isNotConfigured(supervisor.state))

            blocking.value = true
            supervisor.recheckConfigurationIfNeeded()
            supervisor.start()
            // Синхронно, без await: обе строчки — main-actor вызовы подряд без
            // точки приостановки между ними.
            #expect(supervisor.state == .starting)

            release.signal()
            try await settle(supervisor)
        }

        /// 2. Намерение выполняется — если перепроверка закончилась успехом, клик
        /// не просто разблокировал кнопку, а довёл дело до попытки запуска.
        @Test func isFulfilledWhenCheckSucceeds() async throws {
            let counter = CallCounter()
            let release = DispatchSemaphore(value: 0)
            let blocking = ConfiguredFlag(false)
            let supervisor = makeSupervisor(checkConfigured: { _ in
                counter.increment()
                guard blocking.value else { return .notConfigured }
                release.wait()
                return .configured
            })

            supervisor.start()
            try await settle(supervisor)
            #expect(isNotConfigured(supervisor.state))

            counter.reset()
            blocking.value = true
            supervisor.recheckConfigurationIfNeeded()
            supervisor.start()
            // Один запрос к doctor на оба вызова — второй не должен его удвоить.
            #expect(counter.value == 1)

            release.signal()
            try await settle(supervisor)
            // .stopped означало бы, что перепроверка просто сняла .notConfigured
            // и остановилась, — а запомненный клик обязан довести до попытки
            // запуска (тот же результат, что доказывает startAfterStopReachesAnAttempt).
            #expect(supervisor.state != .stopped)
            #expect(!isNotConfigured(supervisor.state))
        }

        /// 3. Намерение не выполняется вслепую — если перепроверка так и не нашла
        /// конфиг, бот не запускается, а видимая реакция (.starting) не подвисает
        /// навсегда: состояние честно возвращается в .notConfigured.
        @Test func isNotFulfilledWhenCheckStaysNotConfigured() async throws {
            let release = DispatchSemaphore(value: 0)
            let blocking = ConfiguredFlag(false)
            let supervisor = makeSupervisor(checkConfigured: { _ in
                guard blocking.value else { return .notConfigured }
                release.wait()
                return .notConfigured
            })

            supervisor.start()
            try await settle(supervisor)
            #expect(isNotConfigured(supervisor.state))

            blocking.value = true
            supervisor.recheckConfigurationIfNeeded()
            supervisor.start()
            #expect(supervisor.state == .starting)

            release.signal()
            try await settle(supervisor)
            #expect(isNotConfigured(supervisor.state))
        }
    }
}

@MainActor
private func makeSupervisor(
    cli: URL = URL(fileURLWithPath: "/nonexistent/claude-rc-test-cli"),
    checkConfigured: @escaping @Sendable (URL) -> ConfigurationCheck,
    detectForeignBot: @escaping @MainActor () -> Int32? = { nil }
) -> BotSupervisor {
    let logURL = FileManager.default.temporaryDirectory
        .appendingPathComponent("claude-rc-test-\(UUID().uuidString)")
        .appendingPathComponent("claude-rc.log")
    return BotSupervisor(
        cli: cli,
        logURL: logURL,
        checkConfigured: checkConfigured,
        detectForeignBot: detectForeignBot
    )
}

/// Скрипт-заглушка вместо настоящего бота: просто спит `duration` секунд. Safe
/// stand-in, чтобы проверить переход в `.running` и его дальнейшую судьбу, не трогая
/// ни tmux, ни Telegram, ни реального бота, который может уже работать на машине.
@MainActor
private func makeFakeBotScript(duration: Int = 5) throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("claude-rc-test-bot-\(UUID().uuidString)")
    try "#!/bin/sh\nsleep \(duration)\n".write(to: url, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
    return url
}

/// `.starting` — переходное состояние на время фоновой проверки; ждём, пока
/// `continueStart` (уже на main actor) решит, во что она превратится. Годится только
/// там, где итоговое состояние заведомо отличается от того, что было ДО запуска
/// проверки, — иначе (см. `TransitionRecorder`) можно выйти раньше, чем проверка
/// реально отработала. `await Task.sleep` — настоящая точка приостановки: не
/// блокирует main actor и даёт доехать до него `Task { @MainActor in }` из `start()`.
@MainActor
private func settle(_ supervisor: BotSupervisor, timeout: TimeInterval = 3) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if case .starting = supervisor.state {
            try await Task.sleep(nanoseconds: 5_000_000)
            continue
        }
        return
    }
    // Раньше здесь молча возвращали управление, и `!= .stopped` после этого мог
    // пройти на застрявшем `.starting` — помощник делал вид, что всё хорошо, хотя
    // ничего не дождался. Явная ошибка вместо тихого успеха.
    throw SettleTimeoutError(state: supervisor.state, timeout: timeout)
}

private struct SettleTimeoutError: Error, CustomStringConvertible {
    let state: BotState
    let timeout: TimeInterval
    var description: String {
        "не устоялось за \(timeout)с, застряло на \(state)"
    }
}

/// Записывает переходы `state` по порядку и умеет дожидаться N-го из них — в отличие
/// от `settle()`, не полагается на то, что итоговое значение отличается от исходного.
/// Нужен там, где ожидаемый результат (например, повторный `.stopped`) может СОВПАСТЬ
/// со значением, которое было ещё до того, как проверяемая логика вообще отработала.
@MainActor
private final class TransitionRecorder {
    private(set) var states: [BotState] = []

    func attach(to supervisor: BotSupervisor) {
        supervisor.onStateChange = { [weak self] state in self?.states.append(state) }
    }

    /// Бросает по таймауту с тем, что успело накопиться, — а не молча отдаёт
    /// управление дальше с недостоверным состоянием.
    func wait(forCount count: Int, timeout: TimeInterval = 3) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while states.count < count {
            guard Date() < deadline else {
                throw RecorderTimeoutError(expected: count, states: states, timeout: timeout)
            }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
    }
}

private struct RecorderTimeoutError: Error, CustomStringConvertible {
    let expected: Int
    let states: [BotState]
    let timeout: TimeInterval
    var description: String {
        "не дождались \(expected)-го перехода состояния за \(timeout)с; накоплено: \(states)"
    }
}

/// Считает вызовы `checkConfigured` через границу потоков — заглушка зовётся с
/// `DispatchQueue.global`, обычный `var` для этого не потокобезопасен.
private final class CallCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    func increment() {
        lock.lock()
        count += 1
        lock.unlock()
    }

    func reset() {
        lock.lock()
        count = 0
        lock.unlock()
    }

    var value: Int {
        lock.lock()
        defer { lock.unlock() }
        return count
    }
}

/// Переключаемая заглушка `checkConfigured` — имитирует человека, прошедшего визард
/// в соседнем окне уже после того, как приложение увидело `.notConfigured`. Обычный
/// `var` не годится: заглушка читает значение с `DispatchQueue.global`.
private final class ConfiguredFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Bool

    init(_ value: Bool) { stored = value }

    var value: Bool {
        get {
            lock.lock()
            defer { lock.unlock() }
            return stored
        }
        set {
            lock.lock()
            stored = newValue
            lock.unlock()
        }
    }
}
