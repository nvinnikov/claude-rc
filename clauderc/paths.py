"""Где лежат файлы установленной тулзы.

Единственное место, которое знает расположение конфига, лога и хранилища
диалогов Claude Code. Пока конфиг искали рядом с исходниками, пакет нельзя
было поставить: `uv tool install` кладёт код в чужой каталог, где `config.toml`
взяться неоткуда.
"""

from __future__ import annotations

import os
from pathlib import Path


def config_file() -> Path:
    """Первый существующий конфиг из цепочки; если ни одного — XDG-путь.

    Возврат несуществующего XDG-пути намеренный: сообщение об ошибке должно
    называть место, куда конфиг положить, а не то, где его случайно искали.
    """
    env = os.environ.get("CLAUDE_RC_CONFIG")
    if env:
        return Path(env).expanduser()

    candidates = [
        Path.home() / ".config/claude-rc/config.toml",
        Path.cwd() / "config.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def log_file() -> Path:
    return Path.home() / ".claude-rc/claude-rc.log"


def claude_projects() -> Path:
    """Хранилище диалогов Claude Code. CLAUDE_CONFIG_DIR уважаем: он есть у claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(env).expanduser() if env else Path.home() / ".claude"
    return base / "projects"
