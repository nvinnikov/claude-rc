"""Сборка config.toml для визарда первого запуска.

Всё, что можно проверить тестом, живёт здесь чистыми функциями: сам диалог в
`cli.py` только спрашивает и печатает.
"""

from __future__ import annotations

import asyncio
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class TokenCheck:
    ok: bool
    bot_name: str | None
    offline: bool
    detail: str


def _make_bot(token: str) -> Any:
    """Обёртка ради тестируемости: заглушка подменяет именно её."""
    from aiogram import Bot

    return Bot(token=token)


async def verify_token(token: str) -> TokenCheck:
    """Спрашивает Telegram, живой ли токен.

    Опечатка в токене иначе превращается в крэш-луп бота, который разбирают
    полчаса. Отсутствие сети и отказ Telegram различаются: в первом случае
    настройку можно продолжать, во втором — бессмысленно.
    """
    if not looks_like_token(token):
        return TokenCheck(False, None, False, "не похоже на токен от @BotFather")

    bot = _make_bot(token)
    try:
        me = await bot.get_me()
    except OSError as exc:
        return TokenCheck(False, None, True, f"нет связи с Telegram: {exc}")
    except Exception:
        # Текст исключения от aiogram может содержать сам токен (он в URL) —
        # наружу отдаём только факт отказа.
        return TokenCheck(False, None, False, "Telegram отверг токен")
    finally:
        await _close(bot)

    return TokenCheck(True, getattr(me, "username", None), False, "токен принят")


async def catch_user_id(token: str, *, timeout_s: float = 120.0) -> int | None:
    """Ждёт первое сообщение боту и возвращает id отправителя.

    Иначе человеку пришлось бы искать @userinfobot и копировать число — самый
    ошибкоёмкий шаг настройки.

    Зовётся только пока бот не настроен, то есть заведомо не запущен: второй
    поллер того же токена ломает работающего бота.
    """
    from aiogram import Bot

    bot = Bot(token=token)
    deadline = asyncio.get_running_loop().time() + timeout_s
    offset: int | None = None
    try:
        while asyncio.get_running_loop().time() < deadline:
            updates = await bot.get_updates(offset=offset, timeout=5)
            for update in updates:
                offset = update.update_id + 1
                message = getattr(update, "message", None)
                sender = getattr(message, "from_user", None) if message else None
                if sender is not None:
                    return int(sender.id)
    except Exception:
        return None
    finally:
        await _close(bot)
    return None


async def _close(bot: Any) -> None:
    session = getattr(bot, "session", None)
    if session is not None:
        await session.close()


def _toml_string(value: str) -> str:
    """Строковый литерал TOML. Пользуемся тем, что tomllib умеет читать обратно."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    literal = f'"{escaped}"'
    # Дешёвая страховка от собственной ошибки экранирования: если получившийся
    # литерал не читается обратно, лучше упасть здесь, чем отдать битый конфиг.
    if tomllib.loads(f"x = {literal}")["x"] != value:
        raise ValueError(f"не удалось закодировать значение для TOML: {value!r}")
    return literal
