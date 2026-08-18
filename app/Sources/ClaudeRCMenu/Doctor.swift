import Foundation

/// Разбор `claude-rc doctor --json`.
///
/// Путь к конфигу берём отсюда, а не из своей константы: иначе приложение и тулза
/// разойдутся в понимании того, где он лежит, и человек будет править не тот файл.
enum Doctor {
    struct Check: Decodable {
        let name: String
        let ok: Bool
        let detail: String
    }

    private struct Envelope: Decodable {
        let checks: [Check]
    }

    static func parse(_ data: Data) -> [Check] {
        (try? JSONDecoder().decode(Envelope.self, from: data))?.checks ?? []
    }

    static func configPath(in checks: [Check]) -> String? {
        guard let check = checks.first(where: { $0.name == "config" }), check.ok else {
            return nil
        }
        return check.detail
    }
}
