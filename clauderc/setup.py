"""Сборка config.toml для визарда первого запуска.

Всё, что можно проверить тестом, живёт здесь чистыми функциями: сам диалог в
`cli.py` только спрашивает и печатает.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_VISIBLE_TAIL = 4


class RootsError(ValueError):
    """Ответ про каталоги нельзя принять."""


@dataclass(frozen=True)
class Answers:
    bot_token: str
    allowed_user_id: int
    rc_roots: tuple[Path, ...]


def render_config(answers: Answers) -> str:
    """Текст config.toml. Значения экранируются как строки TOML.

    В токене есть двоеточие, а в путях может быть что угодно — склейка через
    кавычки рано или поздно даст файл, который не читается.
    """
    roots = ", ".join(_toml_string(str(root)) for root in answers.rc_roots)
    return (
        "# Создан `claude-rc setup`. Правь руками, если нужно.\n\n"
        "# Токен бота от @BotFather.\n"
        f"bot_token = {_toml_string(answers.bot_token)}\n\n"
        "# Твой Telegram user_id. Только он может управлять ботом.\n"
        f"allowed_user_id = {answers.allowed_user_id}\n\n"
        "# Где искать репозитории. Сами корни тоже валидные цели.\n"
        f"rc_roots = [{roots}]\n"
    )


def parse_roots(raw: str, *, default: tuple[Path, ...]) -> tuple[Path, ...]:
    """Разбирает ответ про каталоги: несколько путей через запятую."""
    items = [chunk.strip() for chunk in raw.split(",")]
    items = [chunk for chunk in items if chunk]
    if not items:
        if default:
            return default
        raise RootsError("нужен хотя бы один каталог")

    roots = tuple(Path(item).expanduser() for item in items)
    missing = [str(root) for root in roots if not root.is_dir()]
    if missing:
        raise RootsError(f"каталог не найден: {', '.join(missing)}")
    return roots


def mask_token(token: str) -> str:
    """Скрывает середину токена: узнать свой можно, скопировать чужой — нет."""
    if not token:
        return ""
    head, _, tail = token.partition(":")
    if not tail:
        return "…"
    return f"{head}:…{tail[-_VISIBLE_TAIL:]}" if len(tail) > _VISIBLE_TAIL else f"{head}:…"


def looks_like_token(value: str) -> bool:
    """Грубая проверка формы. Настоящая проверка — вызовом getMe."""
    return bool(_TOKEN.match(value.strip()))


def default_roots() -> tuple[Path, ...]:
    """Что предложить по умолчанию.

    Домашний каталог целиком — плохой ответ: обход на глубину 3 по нему долгий
    и мусорный. Поэтому сначала пробуем ~/Documents.
    """
    documents = Path.home() / "Documents"
    return (documents,) if documents.is_dir() else (Path.home(),)


def _toml_string(value: str) -> str:
    """Строковый литерал TOML. Пользуемся тем, что tomllib умеет читать обратно."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    literal = f'"{escaped}"'
    # Дешёвая страховка от собственной ошибки экранирования: если получившийся
    # литерал не читается обратно, лучше упасть здесь, чем отдать битый конфиг.
    if tomllib.loads(f"x = {literal}")["x"] != value:
        raise ValueError(f"не удалось закодировать значение для TOML: {value!r}")
    return literal
