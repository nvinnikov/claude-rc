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

        private enum CodingKeys: String, CodingKey { case name, ok, detail }

        // detail отсутствует в JSON — не повод ронять декодирование всего конверта:
        // без этого одна неполная проверка молча стирала все остальные из parse().
        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            name = try container.decode(String.self, forKey: .name)
            ok = try container.decode(Bool.self, forKey: .ok)
            detail = try container.decodeIfPresent(String.self, forKey: .detail) ?? ""
        }
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

    /// Хватает ли конфига, чтобы бот вообще поднялся.
    ///
    /// Смотрим только на проверки про конфиг: отсутствие `tmux` — другая беда,
    /// и про неё бот скажет сам, а вот без токена он просто упадёт.
    static func isConfigured(in checks: [Check]) -> Bool {
        let blocking = ["config", "bot_token", "allowed_user_id"]
        guard checks.contains(where: { $0.name == "config" }) else { return false }
        return !checks.contains { blocking.contains($0.name) && !$0.ok }
    }
}
