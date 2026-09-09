import Foundation
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

    /// Без этих ключей launchd отправляет вывод `open` в /dev/null, и причину
    /// несработавшего автозапуска узнать негде: приложение не стартовало, значит
    /// и в свой лог о себе ничего не написало.
    @Test func agentPlistRedirectsBothStreamsToTheLog() {
        let plist = LoginItem.agentPlist(bundlePath: "/Applications/ClaudeRC.app", logPath: "/l.log")
        #expect(plist["StandardOutPath"] as? String == "/l.log")
        #expect(plist["StandardErrorPath"] as? String == "/l.log")
    }

    /// Лог добавлен рядом с запуском, а не вместо него.
    @Test func agentPlistStillLaunchesTheBundleAtLogin() {
        let plist = LoginItem.agentPlist(bundlePath: "/Applications/ClaudeRC.app", logPath: "/l.log")
        let expected = ["/usr/bin/open", "-a", "/Applications/ClaudeRC.app"]
        #expect(plist["ProgramArguments"] as? [String] == expected)
        #expect(plist["RunAtLoad"] as? Bool == true)
    }

    /// Плист должен сериализоваться: неподдерживаемый тип в значении — это
    /// `writeAgent`, падающий уже на машине, а не в тесте.
    @Test func agentPlistSerializes() throws {
        let plist = LoginItem.agentPlist(bundlePath: "/Applications/ClaudeRC.app", logPath: "/l.log")
        let data = try PropertyListSerialization.data(
            fromPropertyList: plist, format: .xml, options: 0
        )
        #expect(data.isEmpty == false)
    }
}
