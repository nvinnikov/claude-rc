"""Состояние сессии, каким его видно на панели.

У tmux нет событий, у claude нет API статуса сессии. Единственный источник —
последние строки панели, поэтому всё здесь — наблюдение, а не знание, и
рядом всегда отдаётся сырой хвост: читатель должен иметь возможность не
поверить ярлыку. Шаблон, сломанный обновлением claude, даёт UNKNOWN, а не
ложный IDLE; на каждое состояние есть тест с настоящим снимком панели.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum

from clauderc import remote

_EXEC_TIMEOUT_S = 5.0

_TAIL_FOR_CLASSIFY = 12

_NEEDS_INPUT = (
    re.compile(r"Yes, I trust this folder"),
    # Только с начала строки: диалог печатает вопрос своей строкой, а в прозе
    # claude «…, do you want to see more?» встречается посреди ответа — и
    # свободная сессия показывалась бы как ждущая ответа.
    re.compile(r"^\s*Do you want to", re.MULTILINE),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    # Каретка обязательна: без неё в диалог записывался любой ответ claude с
    # нумерованным списком, и свободная сессия показывалась как «ждёт ответа».
    # Подсвеченный пункт диалог рисует именно с ней.
    re.compile(r"^\s*❯\s*\d+[.)]\s+\S", re.MULTILINE),
)
_WORKING = (
    re.compile(r"esc to interrupt"),
    re.compile(r"^[✻✶✳✢·✽]\s+\S+ing…", re.MULTILINE),
)
# Рамка ввода claude — пустая каретка на своей строке. Второго шаблона (│ > │)
# здесь нет намеренно: ни один настоящий снимок панели его не даёт, а шаблон
# без снимка — это догадка, которая когда-нибудь совпадёт не с тем.
_IDLE = (re.compile(r"^\s*❯\s*$", re.MULTILINE),)


class State(StrEnum):
    WORKING = "working"
    IDLE = "idle"
    NEEDS_INPUT = "needs_input"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionState:
    state: State
    last_lines: tuple[str, ...]
    listening: tuple[int, ...] = ()


def _tail(pane: str, lines: int) -> tuple[str, ...]:
    rows = [row.rstrip() for row in pane.rstrip("\n").split("\n")]
    return tuple(rows[-lines:]) if lines > 0 else ()


def classify(pane: str) -> State:
    """Порядок важен: диалог рисуется поверх рамки ввода, спиннер — над ней."""
    recent = "\n".join(_tail(pane, _TAIL_FOR_CLASSIFY))
    if any(p.search(recent) for p in _NEEDS_INPUT):
        return State.NEEDS_INPUT
    if any(p.search(recent) for p in _WORKING):
        return State.WORKING
    if any(p.search(recent) for p in _IDLE):
        return State.IDLE
    return State.UNKNOWN


async def _exec(argv: list[str], timeout_s: float = _EXEC_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:  # бинаря нет
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"{argv[0]} не ответил за {timeout_s:.0f}с"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _descendants(root: str) -> list[str]:
    """Обход дерева процессов через `pgrep -P`; порядок узлов не важен."""
    pids, queue = [root], [root]
    while queue:
        code, out = await _exec(["pgrep", "-P", queue.pop()])
        if code != 0:
            continue
        children = out.split()
        pids.extend(children)
        queue.extend(children)
    return pids


def _parse_lsof(out: str) -> tuple[int, ...]:
    ports: set[int] = set()
    for line in out.splitlines():
        if line.startswith("n") and ":" in line:
            tail = line.rsplit(":", 1)[1]
            if tail.isdigit():
                ports.add(int(tail))
    return tuple(sorted(ports))


async def listening_ports(tmux_name: str) -> tuple[int, ...]:
    """TCP-порты, которые слушают процессы панели. Детерминировано: pid → потомки → lsof."""
    code, out = await remote._run(
        "list-panes", "-t", f"={tmux_name}:", "-F", "#{pane_pid}", check=False
    )
    root = out.strip().split("\n")[0] if code == 0 else ""
    if not root.isdigit():
        return ()
    pids = await _descendants(root)
    code, out = await _exec(
        ["lsof", "-a", "-p", ",".join(pids), "-iTCP", "-sTCP:LISTEN", "-P", "-n", "-Fn"]
    )
    # lsof выходит с кодом 1, если хоть один pid из списка уже умер (типичная гонка
    # с pgrep), но всё равно печатает сокеты живых — код игнорируем, кроме «бинаря нет».
    if code == 127:
        return ()
    return _parse_lsof(out)


async def _capture(tmux_name: str) -> tuple[int, str]:
    return await remote._run("capture-pane", "-p", "-J", "-t", f"={tmux_name}:", check=False)


async def classify_pane(tmux_name: str) -> State:
    """Только состояние: один `capture-pane` и разбор, без обхода портов.

    Вопрос «открыт ли в панели диалог» задаётся на каждый тап кнопки, а поиск
    портов из `probe` — это `list-panes`, дерево процессов через `pgrep -P` и
    `lsof`. На сессии с ветвистым деревом он добавляет к отклику кнопки секунды
    и к ответу не относится вовсе.
    """
    code, pane = await _capture(tmux_name)
    return State.DEAD if code != 0 else classify(pane)


async def probe(tmux_name: str, *, lines: int = 5) -> SessionState:
    code, pane = await _capture(tmux_name)
    if code != 0:
        return SessionState(state=State.DEAD, last_lines=())
    return SessionState(
        state=classify(pane),
        last_lines=_tail(pane, lines),
        listening=await listening_ports(tmux_name),
    )
