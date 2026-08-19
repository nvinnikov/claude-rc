import Foundation
import Testing

@testable import ClaudeRCMenu

@Test func configuredWhenConfigCheckPassed() {
    let json = """
    {"checks": [{"name": "config", "ok": true, "detail": "/Users/x/.config/claude-rc/config.toml"}]}
    """
    #expect(Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenConfigCheckFailed() {
    let json = """
    {"checks": [{"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}]}
    """
    #expect(!Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenTokenIsEmpty() {
    // Файл есть, но токен пуст — бот всё равно не поднимется.
    let json = """
    {"checks": [
      {"name": "config", "ok": true, "detail": "/x/config.toml"},
      {"name": "bot_token", "ok": false, "detail": "пуст"}
    ]}
    """
    #expect(!Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenDoctorSaidNothing() {
    // Пустой ответ — не повод считать, что всё хорошо.
    #expect(!Doctor.isConfigured(in: []))
}

@Test func unrelatedFailedChecksDoNotBlockStart() {
    // tmux или claude не найдены — это другая беда, и про неё скажет сам бот.
    let json = """
    {"checks": [
      {"name": "config", "ok": true, "detail": "/x/config.toml"},
      {"name": "bot_token", "ok": true, "detail": "задан"},
      {"name": "tmux", "ok": false, "detail": "не найден в PATH"}
    ]}
    """
    #expect(Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func setupRowShowsOnlyWhenNotConfigured() {
    #expect(isNotConfigured(.notConfigured))
    #expect(!isNotConfigured(.stopped))
    #expect(!isNotConfigured(.running(since: Date(timeIntervalSince1970: 0))))
    #expect(!isNotConfigured(nil))
}
