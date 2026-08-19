class ClaudeRc < Formula
  include Language::Python::Virtualenv

  desc "Telegram bot that launches Claude Code sessions with Remote Control"
  homepage "https://github.com/nvinnikov/claude-rc"
  url "https://github.com/nvinnikov/claude-rc/releases/download/vVERSION/claude_rc-VERSION.tar.gz"
  sha256 "REPLACE_WITH_SHA256"
  # Проект пока не объявил лицензию (нет ни LICENSE, ни `license` в
  # pyproject.toml) — строку не добавляем сами, это решение автора, не наше.

  depends_on "python@3.12"
  # Без tmux сессию не поднять: `claude --remote-control` требует tty.
  depends_on "tmux"

  # ОБЯЗАТЕЛЬНО перед первой публикацией: virtualenv_install_with_resources
  # ставит только сам пакет, а не его зависимости (aiogram и её транзитивные
  # зависимости) — без блоков `resource` на каждую из них `brew install`
  # упадёт, не найдя их на PyPI внутри изолированного venv. Сгенерировать их
  # можно только имея этот файл внутри настоящего tap:
  #   brew update-python-resources Formula/claude-rc.rb
  # См. packaging/homebrew/README.md.
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "claude-rc", shell_output("#{bin}/claude-rc --help")
  end
end
