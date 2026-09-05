import Foundation
import Testing
@testable import ClaudeRCMenu

@Suite struct UpdaterTests {
    @Test func parsesTheStatusFromTheTool() {
        let json = """
        {"channel": "clone", "install": "клон /src/claude-rc", "current": "0.2.0",
         "latest": "0.3.0", "available": true}
        """.data(using: .utf8)!

        let status = Updater.parse(json)
        #expect(status?.current == "0.2.0")
        #expect(status?.latest == "0.3.0")
        #expect(status?.available == true)
    }

    @Test func survivesAMissingField() {
        // Тот же контракт, что у Doctor.Check: одна недостача не должна стирать
        // остальные поля вместе с собой.
        let status = Updater.parse(#"{"current": "0.2.0"}"#.data(using: .utf8)!)
        #expect(status?.current == "0.2.0")
        #expect(status?.available == false)
        #expect(status?.latest == nil)
    }

    @Test func returnsNothingForOutputThatIsNotJSON() {
        #expect(Updater.parse("claude-rc: command not found".data(using: .utf8)!) == nil)
    }

    @Test func clickInstallsOnlyWhenThereIsSomethingToInstall() {
        // Пункт с надписью «Check for updates…» не должен гасить бота и
        // переустанавливать тулзу: заголовок и действие решаются одним признаком.
        #expect(!Updater.installsOnClick(status: nil))
        #expect(!Updater.installsOnClick(status: made(available: false)))
        #expect(!Updater.installsOnClick(status: made(available: true, latest: nil)))
        #expect(Updater.installsOnClick(status: made(available: true)))
    }

    @Test func titleAndActionAgree() {
        for status in [nil, made(available: false), made(available: true)] {
            let installs = Updater.installsOnClick(status: status)
            let promises = Updater.menuTitle(for: status).hasPrefix("Update to ")
            #expect(installs == promises)
        }
    }

    @Test func menuTitleNamesTheVersionOnlyWhenThereIsOne() {
        #expect(Updater.menuTitle(for: nil) == "Check for updates…")
        #expect(Updater.menuTitle(for: made(available: false)) == "Check for updates…")
        #expect(Updater.menuTitle(for: made(available: true)) == "Update to 0.3.0…")
    }

    @Test func menuNoteSeparatesUpToDateFromUnknown() {
        #expect(Updater.menuNote(for: nil) == "")
        #expect(Updater.menuNote(for: made(available: true)) == "0.2.0 → 0.3.0")
        #expect(Updater.menuNote(for: made(available: false)) == "0.2.0, новее нет")
        // Сеть недоступна — это не «новее нет»: сказать так значило бы соврать.
        #expect(
            Updater.menuNote(for: made(available: false, latest: nil))
                == "0.2.0, последний релиз не узнать"
        )
    }

    @Test func runsByItselfOnlyWithTheToggleOnAndSomethingToInstall() {
        #expect(
            Updater.shouldRunAutomatically(
                status: made(available: true), enabled: true, lastAttempt: nil
            )
        )
        // Тумблер выключен — молчим: обновление гасит приложение.
        #expect(
            !Updater.shouldRunAutomatically(
                status: made(available: true), enabled: false, lastAttempt: nil
            )
        )
        #expect(
            !Updater.shouldRunAutomatically(
                status: made(available: false), enabled: true, lastAttempt: nil
            )
        )
        #expect(Updater.shouldRunAutomatically(status: nil, enabled: true, lastAttempt: nil) == false)
    }

    @Test func doesNotRetryTheSameVersionByItself() {
        // Неудачное обновление иначе зацикливается: приложение открывается
        // заново, проверка снова видит ту же версию и снова открывает Терминал.
        #expect(
            !Updater.shouldRunAutomatically(
                status: made(available: true), enabled: true, lastAttempt: "0.3.0"
            )
        )
        // Следующая версия — снова повод.
        #expect(
            Updater.shouldRunAutomatically(
                status: made(available: true, latest: "0.4.0"), enabled: true, lastAttempt: "0.3.0"
            )
        )
    }

    @Test func scriptQuotesPathsAndAlwaysReopensTheApp() {
        let script = Updater.script(
            cli: "/Users/o'brien/bin/claude-rc", bundle: "/Applications/My App.app"
        )
        // Апостроф в пути пользователя разваливает скрипт без честного экранирования.
        #expect(script.contains(#"'/Users/o'\''brien/bin/claude-rc' update"#))
        #expect(script.contains("open -a '/Applications/My App.app'"))
        // `open` не под условием: неудачное обновление не должно оставить
        // человека вообще без приложения.
        #expect(!script.contains("&& open"))
        // Тот же якорь, что в Makefile и в foreignBotPID: голое имя ловит чужие процессы.
        #expect(script.contains("pkill -f '/ClaudeRCMenu$'"))
    }

    private func made(available: Bool, latest: String? = "0.3.0") -> Updater.Status {
        let latestField = latest.map { "\"\($0)\"" } ?? "null"
        let json = """
        {"channel": "clone", "install": "клон /src", "current": "0.2.0",
         "latest": \(latestField), "available": \(available)}
        """
        return Updater.parse(json.data(using: .utf8)!)!
    }
}
