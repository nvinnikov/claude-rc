"""Диалоги Claude Code, лежащие в ~/.claude/projects.

Своего реестра сессий не заводим: единственный источник правды — то же
хранилище, из которого `claude --resume` берёт историю. Формат хранилища
публичным API не является, поэтому модуль изолирован и при любой неожиданности
возвращает пустой список — бот тогда деградирует до запуска без развилки.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clauderc import paths as paths  # реэкспорт: тест патчит history.paths.claude_projects

log = logging.getLogger("clauderc.history")

PREVIEW_CHARS = 80
# Служебные записи идут первыми, а cwd и первое сообщение лежат в начале файла.
# Диалог бывает на сотни килобайт — читать его целиком незачем.
_MAX_LINES = 200
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_SPACES = re.compile(r"\s+")
_UNTITLED = "без названия"


@dataclass(frozen=True)
class Conversation:
    session_id: str
    cwd: str
    updated_at: float
    preview: str


def slug(cwd: str) -> str:
    """Имя каталога внутри ~/.claude/projects.

    Каждый символ вне [A-Za-z0-9] заменяется по отдельности, серии не
    схлопываются: `/Users/n/.x` даёт `-Users-n--x`, два дефиса подряд.
    """
    return _NON_ALNUM.sub("-", cwd)


def conversations(cwd: str, *, limit: int = 5) -> list[Conversation]:
    """Диалоги Claude Code для каталога, свежие первыми."""
    directory = _directory(cwd)
    if directory is None:
        return []

    wanted = _real(cwd)
    found: list[Conversation] = []
    for path in directory.glob("*.jsonl"):
        if not path.is_file():
            continue  # рядом с диалогами лежат каталоги, названные тем же uuid
        conversation = _read(path)
        if conversation is None or _real(conversation.cwd) != wanted:
            continue
        found.append(conversation)

    found.sort(key=lambda c: c.updated_at, reverse=True)
    return found[:limit]


def _directory(cwd: str) -> Path | None:
    """Каталог хранилища для пути. Пробуем и как есть, и через realpath.

    claude называет каталог по тому пути, из которого его запустили; наш
    вызывающий мог отдать симлинк.
    """
    root = paths.claude_projects()
    for candidate in (cwd, _real(cwd)):
        directory = root / slug(candidate)
        if directory.is_dir():
            return directory
    return None


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _read(path: Path) -> Conversation | None:
    cwd: str | None = None
    preview = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index >= _MAX_LINES:
                    break
                record = _record(line)
                if record is None:
                    continue
                if cwd is None and isinstance(record.get("cwd"), str):
                    cwd = record["cwd"]
                if not preview and record.get("type") == "user":
                    preview = _preview(record)
                if cwd is not None and preview:
                    break
            mtime = path.stat().st_mtime
    except OSError as exc:
        log.debug("skip %s: %s", path, exc)
        return None

    if cwd is None:
        return None
    return Conversation(
        session_id=path.stem, cwd=cwd, updated_at=mtime, preview=preview or _UNTITLED
    )


def _record(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None  # файл пишется прямо сейчас или испорчен — одна строка не повод падать
    return parsed if isinstance(parsed, dict) else None


def _preview(record: dict[str, Any]) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    else:
        return ""

    text = _SPACES.sub(" ", text).strip()
    if len(text) > PREVIEW_CHARS:
        return text[:PREVIEW_CHARS] + "…"
    return text
