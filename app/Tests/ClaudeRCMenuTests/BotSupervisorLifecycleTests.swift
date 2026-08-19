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
/// Проверка конфигурации подменяется синхронной заглушкой через внедряемую зависимость
/// `checkConfigured` — настоящий `doctor` не зовём. `cli` в тестах указывает в никуда:
/// если проверка чужого бота (`foreignBotPID`, настоящий `pgrep`, только читает список
/// процессов) никого не находит, попытка реального запуска процесса синхронно падает
/// (`.crashed`) прежде, чем что-либо успевает запуститься; если на машине уже есть живой
/// бот — вместо этого честно вернётся `.foreignBotRunning`. Оба исхода одинаково
/// доказывают, что мы прошли `continueStart`, не застряв в `.stopped`, — только это тесты
/// и проверяют.
@MainActor
@Suite struct BotSupervisorLifecycleTests {
    @Test func startAfterStopReachesAnAttempt() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in true })

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

    @Test func stopDuringConfigurationCheckPreventsStart() async throws {
        let release = DispatchSemaphore(value: 0)
        let supervisor = makeSupervisor(checkConfigured: { _ in
            release.wait()
            return true
        })

        supervisor.start()
        supervisor.stop()
        #expect(supervisor.state == .stopped)

        release.signal()
        try await settle(supervisor)
        #expect(supervisor.state == .stopped)
    }

    @Test func configurationCheckFailureGivesNotConfigured() async throws {
        let supervisor = makeSupervisor(checkConfigured: { _ in false })

        supervisor.start()
        try await settle(supervisor)
        #expect(isNotConfigured(supervisor.state))
    }

    @Test func repeatedStartDuringCheckDoesNotDoubleCheck() async throws {
        let counter = CallCounter()
        let release = DispatchSemaphore(value: 0)
        let supervisor = makeSupervisor(checkConfigured: { _ in
            counter.increment()
            release.wait()
            return true
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
}

@MainActor
private func makeSupervisor(checkConfigured: @escaping @Sendable (URL) -> Bool) -> BotSupervisor {
    let logURL = FileManager.default.temporaryDirectory
        .appendingPathComponent("claude-rc-test-\(UUID().uuidString)")
        .appendingPathComponent("claude-rc.log")
    return BotSupervisor(
        // Путь заведомо не существует: реальный запуск (`task.run()`) синхронно
        // падает в `.crashed`, не порождая ни одного настоящего процесса.
        cli: URL(fileURLWithPath: "/nonexistent/claude-rc-test-cli"),
        logURL: logURL,
        checkConfigured: checkConfigured
    )
}

/// `.starting` — переходное состояние на время фоновой проверки; ждём, пока
/// `continueStart` (уже на main actor) решит, во что она превратится. `await
/// Task.sleep` — настоящая точка приостановки: не блокирует main actor и даёт
/// доехать до него `Task { @MainActor in }` из `start()`.
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

    var value: Int {
        lock.lock()
        defer { lock.unlock() }
        return count
    }
}
