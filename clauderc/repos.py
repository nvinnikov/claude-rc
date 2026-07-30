"""Поиск каталогов, в которых можно поднять RC-сессию."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# Каталоги, внутрь которых спускаться бессмысленно и дорого.
SKIP_DIRS = {
    "node_modules",
    "vendor",
    "target",
    "build",
    "Pods",
    "__pycache__",
    "Library",
    ".worktrees",
}


def _walk(root: Path, depth: int) -> Iterator[Path]:
    if depth <= 0:
        return
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return  # нет прав или каталог исчез — просто пропускаем
    for entry in entries:
        if entry.name.startswith(".") or entry.name in SKIP_DIRS or not entry.is_dir():
            continue
        if (entry / ".git").exists():
            yield entry
        # Спускаемся и внутрь репозиториев: там лежат сабмодули и вложенные
        # сервисы вида <монорепо>/services/<сервис>.
        yield from _walk(entry, depth - 1)


def discover(roots: list[Path], depth: int = 3) -> list[Path]:
    """Цели запуска: сами корни плюс всё с `.git` на глубину `depth`.

    Корни включены потому, что рабочий зонт-каталог собственного
    `.git` не имеет, но запускаться в нём осмысленно.
    """
    found: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        found[root] = None
        for path in _walk(root, depth):
            found[path] = None
    return sorted(found, key=lambda p: (p.name.lower(), str(p)))


def resolve(query: str, candidates: list[Path]) -> list[Path]:
    """Точное совпадение имени каталога, иначе — все частичные."""
    needle = query.strip().lower()
    if not needle:
        return []
    exact = [p for p in candidates if p.name.lower() == needle]
    if exact:
        return exact
    return [p for p in candidates if needle in p.name.lower()]
