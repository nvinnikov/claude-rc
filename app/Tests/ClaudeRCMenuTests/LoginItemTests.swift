import ServiceManagement
import Testing
@testable import ClaudeRCMenu

/// Тут была ошибка ревью: любая ошибка `register()` уходила в один и тот же
/// запасной путь, даже когда штатная регистрация фактически уже прошла
/// (`.requiresApproval`) — бот заводился дважды при следующем входе.
@Suite struct LoginItemTests {
    @Test func fallbackNotNeededWhenRegistrationEnabled() {
        #expect(LoginItem.needsFallback(status: .enabled) == false)
    }

    @Test func fallbackNotNeededWhenRegistrationAwaitsApproval() {
        #expect(LoginItem.needsFallback(status: .requiresApproval) == false)
    }

    @Test func fallbackNeededWhenNotRegistered() {
        #expect(LoginItem.needsFallback(status: .notRegistered) == true)
    }

    @Test func fallbackNeededWhenServiceNotFound() {
        #expect(LoginItem.needsFallback(status: .notFound) == true)
    }

    @Test func isEnabledTrueWhenServiceEnabledRegardlessOfAgentFile() {
        #expect(LoginItem.isEnabled(status: .enabled, agentFileExists: false) == true)
    }

    @Test func isEnabledTrueWhenAwaitingApprovalRegardlessOfAgentFile() {
        #expect(LoginItem.isEnabled(status: .requiresApproval, agentFileExists: false) == true)
    }

    @Test func isEnabledFallsBackToAgentFileWhenServiceNotRegistered() {
        #expect(LoginItem.isEnabled(status: .notRegistered, agentFileExists: true) == true)
        #expect(LoginItem.isEnabled(status: .notRegistered, agentFileExists: false) == false)
    }
}
