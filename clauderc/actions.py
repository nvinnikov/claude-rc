"""Действия над живой сессией: набрать текст, прочитать хвост, переименовать,
перезапустить. Ни одно не гасит сессию само — гашение идёт через того, кому
его передали (в боте это Watcher), см. `restart`.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

from clauderc import remote
from clauderc.remote import RemoteSession

LABEL_OPTION = "@rc_label"
_UNKNOWN_COMMAND = re.compile(r"Unknown (?:slash )?command", re.IGNORECASE)


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
        await remote._run("send-keys", "-t", _pane(session), "Enter", check=False)


async def tail(session: RemoteSession, lines: int = 5) -> str:
    """Последние непустые строки панели."""
    code, pane = await remote._run("capture-pane", "-p", "-J", "-t", _pane(session), check=False)
    if code != 0:
        raise ActionError(pane.strip() or "capture-pane failed")
    rows = pane.rstrip("\n").split("\n")
    return "\n".join(rows[-lines:]) if lines > 0 else ""


def _repo_of(session: RemoteSession) -> str:
    label = session.label or ""
    if "@" in label:
        return label.split("@", 1)[0]
    return os.path.basename(session.cwd.rstrip(os.sep)) or session.name


async def rename(session: RemoteSession, name: str, *, settle_s: float = 1.5) -> RenameResult:
    """Новый ярлык `repo@name`: в tmux всегда, в приложении — если claude знает /rename.

    Ветку и каталог не трогает: они git, а не название.
    """
    label = f"{_repo_of(session)}@{name.strip()}"
    code, out = await remote._run(
        "set-option", "-t", _pane(session), LABEL_OPTION, label, check=False
    )
    if code != 0:
        raise ActionError(out.strip() or f"set-option: код {code}")
    await send(session, f"/rename {name.strip()}")
    await asyncio.sleep(settle_s)
    recent = await tail(session, lines=8)
    return RenameResult(label=label, app_renamed=_UNKNOWN_COMMAND.search(recent) is None)
