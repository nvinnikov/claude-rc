cask "claude-rc-app" do
  version "VERSION"
  sha256 "REPLACE_WITH_SHA256"

  url "https://github.com/nvinnikov/claude-rc/releases/download/v#{version}/ClaudeRC.app.zip"
  name "ClaudeRC"
  desc "Menu bar app that keeps the claude-rc bot running"
  homepage "https://github.com/nvinnikov/claude-rc"

  # Приложение без тулзы бессмысленно: оно только запускает `claude-rc bot`.
  depends_on formula: "claude-rc"
  depends_on macos: ">= :sonoma"

  app "ClaudeRC.app"

  # `~/.config/claude-rc/config.toml` сюда намеренно не входит: там боевой
  # токен бота, и удалять его при удалении приложения нельзя.
  zap trash: [
    "~/.claude-rc",
    "~/Library/LaunchAgents/com.nvinnikov.claude-rc-app.plist",
  ]
end
