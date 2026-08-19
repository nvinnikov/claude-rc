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

from aiogram.exceptions import TelegramNetworkError

_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_VISIBLE_TAIL = 4


class RootsError(ValueError):
    """Ответ про каталоги нельзя принять."""


@dataclass(frozen=True)
class Answers:
    bot_token: str
    allowed_user_id: int
    rc_roots: tuple[Path, ...]


def render_config(answers: Answers, *, extra: dict[str, object] | None = None) -> str:
    """Текст config.toml. Значения экранируются как строки TOML.

    В токене есть двоеточие, а в путях может быть что угодно — склейка через
    кавычки рано или поздно даст файл, который не читается.

    `extra` — поля, которых визард не спрашивает (`worktree_root`, `scan_depth`
    и т.п.), но которые могли быть правлены руками в прежнем файле. Без них
    повторный запуск визарда молча стирал бы такую правку. Тип каждого значения
    неизвестен заранее (читается сырым `tomllib` в cli.py) — `_toml_value`
    отбрасывает то, что не умеет закодировать обратно.
    """
    roots = ", ".join(_toml_string(str(root)) for root in answers.rc_roots)
    base = (
        "# Создан `claude-rc setup`. Правь руками, если нужно.\n\n"
        "# Токен бота от @BotFather.\n"
        f"bot_token = {_toml_string(answers.bot_token)}\n\n"
        "# Твой Telegram user_id. Только он может управлять ботом.\n"
        f"allowed_user_id = {answers.allowed_user_id}\n\n"
        "# Где искать репозитории. Сами корни тоже валидные цели.\n"
        f"rc_roots = [{roots}]\n"
    )
    if not extra:
        return base
    lines = []
    for key, value in extra.items():
        literal = _toml_value(value)
        if literal is not None:
            lines.append(f"\n{key} = {literal}\n")
    return base + "".join(lines)


def unsupported_extra_keys(extra: dict[str, object] | None) -> tuple[tuple[str, str], ...]:
    """Ключи `extra`, которые `render_config` молча отбросил, и их тип.

    Отдельно от `render_config`, а не print внутри нее: та функция чистая и
    только строит текст. Человек должен узнать, что поле пропало, а не
    обнаружить это через полгода, разбирая, откуда в конфиге нет его правки —
    печатью занимается вызывающий код в cli.py.
    """
    if not extra:
        return ()
    return tuple(
        (key, type(value).__name__) for key, value in extra.items() if _toml_value(value) is None
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
    except (TelegramNetworkError, OSError):
        # aiogram заворачивает обрыв сети (таймаут, ClientError) в
        # TelegramNetworkError — голый OSError он не пропускает никогда, но
        # ловим и его на случай других версий и подмен в тестах. Текст
        # исключения в сообщение не кладём по той же причине, что и ниже:
        # он может содержать токен.
        return TokenCheck(False, None, True, "нет связи с Telegram")
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
    bot = _make_bot(token)
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
        # Тот же довод, что и в verify_token: текст ошибки может содержать токен
        # (он в URL запроса) — наружу отдаём None, а не исключение с его текстом.
        return None
    finally:
        await _close(bot)
    return None


async def _close(bot: Any) -> None:
    session = getattr(bot, "session", None)
    if session is not None:
        await session.close()


def _toml_value(value: object) -> str | None:
    """Литерал TOML для поля из `extra` — тип сохраняем как в исходном файле.

    `None` — значение неизвестного типа (список, таблица, дата и т.п.), которое
    не переносим вовсе: `str(value)` дал бы синтаксис, который `tomllib` не
    читает обратно, — лучше потерять один непонятный ключ, чем весь файл после
    этого перестанет разбираться.
    """
    if isinstance(value, bool):
        # bool — подкласс int в Python, поэтому проверяем раньше него: иначе
        # `scan_depth = true` ушло бы как `str(True)` → `scan_depth = True`,
        # а это не валидный TOML.
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _toml_string(value: str) -> str:
    """Строковый литерал TOML. Пользуемся тем, что tomllib умеет читать обратно."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    literal = f'"{escaped}"'
    # Дешёвая страховка от собственной ошибки экранирования: если получившийся
    # литерал не читается обратно, лучше упасть здесь, чем отдать битый конфиг.
    # Значение в сообщение не кладём: сюда попадает и bot_token, а текст исключения
    # долетает до логов и трейсбеков.
    if tomllib.loads(f"x = {literal}")["x"] != value:
        raise ValueError("не удалось закодировать значение для TOML")
    return literal
