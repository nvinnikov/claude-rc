import Testing
@testable import ClaudeRCMenu

/// I-3 из финального ревью: второй экземпляр (прямой запуск бинаря рядом с уже
/// открытым .app) не должен решить, что он один, и поднять второго поллера.
@Suite struct SingleInstanceTests {
    @Test func onlySelfIsNotADuplicate() {
        #expect(SingleInstance.isDuplicate(ownPID: 100, candidatePIDs: [100]) == false)
    }

    @Test func anotherPIDWithSameBundleIDIsADuplicate() {
        #expect(SingleInstance.isDuplicate(ownPID: 100, candidatePIDs: [100, 200]) == true)
    }

    @Test func emptyCandidateListIsNotADuplicate() {
        #expect(SingleInstance.isDuplicate(ownPID: 100, candidatePIDs: []) == false)
    }
}
