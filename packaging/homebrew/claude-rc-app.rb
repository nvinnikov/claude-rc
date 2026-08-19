cask "claude-rc-app" do
  version "0.2.0"
  sha256 "18eecdba40f0eeebe0df1599ff0fdab4de89ddbfe41092aa505241a1137895cf"

  url "https://github.com/nvinnikov/claude-rc/releases/download/v#{version}/ClaudeRC.app.zip"
  name "ClaudeRC"
  desc "Menu bar app that keeps the claude-rc bot running"
  homepage "https://github.com/nvinnikov/claude-rc"

  # Приложение без тулзы бессмысленно: оно только запускает `claude-rc bot`.
  # Полный путь тапа — иначе зависимость резолвится только потому, что тап
  # уже подключён у того, кто ставит cask руками, а не сама по себе.
  depends_on formula: "nvinnikov/tap/claude-rc"
  depends_on macos: :sonoma
  # app/make-app.sh собирает `swift build -c release` без указания архитектур —
  # бинарь получается под архитектуру сборочной машины (arm64), универсального
  # никто не делает. На Intel cask ставил бы бандл, который не запустится.
  depends_on arch: :arm64

  app "ClaudeRC.app"

  # Без этого `brew uninstall --cask` удалит бандл из-под живого процесса:
  # приложение останется в памяти, а бот внутри него продолжит поллить
  # Telegram, хотя человек считает, что его удалил.
  uninstall quit: "com.nvinnikov.claude-rc-app"

  # `~/.claude-rc` целиком сюда не входит: там `worktree_root` и `state_path`
  # (см. config.example.toml) — рабочие каталоги, которые бот заводит под
  # git worktree, и в них может быть незакоммиченная работа человека. `zap`
  # чистит только то, что приложение оставляет само: лог и LaunchAgent.
  # `~/.config/claude-rc/config.toml` тоже не входит — там боевой токен бота,
  # и удалять его при удалении приложения нельзя.
  zap trash: [
    "~/.claude-rc/claude-rc.log",
    "~/Library/LaunchAgents/com.nvinnikov.claude-rc-app.plist",
  ]

  # Cask ставит com.apple.quarantine на скачанный бандл, а подписан он ad-hoc
  # (без Developer ID) — Gatekeeper откажется открывать его точно так же, как
  # и зип, скачанный руками со страницы релизов.
  caveats <<~EOS
    ClaudeRC.app подписан ad-hoc, без Developer ID — macOS пометит его
    карантином и откажется открывать. Сними карантин вручную:

      xattr -dr com.apple.quarantine #{appdir}/ClaudeRC.app

    Либо переустанови без карантина:

      brew install --cask --no-quarantine claude-rc-app
  EOS
end
