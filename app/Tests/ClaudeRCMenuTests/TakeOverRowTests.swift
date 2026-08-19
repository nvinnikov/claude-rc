import Foundation
import Testing
@testable import ClaudeRCMenu

@Suite struct TakeOverRowTests {
    @Test func hiddenInEveryStateExceptForeignBotRunning() {
        #expect(showsTakeOverRow(for: .stopped) == false)
        #expect(showsTakeOverRow(for: .starting) == false)
        #expect(showsTakeOverRow(for: .running(since: Date())) == false)
        #expect(showsTakeOverRow(for: .crashed(reason: "упал")) == false)
        #expect(showsTakeOverRow(for: .foreignBotRunning(pid: 123)) == true)
    }

    @Test func titleCarriesThePIDAndIsNilElsewhere() {
        #expect(takeOverRowTitle(for: .foreignBotRunning(pid: 80464)) == "Take over bot (pid 80464)")
        #expect(takeOverRowTitle(for: .stopped) == nil)
        #expect(takeOverRowTitle(for: .crashed(reason: "упал")) == nil)
    }
}
