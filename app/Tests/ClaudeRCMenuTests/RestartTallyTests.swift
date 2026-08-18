import Testing
@testable import ClaudeRCMenu

@Suite struct RestartTallyTests {
    @Test func threeQuickDeathsGiveGrowingDelaysThenGiveUp() {
        var tally = RestartTally()
        tally.recordDeath(afterUptime: 0)
        #expect(tally.nextDelay == 2)
        tally.recordDeath(afterUptime: 0)
        #expect(tally.nextDelay == 5)
        tally.recordDeath(afterUptime: 0)
        #expect(tally.nextDelay == 15)
        tally.recordDeath(afterUptime: 0)
        #expect(tally.nextDelay == nil)
    }

    @Test func deathAfterLongUptimeStartsSeriesOver() {
        // Бот, падающий раз в месяц, не должен упереться в лимит на четвёртом
        // падении подряд — подряд их не было.
        var tally = RestartTally()
        tally.recordDeath(afterUptime: 0)
        tally.recordDeath(afterUptime: 0)
        tally.recordDeath(afterUptime: 0)
        tally.recordDeath(afterUptime: 0)
        #expect(tally.nextDelay == nil)

        tally.recordDeath(afterUptime: RestartTally.resetThreshold)
        #expect(tally.nextDelay == 2)
    }

    @Test func stableBotBetweenRareCrashesNeverGivesUp() {
        var tally = RestartTally()
        for _ in 0..<10 {
            tally.recordDeath(afterUptime: RestartTally.resetThreshold + 1)
            #expect(tally.nextDelay == 2)
        }
    }
}
