import Testing
@testable import ClaudeRCMenu

// XCTest недоступен на этой машине (см. CLILocatorTests.swift) — та же логика
// тестов и тот же набор случаев, оформленные через swift-testing.
@Suite struct BackoffTests {
    @Test func firstThreeAttemptsHaveGrowingDelays() {
        #expect(backoffDelay(attempt: 1) == 2)
        #expect(backoffDelay(attempt: 2) == 5)
        #expect(backoffDelay(attempt: 3) == 15)
    }

    @Test func givesUpAfterThirdAttempt() {
        // Бесконечная прокрутка падающего бота — тот же молчаливый сбой:
        // иконка мигает, а человек не понимает почему.
        #expect(backoffDelay(attempt: 4) == nil)
        #expect(backoffDelay(attempt: 10) == nil)
    }

    @Test func zeroAndNegativeAttemptsAreRejected() {
        #expect(backoffDelay(attempt: 0) == nil)
        #expect(backoffDelay(attempt: -1) == nil)
    }
}
