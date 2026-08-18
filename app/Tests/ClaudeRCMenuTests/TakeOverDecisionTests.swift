import Testing
@testable import ClaudeRCMenu

/// Таблица истинности по I-1 из финального ревью: `takeOver()` шлёт SIGTERM
/// только по pid, буквально подтверждённому свежим опросом, — иначе macOS могла
/// успеть переиспользовать номер под другой процесс.
@Suite struct TakeOverDecisionTests {
    @Test func samePIDStillForeignKillsIt() {
        #expect(takeOverDecision(remembered: 123, current: 123) == .killThenStart(pid: 123))
    }

    @Test func foreignExitedOnItsOwnStartsWithoutSignal() {
        #expect(takeOverDecision(remembered: 123, current: nil) == .startDirectly)
    }

    @Test func pidReusedByAnotherProcessIsNeverKilled() {
        #expect(takeOverDecision(remembered: 123, current: 456) == .updateForeign(pid: 456))
    }
}
