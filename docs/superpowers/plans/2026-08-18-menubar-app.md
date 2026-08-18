# Приложение в меню-баре — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Иконка в меню-баре, показывающая, жив ли Telegram-бот, поднимающая и гасящая его кликом, и переживающая вход в систему.

**Architecture:** Отдельный Swift-пакет в `app/`, собираемый SPM без `.xcodeproj`. Четыре типа: `CLILocator` (где `claude-rc` и с каким `PATH` его звать), `BotSupervisor` (владеет дочерним процессом и перезапусками), `LoginItem` (автозапуск с запасным путём), `AppDelegate` (иконка и меню). Скрипт собирает из бинаря бандл `ClaudeRC.app`.

**Tech Stack:** Swift 6.2, AppKit, ServiceManagement, swift-testing или XCTest из состава Command Line Tools.

**Spec:** `docs/superpowers/specs/2026-08-18-menubar-app-design.md`

## Global Constraints

- Ветка работы: `feat/menubar-app` (создана, на ней лежит спека).
- Python-часть репозитория не трогаем: `clauderc/` и `tests/` в этой части не меняются.
- Полного Xcode на машине нет, только Command Line Tools (`xcode-select -p` → `/Library/Developer/CommandLineTools`). Всё собирается через `swift build`; `.xcodeproj` не создаём и в git не кладём.
- `app/.build/` в git не попадает.
- Комментарии и сообщения коммитов — русские; код, идентификаторы, подписи пунктов меню — английские.
- Комментарии только там, где решение неочевидно.
- Коммиты атомарные, Conventional Commits; в теле — **почему**, а не что.
- Изменение поведения сопровождается тестом там, где предмет тестируем. GUI и настоящий `Process` не тестируем — они проверяются по чек-листу.
- **Тесты пишем на swift-testing, а не на XCTest.** `XCTest.framework` поставляется только вместе с Xcode.app, а на машине его нет — проверено поиском по диску. `Testing.framework` в Command Line Tools есть, но SPM не знает про его каталог, поэтому тесты гоняются так:

```bash
FW="$(xcode-select -p)/Library/Developer/Frameworks"
swift test --package-path app \
  -Xswiftc -F"$FW" -Xlinker -F"$FW" -Xlinker -rpath -Xlinker "$FW" \
  -Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays
```

  Последний флаг обязателен, если тест импортирует и `Foundation`, и `Testing`:
  это включает cross-import overlay `_Testing_Foundation`, а в Command Line Tools
  он идёт без `.swiftmodule`. Наши тесты трогают `URL` и `FileManager`, так что
  флаг нужен везде.

  Проверено живьём: с этими флагами набор проходит. Примеры тестов ниже по плану записаны в синтаксисе XCTest — **переводи их механически**, состав случаев и их смысл не меняя:

| XCTest | swift-testing |
|---|---|
| `import XCTest` + `final class X: XCTestCase` | `import Testing` + `struct X` (или свободные функции) |
| `func testFoo()` | `@Test func foo()` |
| `XCTAssertEqual(a, b)` | `#expect(a == b)` |
| `XCTAssertNil(a)` | `#expect(a == nil)` |
| `XCTAssertTrue(a)` / `XCTAssertFalse(a)` | `#expect(a)` / `#expect(!(a))` |
| `setUpWithError` / `tearDownWithError` | `init() throws` / `deinit`, либо создание и уборка внутри самого теста |
| `throws`-тест | `@Test func foo() throws` |

  Уборку временных каталогов делай внутри теста через `defer` — это надёжнее, чем полагаться на `deinit` у структуры.
- Бота из-под тестов не запускать: у токена может быть живой поллер, второй получит от Telegram конфликт.

---

### Task 0: Спайк — собирается ли бандл и работает ли автозапуск

Результат — знание, от которого зависят задачи 3 и 5. Кода не коммитим.

**Files:** черновики в `/private/tmp/claude-501/-Users-nvinnikov-Documents-tg-claude/0856f588-9100-4a4f-8d02-150d4802d96c/scratchpad/spike-app/`

**Interfaces:**
- Consumes: ничего
- Produces: ответ, появляется ли иконка в меню-баре из ad-hoc бандла и принимает ли `SMAppService.mainApp.register()` такой бандл

- [ ] **Step 1: Собрать минимальный менюбар-бинарь**

```bash
SP=/private/tmp/claude-501/-Users-nvinnikov-Documents-tg-claude/0856f588-9100-4a4f-8d02-150d4802d96c/scratchpad/spike-app
mkdir -p "$SP" && cd "$SP"
cat > main.swift <<'SWIFT'
import AppKit
import ServiceManagement

final class Delegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem?
    func applicationDidFinishLaunching(_ note: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "spike")
        item.button?.image?.isTemplate = true
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "spike alive", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Register login item", action: #selector(register), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        menu.items.forEach { $0.target = self }
        item.menu = menu
        self.item = item
        print("status item button:", item.button != nil)
        print("SMAppService status before:", SMAppService.mainApp.status.rawValue)
    }
    @objc func register() {
        do {
            try SMAppService.mainApp.register()
            print("SMAppService register: OK, status:", SMAppService.mainApp.status.rawValue)
        } catch {
            print("SMAppService register FAILED:", error)
        }
    }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
SWIFT
swiftc -o ClaudeRCSpike main.swift && echo "СОБРАЛОСЬ"
```

Ожидание: `СОБРАЛОСЬ`. Ошибки компиляции означают, что AppKit из Command Line Tools недоступен — тогда останови работу и сообщи человеку, весь план придётся пересматривать.

- [ ] **Step 2: Завернуть в бандл и подписать ad-hoc**

```bash
cd "$SP"
rm -rf ClaudeRCSpike.app
mkdir -p ClaudeRCSpike.app/Contents/MacOS
mv ClaudeRCSpike ClaudeRCSpike.app/Contents/MacOS/
cat > ClaudeRCSpike.app/Contents/Info.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>ClaudeRCSpike</string>
    <key>CFBundleIdentifier</key><string>com.nvinnikov.claude-rc-spike</string>
    <key>CFBundleName</key><string>ClaudeRCSpike</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.0.1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
codesign --force --sign - ClaudeRCSpike.app && echo "ПОДПИСАНО"
codesign -dv ClaudeRCSpike.app 2>&1 | head -3
```

- [ ] **Step 3: Запустить и посмотреть на меню-бар**

```bash
open "$SP/ClaudeRCSpike.app"
sleep 3
pgrep -fl ClaudeRCSpike
```

Ожидание: процесс жив. Спроси человека, видит ли он новую иконку-молнию в меню-баре — сам ты этого проверить не можешь. Если иконки нет, а процесс жив, зафиксируй это: значит бандлу чего-то не хватает.

- [ ] **Step 4: Проверить автозапуск**

Попроси человека нажать в меню иконки пункт `Register login item`, затем:

```bash
log show --last 2m --predicate 'process == "ClaudeRCSpike"' 2>/dev/null | tail -20
```

Либо, если вывод не виден, перезапусти приложение из терминала, чтобы `print` шёл в консоль:

```bash
pkill -f ClaudeRCSpike
"$SP/ClaudeRCSpike.app/Contents/MacOS/ClaudeRCSpike"
```

Нужен ответ на один вопрос: `SMAppService register` даёт `OK` или `FAILED` для ad-hoc подписанного бандла.

- [ ] **Step 5: Прибрать**

```bash
pkill -f ClaudeRCSpike
rm -rf "$SP/ClaudeRCSpike.app"
launchctl print gui/$(id -u) 2>/dev/null | grep -i claude-rc-spike || echo "следов автозапуска не осталось"
```

Если `SMAppService` успел зарегистрироваться, сними регистрацию: перезапусти бинарь и вызови `SMAppService.mainApp.unregister()`, либо удали запись через `launchctl`. Не оставляй за собой чужой автозапуск.

- [ ] **Step 6: Записать результат**

Ответь двумя строками: собирается ли бандл и появляется ли иконка; принимает ли `SMAppService` ad-hoc подпись. Если не принимает — план не меняется, но задача 4 пойдёт по запасному пути с LaunchAgent, и это надо явно отметить.

---

### Task 1: Пакет и `CLILocator`

**Files:**
- Create: `app/Package.swift`
- Create: `app/Sources/ClaudeRCMenu/CLILocator.swift`
- Create: `app/Tests/ClaudeRCMenuTests/CLILocatorTests.swift`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: ничего
- Produces:
  - `CLILocator.find(searchPaths: [String], environmentPath: String?) -> URL?`
  - `CLILocator.defaultSearchPaths: [String]`
  - `CLILocator.childEnvironment(base: [String: String]) -> [String: String]`

- [ ] **Step 1: Завести пакет**

Создай `app/Package.swift`:

```swift
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeRCMenu",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "ClaudeRCMenu"),
        .testTarget(name: "ClaudeRCMenuTests", dependencies: ["ClaudeRCMenu"]),
    ]
)
```

Добавь в `.gitignore` строку `app/.build/`.

Создай заглушку `app/Sources/ClaudeRCMenu/main.swift` с единственной строкой
`print("claude-rc menu")`, чтобы пакет собирался до появления настоящего входа.

- [ ] **Step 2: Написать падающие тесты**

Создай `app/Tests/ClaudeRCMenuTests/CLILocatorTests.swift`:

```swift
import XCTest
@testable import ClaudeRCMenu

final class CLILocatorTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("clilocator-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func makeExecutable(_ directory: String, name: String = "claude-rc") throws -> URL {
        let dir = root.appendingPathComponent(directory)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent(name)
        try Data().write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
        return file
    }

    func testFindsInFirstSearchPath() throws {
        let expected = try makeExecutable("first")
        _ = try makeExecutable("second")
        let found = CLILocator.find(
            searchPaths: [root.appendingPathComponent("first").path,
                          root.appendingPathComponent("second").path],
            environmentPath: nil
        )
        XCTAssertEqual(found?.path, expected.path)
    }

    func testSkipsMissingDirectories() throws {
        let expected = try makeExecutable("real")
        let found = CLILocator.find(
            searchPaths: [root.appendingPathComponent("nope").path,
                          root.appendingPathComponent("real").path],
            environmentPath: nil
        )
        XCTAssertEqual(found?.path, expected.path)
    }

    func testSkipsNonExecutableFile() throws {
        // Файл с тем же именем, но без бита исполнения, — не наша тулза.
        let dir = root.appendingPathComponent("plain")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data().write(to: dir.appendingPathComponent("claude-rc"))
        let expected = try makeExecutable("real")
        let found = CLILocator.find(
            searchPaths: [dir.path, root.appendingPathComponent("real").path],
            environmentPath: nil
        )
        XCTAssertEqual(found?.path, expected.path)
    }

    func testFallsBackToEnvironmentPath() throws {
        let expected = try makeExecutable("viapath")
        let found = CLILocator.find(
            searchPaths: [],
            environmentPath: "/nonexistent:\(root.appendingPathComponent("viapath").path)"
        )
        XCTAssertEqual(found?.path, expected.path)
    }

    func testReturnsNilWhenNothingFound() {
        XCTAssertNil(CLILocator.find(searchPaths: [root.path], environmentPath: nil))
    }

    func testChildEnvironmentAlwaysCarriesToolDirectories() {
        // Приложение из автозапуска получает голый PATH, и бот не найдёт ни tmux,
        // ни claude. На эти грабли уже наступали в launchd-плисте части 1.
        let env = CLILocator.childEnvironment(base: [:])
        let path = env["PATH"] ?? ""
        XCTAssertTrue(path.contains("/opt/homebrew/bin"))
        XCTAssertTrue(path.contains("/usr/bin"))
        XCTAssertTrue(path.contains(".local/bin"))
    }

    func testChildEnvironmentKeepsExistingPathEntries() {
        let env = CLILocator.childEnvironment(base: ["PATH": "/custom/tools"])
        XCTAssertTrue((env["PATH"] ?? "").contains("/custom/tools"))
    }

    func testChildEnvironmentDoesNotDuplicateEntries() {
        let env = CLILocator.childEnvironment(base: ["PATH": "/opt/homebrew/bin"])
        let entries = (env["PATH"] ?? "").split(separator: ":").filter { $0 == "/opt/homebrew/bin" }
        XCTAssertEqual(entries.count, 1)
    }

    func testChildEnvironmentPreservesOtherVariables() {
        let env = CLILocator.childEnvironment(base: ["HOME": "/Users/test"])
        XCTAssertEqual(env["HOME"], "/Users/test")
    }
}
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `make app-test 2>&1 | tail -20` (цель появится в задаче 4; до неё гоняй командой с флагами из Global Constraints)
Expected: ошибка компиляции — `cannot find 'CLILocator' in scope`

- [ ] **Step 4: Написать `CLILocator`**

Создай `app/Sources/ClaudeRCMenu/CLILocator.swift`:

```swift
import Foundation

/// Где лежит `claude-rc` и с каким окружением его звать.
enum CLILocator {
    /// Каталоги, куда тулза попадает при обычных способах установки.
    static let defaultSearchPaths: [String] = [
        NSHomeDirectory() + "/.local/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]

    /// Каталоги, без которых бот не найдёт tmux и claude. Приложение из автозапуска
    /// получает голый PATH — те же грабли, что были в launchd-плисте.
    private static let requiredPathEntries: [String] = [
        NSHomeDirectory() + "/.local/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]

    static func find(searchPaths: [String], environmentPath: String?) -> URL? {
        let fromEnvironment = (environmentPath ?? "")
            .split(separator: ":", omittingEmptySubsequences: true)
            .map(String.init)

        for directory in searchPaths + fromEnvironment {
            let candidate = URL(fileURLWithPath: directory).appendingPathComponent("claude-rc")
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func childEnvironment(base: [String: String]) -> [String: String] {
        var environment = base
        let existing = (base["PATH"] ?? "")
            .split(separator: ":", omittingEmptySubsequences: true)
            .map(String.init)

        var seen = Set<String>()
        let merged = (existing + requiredPathEntries).filter { seen.insert($0).inserted }
        environment["PATH"] = merged.joined(separator: ":")
        return environment
    }
}
```

- [ ] **Step 5: Проверить, что тесты проходят**

Run: командой из Global Constraints
Expected: 9 тестов зелёные

- [ ] **Step 6: Коммит**

```bash
git add app/Package.swift app/Sources app/Tests .gitignore
git commit -m "$(cat <<'MSG'
feat: swift-пакет приложения и поиск claude-rc

PATH собирается явно, а не наследуется: приложение из автозапуска получает голый
PATH, и бот не нашёл бы ни tmux, ни claude — те же грабли, что уже прописаны
руками в launchd-плисте.
MSG
)"
```

---

### Task 2: `BotSupervisor` и разбор `doctor --json`

**Files:**
- Create: `app/Sources/ClaudeRCMenu/BotSupervisor.swift`
- Create: `app/Sources/ClaudeRCMenu/Doctor.swift`
- Create: `app/Tests/ClaudeRCMenuTests/BackoffTests.swift`
- Create: `app/Tests/ClaudeRCMenuTests/DoctorTests.swift`

**Interfaces:**
- Consumes: `CLILocator.find`, `CLILocator.childEnvironment` (Task 1)
- Produces:
  - `enum BotState { case stopped, starting, running(since: Date), crashed(reason: String) }`
  - `BotSupervisor(cli: URL, logURL: URL)` с `start()`, `stop()`, `var state: BotState`, `var onStateChange: ((BotState) -> Void)?`, `static func foreignBotPID() -> Int32?`
  - `backoffDelay(attempt: Int) -> TimeInterval?` — `nil` означает «больше не пытаемся»
  - `Doctor.parse(_ data: Data) -> [Doctor.Check]` и `Doctor.Check { name, ok, detail }`

- [ ] **Step 1: Написать падающие тесты на паузы перезапуска**

Создай `app/Tests/ClaudeRCMenuTests/BackoffTests.swift`:

```swift
import XCTest
@testable import ClaudeRCMenu

final class BackoffTests: XCTestCase {
    func testFirstThreeAttemptsHaveGrowingDelays() {
        XCTAssertEqual(backoffDelay(attempt: 1), 2)
        XCTAssertEqual(backoffDelay(attempt: 2), 5)
        XCTAssertEqual(backoffDelay(attempt: 3), 15)
    }

    func testGivesUpAfterThirdAttempt() {
        // Бесконечная прокрутка падающего бота — тот же молчаливый сбой:
        // иконка мигает, а человек не понимает почему.
        XCTAssertNil(backoffDelay(attempt: 4))
        XCTAssertNil(backoffDelay(attempt: 10))
    }

    func testZeroAndNegativeAttemptsAreRejected() {
        XCTAssertNil(backoffDelay(attempt: 0))
        XCTAssertNil(backoffDelay(attempt: -1))
    }
}
```

- [ ] **Step 2: Написать падающие тесты на разбор `doctor --json`**

Создай `app/Tests/ClaudeRCMenuTests/DoctorTests.swift`:

```swift
import XCTest
@testable import ClaudeRCMenu

final class DoctorTests: XCTestCase {
    func testParsesChecks() throws {
        let json = """
        {"checks": [
          {"name": "tmux", "ok": true, "detail": "/opt/homebrew/bin/tmux"},
          {"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}
        ]}
        """.data(using: .utf8)!

        let checks = Doctor.parse(json)
        XCTAssertEqual(checks.count, 2)
        XCTAssertEqual(checks[0].name, "tmux")
        XCTAssertTrue(checks[0].ok)
        XCTAssertEqual(checks[1].name, "config")
        XCTAssertFalse(checks[1].ok)
    }

    func testConfigPathIsTakenFromDoctorNotFromConstant() {
        // Приложение и тулза не должны разойтись в понимании того, где лежит
        // конфиг: иначе человек будет править не тот файл.
        let json = """
        {"checks": [{"name": "config", "ok": true, "detail": "/Users/x/.config/claude-rc/config.toml"}]}
        """.data(using: .utf8)!

        XCTAssertEqual(Doctor.configPath(in: Doctor.parse(json)),
                       "/Users/x/.config/claude-rc/config.toml")
    }

    func testConfigPathIsNilWhenCheckFailed() {
        let json = """
        {"checks": [{"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}]}
        """.data(using: .utf8)!

        XCTAssertNil(Doctor.configPath(in: Doctor.parse(json)))
    }

    func testGarbageGivesEmptyList() {
        XCTAssertTrue(Doctor.parse(Data("не json".utf8)).isEmpty)
        XCTAssertTrue(Doctor.parse(Data()).isEmpty)
    }

    func testUnknownFieldsAreIgnored() {
        // Формат вывода тулзы задуман расширяемым: объект, а не голый массив.
        let json = """
        {"checks": [{"name": "tmux", "ok": true, "detail": "x", "future": 1}], "future": true}
        """.data(using: .utf8)!
        XCTAssertEqual(Doctor.parse(json).count, 1)
    }
}
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `make app-test 2>&1 | tail -20` (цель появится в задаче 4; до неё гоняй командой с флагами из Global Constraints)
Expected: `cannot find 'backoffDelay' in scope`, `cannot find 'Doctor' in scope`

- [ ] **Step 4: Написать `Doctor`**

Создай `app/Sources/ClaudeRCMenu/Doctor.swift`:

```swift
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
```

- [ ] **Step 5: Написать `BotSupervisor`**

Создай `app/Sources/ClaudeRCMenu/BotSupervisor.swift`:

```swift
import Foundation

enum BotState {
    case stopped
    case starting
    case running(since: Date)
    case crashed(reason: String)
}

/// Паузы перед перезапуском упавшего бота. `nil` — больше не пытаемся.
func backoffDelay(attempt: Int) -> TimeInterval? {
    switch attempt {
    case 1: return 2
    case 2: return 5
    case 3: return 15
    default: return nil
    }
}

/// Владеет процессом бота: запускает, гасит, переживает его падения.
///
/// Состояние «жив» — это `Process.isRunning`, а не догадка по сокету: бот запущен
/// нами, и другого источника правды не нужно.
final class BotSupervisor {
    var onStateChange: ((BotState) -> Void)?
    private(set) var state: BotState = .stopped {
        didSet { onStateChange?(state) }
    }

    private let cli: URL
    private let logURL: URL
    private var process: Process?
    private var stopRequested = false
    private var restartAttempt = 0

    init(cli: URL, logURL: URL) {
        self.cli = cli
        self.logURL = logURL
    }

    /// pid бота, запущенного мимо приложения. Два поллера одного токена получают
    /// от Telegram конфликт и работают через раз, поэтому свой мы не поднимаем.
    static func foreignBotPID(excluding ownPID: Int32?) -> Int32? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        task.arguments = ["-f", "claude-rc bot|clauderc.bot"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        guard (try? task.run()) != nil else { return nil }
        task.waitUntilExit()

        let output = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        ) ?? ""
        return output
            .split(separator: "\n")
            .compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }
            .first { $0 != ownPID && $0 != ProcessInfo.processInfo.processIdentifier }
    }

    func start() {
        guard process == nil else { return }
        if let foreign = BotSupervisor.foreignBotPID(excluding: process?.processIdentifier) {
            state = .crashed(reason: "бот уже запущен вне приложения, pid \(foreign)")
            return
        }

        stopRequested = false
        state = .starting

        let task = Process()
        task.executableURL = cli
        task.arguments = ["bot"]
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        do {
            task.standardOutput = try appendingHandle()
            task.standardError = try appendingHandle()
        } catch {
            state = .crashed(reason: "лог не открывается: \(error.localizedDescription)")
            return
        }

        task.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.handleTermination(finished) }
        }

        do {
            try task.run()
        } catch {
            state = .crashed(reason: "не запускается: \(error.localizedDescription)")
            return
        }

        process = task
        restartAttempt = 0
        state = .running(since: Date())
    }

    func stop() {
        stopRequested = true
        process?.terminate()
        process = nil
        state = .stopped
    }

    private func handleTermination(_ finished: Process) {
        process = nil
        if stopRequested {
            state = .stopped
            return
        }

        restartAttempt += 1
        guard let delay = backoffDelay(attempt: restartAttempt) else {
            state = .crashed(reason: "упал \(restartAttempt) раза подряд, код \(finished.terminationStatus)")
            return
        }

        state = .crashed(reason: "упал, перезапуск через \(Int(delay)) с")
        let attempt = restartAttempt
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.process == nil, !self.stopRequested else { return }
            self.start()
            self.restartAttempt = attempt  // start() обнуляет счётчик, а серию надо помнить
        }
    }

    /// Дозапись, а не перезапись: причина падения не должна теряться при перезапуске.
    private func appendingHandle() throws -> FileHandle {
        let manager = FileManager.default
        try manager.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        if !manager.fileExists(atPath: logURL.path) {
            manager.createFile(atPath: logURL.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        return handle
    }
}
```

- [ ] **Step 6: Проверить, что тесты проходят**

Run: командой из Global Constraints
Expected: все тесты зелёные (9 из задачи 1 плюс 9 новых)

- [ ] **Step 7: Коммит**

```bash
git add app/Sources app/Tests
git commit -m "$(cat <<'MSG'
feat: надзор за процессом бота и разбор doctor --json

Перезапуск ограничен тремя попытками: бесконечная прокрутка падающего бота — тот
же молчаливый сбой, только с мигающей иконкой. Путь к конфигу берётся из вывода
тулзы, иначе приложение и CLI разойдутся в том, где он лежит.
MSG
)"
```

---

### Task 3: Иконка, меню и автозапуск

**Files:**
- Create: `app/Sources/ClaudeRCMenu/LoginItem.swift`
- Create: `app/Sources/ClaudeRCMenu/AppDelegate.swift`
- Modify: `app/Sources/ClaudeRCMenu/main.swift`

**Interfaces:**
- Consumes: `CLILocator`, `BotSupervisor`, `BotState`, `Doctor` (Tasks 1-2)
- Produces: приложение целиком; следующая задача его только упаковывает

- [ ] **Step 1: Написать `LoginItem`**

Создай `app/Sources/ClaudeRCMenu/LoginItem.swift`:

```swift
import Foundation
import ServiceManagement

/// Автозапуск при входе.
///
/// `SMAppService` требует подписанного бандла, а у нас ad-hoc подпись — отказ здесь
/// ожидаемая ветка, а не редкость. Запасной путь пишет LaunchAgent руками.
enum LoginItem {
    private static let label = "com.nvinnikov.claude-rc-app"

    static var isEnabled: Bool {
        if SMAppService.mainApp.status == .enabled { return true }
        return FileManager.default.fileExists(atPath: agentURL.path)
    }

    static func enable() throws {
        do {
            try SMAppService.mainApp.register()
        } catch {
            try writeAgent()
        }
    }

    static func disable() {
        try? SMAppService.mainApp.unregister()
        removeAgent()
    }

    private static var agentURL: URL {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    private static func writeAgent() throws {
        let bundlePath = Bundle.main.bundlePath
        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": ["/usr/bin/open", "-a", bundlePath],
            "RunAtLoad": true,
        ]
        let data = try PropertyListSerialization.data(
            fromPropertyList: plist, format: .xml, options: 0
        )
        try FileManager.default.createDirectory(
            at: agentURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try data.write(to: agentURL)

        let load = Process()
        load.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        load.arguments = ["bootstrap", "gui/\(getuid())", agentURL.path]
        try? load.run()
        load.waitUntilExit()
    }

    private static func removeAgent() {
        let unload = Process()
        unload.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        unload.arguments = ["bootout", "gui/\(getuid())/\(label)"]
        try? unload.run()
        unload.waitUntilExit()
        try? FileManager.default.removeItem(at: agentURL)
    }
}
```

- [ ] **Step 2: Написать `AppDelegate`**

Создай `app/Sources/ClaudeRCMenu/AppDelegate.swift`:

```swift
import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem?
    private var supervisor: BotSupervisor?
    private var cli: URL?

    private let statusRow = NSMenuItem(title: "Bot: stopped", action: nil, keyEquivalent: "")
    private let toggleRow = NSMenuItem(title: "Start bot", action: nil, keyEquivalent: "")
    private let loginRow = NSMenuItem(title: "Launch at login", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = icon(alive: false)
        item.menu = buildMenu()
        statusItem = item

        cli = CLILocator.find(
            searchPaths: CLILocator.defaultSearchPaths,
            environmentPath: ProcessInfo.processInfo.environment["PATH"]
        )
        if let cli {
            let supervisor = BotSupervisor(cli: cli, logURL: logURL)
            supervisor.onStateChange = { [weak self] state in self?.render(state) }
            self.supervisor = supervisor
            supervisor.start()
        } else {
            render(.crashed(reason: "CLI not found"))
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Бот живёт внутри приложения — это выбранная модель владения, и иконка
        // всё время честно её показывала. Подтверждения не спрашиваем.
        supervisor?.stop()
    }

    private var logURL: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".claude-rc/claude-rc.log")
    }

    private func buildMenu() -> NSMenu {
        let menu = NSMenu()
        menu.delegate = self

        statusRow.isEnabled = false
        menu.addItem(statusRow)

        toggleRow.action = #selector(toggleBot)
        toggleRow.target = self
        menu.addItem(toggleRow)

        menu.addItem(.separator())

        let log = NSMenuItem(title: "Open log", action: #selector(openLog), keyEquivalent: "")
        log.target = self
        menu.addItem(log)

        let config = NSMenuItem(title: "Reveal config", action: #selector(revealConfig), keyEquivalent: "")
        config.target = self
        menu.addItem(config)

        menu.addItem(.separator())

        loginRow.action = #selector(toggleLoginItem)
        loginRow.target = self
        menu.addItem(loginRow)

        let quit = NSMenuItem(
            title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"
        )
        menu.addItem(quit)
        return menu
    }

    /// Пока меню закрыто, обновлять нечего — поэтому пересборка здесь, а не по таймеру.
    func menuNeedsUpdate(_ menu: NSMenu) {
        render(supervisor?.state ?? .crashed(reason: "CLI not found"))
        loginRow.state = LoginItem.isEnabled ? .on : .off
    }

    private func render(_ state: BotState) {
        switch state {
        case .stopped:
            statusRow.title = "Bot: stopped"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = cli != nil
        case .starting:
            statusRow.title = "Bot: starting…"
            toggleRow.title = "Stop bot"
            toggleRow.isEnabled = true
        case .running(let since):
            statusRow.title = "Bot: running · \(uptime(since: since))"
            toggleRow.title = "Stop bot"
            toggleRow.isEnabled = true
        case .crashed(let reason):
            statusRow.title = "Bot: \(reason)"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = cli != nil
        }
        statusItem?.button?.image = icon(alive: isAlive(state))
    }

    private func isAlive(_ state: BotState) -> Bool {
        if case .running = state { return true }
        return false
    }

    private func uptime(since: Date) -> String {
        let seconds = Int(Date().timeIntervalSince(since))
        if seconds < 60 { return "\(seconds)s" }
        if seconds < 3600 { return "\(seconds / 60)m" }
        return "\(seconds / 3600)h"
    }

    private func icon(alive: Bool) -> NSImage? {
        let name = alive ? "bolt.fill" : "bolt"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "claude-rc")
        image?.isTemplate = true
        return image
    }

    @objc private func toggleBot() {
        guard let supervisor else { return }
        if isAlive(supervisor.state) {
            supervisor.stop()
        } else {
            supervisor.start()
        }
    }

    @objc private func openLog() {
        NSWorkspace.shared.open(logURL)
    }

    @objc private func revealConfig() {
        guard let cli else { return }
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)
        guard (try? task.run()) != nil else { return }
        task.waitUntilExit()

        let checks = Doctor.parse(pipe.fileHandleForReading.readDataToEndOfFile())
        if let path = Doctor.configPath(in: checks) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        } else {
            // Конфига ещё нет — показываем каталог, куда его класть.
            let directory = URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent(".config/claude-rc")
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            NSWorkspace.shared.open(directory)
        }
    }

    @objc private func toggleLoginItem() {
        if LoginItem.isEnabled {
            LoginItem.disable()
        } else {
            try? LoginItem.enable()
        }
        loginRow.state = LoginItem.isEnabled ? .on : .off
    }
}
```

- [ ] **Step 3: Заменить `main.swift`**

```swift
import AppKit

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
// .accessory — без иконки в Dock; то же самое объявлено в Info.plist через LSUIElement.
app.setActivationPolicy(.accessory)
app.run()
```

- [ ] **Step 4: Убедиться, что собирается и тесты не сломались**

Run: `swift build --package-path app`, затем тесты командой из Global Constraints
Expected: сборка без ошибок, все тесты зелёные

- [ ] **Step 5: Коммит**

```bash
git add app/Sources
git commit -m "$(cat <<'MSG'
feat: иконка в меню-баре, тумблер бота и автозапуск

Заголовок пересобирается в menuNeedsUpdate, а не по таймеру: пока меню закрыто,
обновлять нечего. Автозапуск идёт через SMAppService с запасным LaunchAgent —
ad-hoc подписи для штатного пути может не хватить.
MSG
)"
```

---

### Task 4: Сборка бандла

**Files:**
- Create: `app/make-app.sh`
- Modify: `Makefile`
- Modify: `README.md`
- Delete: `launchd/com.nvinnikov.claude-rc.plist`

**Interfaces:**
- Consumes: собранный бинарь `ClaudeRCMenu` (Task 3)
- Produces: `ClaudeRC.app` в `app/build/`, цель `make app`

- [ ] **Step 1: Написать скрипт сборки**

Создай `app/make-app.sh` (не забудь `chmod +x`):

```bash
#!/usr/bin/env bash
# Собирает ClaudeRC.app из продукта SPM. Полного Xcode на машине нет, поэтому
# бандл складывается руками, а не через xcodebuild.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="ClaudeRCMenu"
APP="$HERE/build/ClaudeRC.app"

# Версия одна на тулзу и приложение, иначе они разъедутся.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$HERE/../pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "версия не найдена в pyproject.toml" >&2; exit 1; }

swift build -c release --package-path "$HERE"
BIN="$(swift build -c release --package-path "$HERE" --show-bin-path)/$NAME"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/$NAME"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$NAME</string>
    <key>CFBundleIdentifier</key><string>com.nvinnikov.claude-rc-app</string>
    <key>CFBundleName</key><string>ClaudeRC</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
echo "$APP"
```

- [ ] **Step 2: Добавить цели в Makefile**

```make
app:
	./app/make-app.sh

# Testing.framework живёт в каталоге Command Line Tools, про который SPM не знает.
# Флаги добавляются, только если каталог есть, — на машине с полным Xcode он не нужен.
app-test:
	@FW="$$(xcode-select -p)/Library/Developer/Frameworks"; \
	if [ -d "$$FW" ]; then \
		swift test --package-path app \
			-Xswiftc -F"$$FW" -Xlinker -F"$$FW" -Xlinker -rpath -Xlinker "$$FW" \
			-Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays; \
	else \
		swift test --package-path app; \
	fi
```

Добавь `app` и `app-test` в список `.PHONY`.

- [ ] **Step 3: Собрать и проверить бандл**

```bash
make app
ls -la app/build/ClaudeRC.app/Contents/MacOS/
codesign -dv app/build/ClaudeRC.app 2>&1 | head -3
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" app/build/ClaudeRC.app/Contents/Info.plist
```

Ожидание: бинарь на месте, подпись ad-hoc, версия совпадает с `pyproject.toml`.

- [ ] **Step 4: Запустить и проверить руками**

```bash
open app/build/ClaudeRC.app
sleep 3
pgrep -fl ClaudeRCMenu
```

Чек-лист, который проверяет человек (сам ты меню-бар не видишь — попроси подтвердить):
1. иконка появилась в меню-баре;
2. в меню видно состояние бота, и оно соответствует действительности;
3. `Stop bot` гасит бота, иконка становится контурной, RC-сессии в tmux при этом живы (`tmux ls`);
4. `Start bot` поднимает его обратно, иконка становится залитой;
5. `Open log` открывает `~/.claude-rc/claude-rc.log`;
6. `Reveal config` показывает в Finder тот же файл, что печатает `claude-rc doctor`;
7. галочка `Launch at login` ставится и снимается, и её состояние переживает перезапуск приложения;
8. `Quit` закрывает приложение и гасит бота.

Если на машине уже крутится бот вне приложения — меню обязано показать его pid и не поднимать свой.

- [ ] **Step 5: Убрать launchd-плист**

```bash
git rm launchd/com.nvinnikov.claude-rc.plist
rmdir launchd 2>/dev/null || true
```

Из README удали секцию про ручную установку плиста и опиши вместо неё `make app`,
перенос `ClaudeRC.app` в `/Applications` и галочку `Launch at login`. Скажи прямо,
что бот живёт внутри приложения: закрыл приложение — бот погас.

- [ ] **Step 6: Обновить CLAUDE.md**

В таблицу «Структура» добавь строку про `app/`. В «Грабли» добавь абзац:

```
**Приложение из автозапуска получает голый `PATH`.** Бот, поднятый им как дочерний
процесс, не найдёт ни `tmux`, ни `claude`, если не подставить `PATH` явно —
`CLILocator.childEnvironment` существует ровно для этого. Те же грабли раньше
обходились руками в launchd-плисте.

**Два поллера одного токена ломают бота молча.** Telegram отдаёт конфликт, и оба
экземпляра работают через раз. Перед запуском своего процесса приложение ищет чужой
и, найдя, показывает его pid вместо запуска.
```

- [ ] **Step 7: Коммит**

```bash
git add app/make-app.sh Makefile README.md CLAUDE.md
git add -u launchd 2>/dev/null || true
git commit -m "$(cat <<'MSG'
feat: сборка ClaudeRC.app и переезд с launchd

Плист уходит: приложение делает то же самое, но состояние бота видно, а не
угадывается. Бандл собирается скриптом, потому что полного Xcode на машине нет,
а .xcodeproj в диффе не читается.
MSG
)"
```

---

## Проверка целиком

- [ ] **Swift-тесты и python-гейт**

```bash
make app-test
make check
```

- [ ] **Сборка из чистого состояния**

```bash
rm -rf app/.build app/build
make app && echo "собралось с нуля"
```

- [ ] **Открыть PR**

```bash
git push -u origin feat/menubar-app
gh pr create --base master --title "feat: приложение в меню-баре" --body "..."
```
