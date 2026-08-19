import Foundation
import Testing
@testable import ClaudeRCMenu

// XCTest недоступен на этой машине (см. CLILocatorTests.swift) — та же логика
// тестов и тот же набор случаев, оформленные через swift-testing.
@Suite struct DoctorTests {
    @Test func parsesChecks() {
        let json = """
        {"checks": [
          {"name": "tmux", "ok": true, "detail": "/opt/homebrew/bin/tmux"},
          {"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}
        ]}
        """.data(using: .utf8)!

        let checks = Doctor.parse(json)
        #expect(checks.count == 2)
        #expect(checks[0].name == "tmux")
        #expect(checks[0].ok)
        #expect(checks[1].name == "config")
        #expect(!checks[1].ok)
    }

    @Test func configPathIsTakenFromDoctorNotFromConstant() {
        // Приложение и тулза не должны разойтись в понимании того, где лежит
        // конфиг: иначе человек будет править не тот файл.
        let json = """
        {"checks": [{"name": "config", "ok": true, "detail": "/Users/x/.config/claude-rc/config.toml"}]}
        """.data(using: .utf8)!

        #expect(Doctor.configPath(in: Doctor.parse(json)) == "/Users/x/.config/claude-rc/config.toml")
    }

    @Test func configPathIsNilWhenCheckFailed() {
        let json = """
        {"checks": [{"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}]}
        """.data(using: .utf8)!

        #expect(Doctor.configPath(in: Doctor.parse(json)) == nil)
    }

    @Test func garbageGivesEmptyList() {
        #expect(Doctor.parse(Data("не json".utf8)).isEmpty)
        #expect(Doctor.parse(Data()).isEmpty)
    }

    @Test func unknownFieldsAreIgnored() {
        // Формат вывода тулзы задуман расширяемым: объект, а не голый массив.
        let json = """
        {"checks": [{"name": "tmux", "ok": true, "detail": "x", "future": 1}], "future": true}
        """.data(using: .utf8)!
        #expect(Doctor.parse(json).count == 1)
    }

    @Test func missingDetailDoesNotDropWholeEnvelope() {
        // Одна проверка без detail не должна ронять декодирование всего конверта:
        // раньше это молча превращало parse() в пустой список.
        let json = """
        {"checks": [
          {"name": "tmux", "ok": true},
          {"name": "config", "ok": false, "detail": "нет файла"}
        ]}
        """.data(using: .utf8)!

        let checks = Doctor.parse(json)
        #expect(checks.count == 2)
        #expect(checks[0].detail == "")
    }
}
