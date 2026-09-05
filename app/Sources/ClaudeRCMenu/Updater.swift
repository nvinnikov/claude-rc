import Foundation

/// Разбор `claude-rc update --check --json` и решения, которые из него следуют.
///
/// Версии и каналы установки знает тулза, а не приложение: продублировать здесь
/// её логику значит завести второй источник правды, который разойдётся с первым
/// при первой же смене способа установки. Приложению остаётся показать ответ и
/// решить, звать ли обновление само.
enum Updater {
    struct Status: Decodable, Equatable {
        let channel: String
        let install: String
        let current: String
        let latest: String?
        let available: Bool

        private enum CodingKeys: String, CodingKey {
            case channel, install, current, latest, available
        }

        // Те же правила, что у Doctor.Check: отсутствующее поле не должно ронять
        // разбор целиком — иначе одна недостача стирает и версию, и канал.
        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            channel = try container.decodeIfPresent(String.self, forKey: .channel) ?? "unknown"
            install = try container.decodeIfPresent(String.self, forKey: .install) ?? ""
            current = try container.decodeIfPresent(String.self, forKey: .current) ?? "?"
            latest = try container.decodeIfPresent(String.self, forKey: .latest)
            available = try container.decodeIfPresent(Bool.self, forKey: .available) ?? false
        }
    }

    static func parse(_ data: Data) -> Status? {
        try? JSONDecoder().decode(Status.self, from: data)
    }

    /// Спрашивает тулзу под таймаутом. Зовётся из фона: команда ходит в сеть, и
    /// на главном потоке она подвесила бы меню-бар на всё время похода.
    ///
    /// `--check --json` ничего не устанавливает — так что даже сорванный по
    /// таймауту вызов не оставляет установку на середине.
    static func check(cli: URL, timeout: TimeInterval = 30) -> Status? {
        let task = Process()
        task.executableURL = cli
        task.arguments = ["update", "--check", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else {
            Log.app("update check: claude-rc не запустился")
            return nil
        }
        guard exited.wait(timeout: .now() + timeout) == .success else {
            task.terminate()
            Log.app("update check: claude-rc не ответил за \(Int(timeout))с")
            return nil
        }
        return parse(pipe.fileHandleForReading.readDataToEndOfFile())
    }

    /// Заголовок пункта меню.
    ///
    /// Версию в заголовке показываем только когда она есть и новее: «Update to
    /// 0.3.0…» — это уже причина нажать, а «Check for updates…» — предложение
    /// сходить в сеть.
    static func menuTitle(for status: Status?) -> String {
        guard let status, status.available, let latest = status.latest else {
            return "Check for updates…"
        }
        return "Update to \(latest)…"
    }

    /// Подпись под пунктом: что стоит и какой версии. Пусто — показывать нечего.
    static func menuNote(for status: Status?) -> String {
        guard let status else { return "" }
        if status.available { return "\(status.current) → \(status.latest ?? "?")" }
        if status.latest == nil { return "\(status.current), последний релиз не узнать" }
        return "\(status.current), новее нет"
    }

    /// Запускать ли обновление само.
    ///
    /// Обновление гасит приложение (переустановка кладёт файлы под работающим
    /// процессом, поэтому и `make install`, и каска сначала его убивают), так что
    /// делать это без спроса можно только по явно включённому тумблеру.
    static func shouldRunAutomatically(status: Status?, enabled: Bool) -> Bool {
        guard enabled, let status else { return false }
        return status.available
    }

    /// Скрипт обновления для Терминала.
    ///
    /// Через Терминал, а не дочерним процессом, по двум причинам сразу: обновление
    /// убивает приложение (значит, дочерний процесс умер бы вместе с ним на
    /// середине), и вывод `git`/`make`/`brew` человеку надо видеть — молчаливое
    /// обновление, которое не получилось, хуже отсутствия обновления.
    ///
    /// Приложение гасим сами, до переустановки: по SIGTERM оно успевает остановить
    /// бота (см. `installSignalHandlers`), а `pkill` внутри `make install` пришёл бы
    /// в тот же момент, но уже без гарантии, что мы не в середине своей работы.
    /// Шаблон `/ClaudeRCMenu$` — тот же, что в Makefile и в `foreignBotPID`.
    /// `open` в конце безусловен: если обновление не удалось, человек всё равно
    /// должен остаться с работающим приложением, а не без него.
    static func script(cli: String, bundle: String) -> String {
        """
        #!/bin/sh
        clear
        echo 'Обновление claude-rc. Приложение будет закрыто и открыто заново.'
        pkill -f '/ClaudeRCMenu$' 2>/dev/null
        sleep 2
        \(shellQuoted(cli)) update
        status=$?
        open -a \(shellQuoted(bundle))
        exit $status
        """
    }
}
