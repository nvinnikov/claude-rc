"""Навигация по дереву каталогов: cd/ls для выбора места запуска сессии.

Поиск по имени (`/rc <репо>`) хорош, когда имя известно и уникально. Когда нет —
проще дойти ногами, как в шелле: с телефона тыкать кнопки быстрее, чем печатать пути.
"""

from __future__ import annotations

import os
from pathlib import Path

from tgclaude.repos import SKIP_DIRS

MAX_ENTRIES = 20


class BrowseError(ValueError):
    """Каталог не найден или недоступен."""


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def entries(path: Path, limit: int = MAX_ENTRIES) -> tuple[list[Path], int]:
    """Подкаталоги для показа и сколько всего их нашлось.

    Возвращаем и обрезанный список, и полное число: молча скрывать часть дерева
    нельзя — иначе кажется, что каталог пуст.
    """
    try:
        found = sorted(
            child
            for child in path.iterdir()
            if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP_DIRS
        )
    except OSError as exc:
        raise BrowseError(f"не читается: {exc.strerror or exc}") from exc
    return found[:limit], len(found)


def change_dir(current: Path, target: str) -> Path:
    """Разрешает `cd`: абсолютный путь, относительный, `..`, `~`."""
    text = target.strip()
    if not text:
        return current

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = current / candidate

    # normpath до resolve: он схлопывает `..` лексически, поэтому `cd ..` из симлинка
    # ведёт туда, куда пользователь смотрит, а не в физического родителя.
    resolved = Path(os.path.normpath(candidate))
    if not resolved.is_dir():
        raise BrowseError(f"каталог не найден: {resolved}")
    return resolved
