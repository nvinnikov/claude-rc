"""Текущий каталог бота между перезапусками.

Одно значение на одного владельца — SQLite тут был бы избыточен.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("tgclaude.state")


class State:
    def __init__(self, path: Path, default_cwd: Path) -> None:
        self._path = path
        self._default = default_cwd
        self._cwd = self._load()

    def _load(self) -> Path:
        try:
            saved = json.loads(self._path.read_text()).get("cwd")
        except (OSError, ValueError):
            return self._default
        if not saved:
            return self._default
        path = Path(saved)
        # Каталог мог исчезнуть, пока бот лежал: не падаем, откатываемся к корню.
        return path if path.is_dir() else self._default

    @property
    def cwd(self) -> Path:
        if not self._cwd.is_dir():
            self._cwd = self._default
        return self._cwd

    def set_cwd(self, path: Path) -> None:
        self._cwd = path
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"cwd": str(path)}))
        except OSError as exc:
            # Не запомнили — не беда: в этом сеансе навигация всё равно работает.
            log.warning("не сохранил состояние в %s: %s", self._path, exc)
