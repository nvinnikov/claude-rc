class ClaudeRc < Formula
  include Language::Python::Virtualenv

  desc "Telegram bot that launches Claude Code sessions with Remote Control"
  homepage "https://github.com/nvinnikov/claude-rc"
  url "https://github.com/nvinnikov/claude-rc/releases/download/vVERSION/claude_rc-VERSION.tar.gz"
  sha256 "REPLACE_WITH_SHA256"
  license "MIT"

  depends_on "python@3.12"
  # Без tmux сессию не поднять: `claude --remote-control` требует tty.
  depends_on "tmux"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "claude-rc", shell_output("#{bin}/claude-rc --help")
  end
end
