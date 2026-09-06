"""Состояние сессии, каким его видно на панели.

У tmux нет событий, у claude нет API статуса сессии. Единственный источник —
последние строки панели, поэтому всё здесь — наблюдение, а не знание, и
рядом всегда отдаётся сырой хвост: читатель должен иметь возможность не
поверить ярлыку. Шаблон, сломанный обновлением claude, даёт UNKNOWN, а не
ложный IDLE; на каждое состояние есть тест с настоящим снимком панели.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from clauderc import remote

_TAIL_FOR_CLASSIFY = 12

_NEEDS_INPUT = (
    re.compile(r"Yes, I trust this folder"),
    re.compile(r"Do you want to"),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"^\s*❯?\s*\d+[.)]\s+\S", re.MULTILINE),
)
_WORKING = (
    re.compile(r"esc to interrupt"),
    re.compile(r"^[✻✶✳✢·✽]\s+\S+ing…", re.MULTILINE),
)
_IDLE = (
    re.compile(r"^\s*❯\s*$", re.MULTILINE),
    re.compile(r"^\s*│\s*>\s*│?\s*$", re.MULTILINE),
)


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


async def listening_ports(tmux_name: str) -> tuple[int, ...]:
    return ()  # задача 9


async def probe(tmux_name: str, *, lines: int = 5) -> SessionState:
    code, pane = await remote._run("capture-pane", "-p", "-J", "-t", f"={tmux_name}:", check=False)
    if code != 0:
        return SessionState(state=State.DEAD, last_lines=())
    return SessionState(
        state=classify(pane),
        last_lines=_tail(pane, lines),
        listening=await listening_ports(tmux_name),
    )
