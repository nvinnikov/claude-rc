import AppKit

/// Защита от второго экземпляра приложения.
///
/// `open -a` от LaunchServices второй процесс поверх работающего не создаёт, но
/// прямой запуск бинаря — создаёт. Тогда оба экземпляра независимо опрашивают
/// `foreignBotPID()`, каждый честно опознаёт чужого бота и предлагает его забрать;
/// после клика `Take over` в одном экземпляре второй, ничего не знающий об этом,
/// через пару секунд поднимает своего — и поллеров одного токена становится два
/// (см. I-3 в финальном ревью). Единственный сигнал, что "я не один", —
/// bundle identifier: у обоих экземпляров один и тот же .app.
enum SingleInstance {
    /// Чистая часть решения: среди чужих pid (уже отфильтрованных по тому же
    /// bundle identifier — см. `check()`) есть ли хоть один, что не наш собственный.
    /// `runningApplications(withBundleIdentifier:)` включает и сам процесс —
    /// без исключения self единственный экземпляр считал бы себя дублем.
    static func isDuplicate(ownPID: pid_t, candidatePIDs: [pid_t]) -> Bool {
        candidatePIDs.contains { $0 != ownPID }
    }

    /// Прямой запуск бинаря не из бандла даёт `Bundle.main.bundleIdentifier == nil`.
    /// Сравнивать в этом случае не с чем — единственная защита у нас именно по
    /// bundle identifier, и без него говорим «дубля нет», а не падаем.
    static func check() -> Bool {
        guard let bundleID = Bundle.main.bundleIdentifier else { return false }
        let pids = NSRunningApplication
            .runningApplications(withBundleIdentifier: bundleID)
            .map(\.processIdentifier)
        return isDuplicate(ownPID: ProcessInfo.processInfo.processIdentifier, candidatePIDs: pids)
    }
}
