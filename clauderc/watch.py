"""Слежение за исчезновением RC-сессий.

События от tmux нам недоступны, а `exec` в панели означает, что упавший claude
уносит tmux-сессию целиком. Значит единственный надёжный признак смерти —
исчезновение из `list_sessions()`, и замечать его приходится опросом.

Гашение проходит через Watcher, а не через remote напрямую: иначе намеренно
убитая сессия попала бы в отчёт как упавшая, и отметить её было бы негде.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from clauderc.remote import RemoteSession, kill_tmux, list_sessions, session_name

log = logging.getLogger("clauderc.watch")

# Заметно больше _POLL_S из remote: сессии не пропадают каждую секунду,
# а лишний опрос tmux — лишний процесс.
POLL_S = 15.0

OnDied = Callable[["Died"], Awaitable[None]]


@dataclass(frozen=True)
class Died:
    name: str
    tmux_name: str
    cwd: str


class Watcher:
    """Сравнивает снимки живых сессий и сообщает о тех, что исчезли не по нашей воле."""

    def __init__(self, *, poll_s: float = POLL_S) -> None:
        self._poll_s = poll_s
        self._known: dict[str, RemoteSession] | None = None
        self._expected: set[str] = set()

    def expect_death(self, tmux_name: str) -> None:
        """Помечает смерть ожидаемой, не гася сессию: её уже погасил кто-то другой."""
        self._expected.add(tmux_name)

    async def kill(self, tmux_name: str) -> bool:
        self.expect_death(tmux_name)
        killed = await kill_tmux(tmux_name)
        if not killed:
            # Гашение не удалось — сессия жива, а метка на живой сессии переживёт
            # её и проглотит настоящее падение. Одноразовость важнее лишней карточки.
            self._expected.discard(tmux_name)
        return killed

    async def kill_named(self, repo: str) -> bool:
        return await self.kill(session_name(repo))

    async def kill_all(self) -> int:
        killed = 0
        for session in await list_sessions():
            killed += await self.kill(session.tmux_name)
        return killed

    async def poll(self, on_died: OnDied) -> None:
        current = {s.tmux_name: s for s in await list_sessions()}
        previous, self._known = self._known, current

        # Метки живут только пока жива сессия: иначе неудавшееся гашение оставило бы
        # вечное «не сообщать», и настоящее падение прошло бы молча.
        expected_gone = self._expected - set(current)
        self._expected &= set(current)

        if previous is None:
            return  # первый снимок базовый: что бы в нём ни было, падений ещё не видели

        for tmux_name, session in previous.items():
            if tmux_name in current or tmux_name in expected_gone:
                continue
            await on_died(Died(name=session.name, tmux_name=tmux_name, cwd=session.cwd))

    async def run(self, on_died: OnDied) -> None:
        while True:
            try:
                await self.poll(on_died)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watch poll failed")
            await asyncio.sleep(self._poll_s)
