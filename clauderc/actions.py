"""Действия над живой сессией: набрать текст, прочитать хвост, переименовать,
перезапустить. Ни одно не гасит сессию само — гашение идёт через того, кому
его передали (в боте это Watcher), см. `restart`.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from clauderc import remote as remote  # тесты подменяют actions.remote.launch
from clauderc.remote import RemoteSession
from clauderc.worktrees import clean_name

LABEL_OPTION = "@rc_label"
_UNKNOWN_COMMAND = re.compile(r"Unknown (?:slash )?command", re.IGNORECASE)

# (tmux_name, cwd) -> погашена ли; в боте — замыкание над Watcher.kill, в CLI —
# обёртка над remote.kill_tmux. Гашение чужое: сам restart сессию не трогает,
# чтобы намеренная смерть не доехала до человека карточкой «сессия упала».
# Экземпляр сессии (tmux-id) в сигнатуру не входит: он нужен только метке
# Watcher, а `restart` о ней не знает — бот подставляет его замыканием.
Killer = Callable[[str, str], Awaitable[bool]]
_SESSION_PREFIX = "session_"


class ActionError(RuntimeError):
    """tmux отказал: сессии нет или команда не прошла."""


@dataclass(frozen=True)
class RenameResult:
    label: str
    app_renamed: bool


def _pane(session: RemoteSession) -> str:
    return f"={session.tmux_name}:"


async def send(session: RemoteSession, text: str, *, enter: bool = True) -> None:
    """Набирает `text` в панель как есть (`-l`) и, если надо, жмёт Enter.

    Без `-l` tmux трактует слова вроде `Enter` и `C-c` как имена клавиш. Enter
    — отдельным вызовом: это и есть клавиша.
    """
    code, out = await remote._run("send-keys", "-t", _pane(session), "-l", text, check=False)
    if code != 0:
        raise ActionError(out.strip() or f"send-keys: код {code}")
    if enter:
        code, out = await remote._run("send-keys", "-t", _pane(session), "Enter", check=False)
        if code != 0:
            raise ActionError(out.strip() or f"send-keys Enter: код {code}")


async def tail(session: RemoteSession, lines: int = 5) -> str:
    """Последние непустые строки панели."""
    code, pane = await remote._run("capture-pane", "-p", "-J", "-t", _pane(session), check=False)
    if code != 0:
        raise ActionError(pane.strip() or "capture-pane failed")
    rows = pane.rstrip("\n").split("\n")
    return "\n".join(rows[-lines:]) if lines > 0 else ""


async def send_and_tail(
    session: RemoteSession, text: str, *, enter: bool = True, wait_s: float = 3.0, lines: int = 20
) -> str:
    """Посылает текст и читает хвост панели.

    Пауза ждёт, пока TUI успеет отреагировать на команду — иначе хвост читаться
    будет, пока ещё ничего не произошло, и вернёт старый вывод.
    """
    await send(session, text, enter=enter)
    await asyncio.sleep(wait_s)
    return await tail(session, lines=lines)


def _repo_of(session: RemoteSession) -> str:
    label = session.label or ""
    if "@" in label:
        return label.split("@", 1)[0]
    return os.path.basename(session.cwd.rstrip(os.sep)) or session.name


async def rename(session: RemoteSession, name: str, *, settle_s: float = 1.5) -> RenameResult:
    """Новый ярлык `repo@name`: в tmux всегда, в приложении — если claude знает /rename.

    Ветку и каталог не трогает: они git, а не название. Имя проходит через
    `clean_name`: таб или перевод строки в нём разорвал бы строку
    `list-sessions`, и сессия исчезла бы из всех списков (см. clean_name).
    """
    cleaned = clean_name(name)
    if not cleaned:
        raise ActionError("пустое имя")
    label = f"{_repo_of(session)}@{cleaned}"
    code, out = await remote._run(
        "set-option", "-t", _pane(session), LABEL_OPTION, label, check=False
    )
    if code != 0:
        raise ActionError(out.strip() or f"set-option: код {code}")
    await send(session, f"/rename {cleaned}")
    await asyncio.sleep(settle_s)
    recent = await tail(session, lines=8)
    return RenameResult(label=label, app_renamed=_UNKNOWN_COMMAND.search(recent) is None)


async def restart(
    session: RemoteSession, *, kill: Killer, mode: str | None = None, timeout_s: float = 90.0
) -> RemoteSession:
    """Гасит и поднимает заново в том же каталоге с тем же ярлыком и --resume.

    `kill` — чужой: в боте это Watcher.kill, иначе намеренное гашение доехало
    бы карточкой «сессия упала». Режим — из аргумента, иначе прежний из
    @rc_mode. Ссылка после перезапуска новая.
    """
    session_id = session.tmux_name if session.tmux_name.startswith(_SESSION_PREFIX) else None
    if not await kill(session.tmux_name, session.cwd):
        raise ActionError(f"сессия {session.name} не погасла — перезапуск отменён")
    return await remote.launch(
        session.label or session.name,
        session.cwd,
        timeout_s=timeout_s,
        resume=session_id,
        permission_mode=mode or session.mode or None,
    )
