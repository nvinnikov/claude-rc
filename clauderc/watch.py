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

from clauderc.remote import RemoteSession, kill_tmux, list_sessions, same_path

log = logging.getLogger("clauderc.watch")

# Заметно больше _POLL_S из remote: сессии не пропадают каждую секунду,
# а лишний опрос tmux — лишний процесс.
POLL_S = 15.0

OnDied = Callable[["Died"], Awaitable[None]]

# Метка ожидаемой смерти: каталог сессии и время её создания. Каталог — потому
# что имя меняется у сессии под ногами (`await_url` даёт ей id Claude), и
# снятая поллером под прежним именем сессия не совпала бы с меткой,
# поставленной под новым. Время создания — потому что каталога мало: `restart`
# поднимает в том же каталоге новую сессию через миллисекунды, и метка «по
# каталогу» пережила бы её и проглотила первое настоящее падение уже
# перезапущенной. `None` вместо времени — «экземпляр неизвестен» (сессия не
# дожила до ссылки, спросить её `created_at` не у кого): такая метка работает
# по каталогу, как раньше.
Mark = tuple[str, int | None]


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
        self._marks: set[Mark] = set()

    def expect_death(
        self, tmux_name: str, cwd: str | None = None, created_at: int | None = None
    ) -> None:
        """Помечает смерть ожидаемой, не гася сессию: её уже погасил кто-то другой."""
        self._expected.add(tmux_name)
        if cwd is not None:
            self._marks.add((cwd, created_at))

    async def kill(
        self, tmux_name: str, cwd: str | None = None, created_at: int | None = None
    ) -> bool:
        """Гасит сессию, пометив смерть ожидаемой.

        `cwd` и `created_at` стоит передавать всегда, когда они известны: имя
        сессии меняется под ногами, а один каталог не отличает погашенную
        сессию от поднятой на её месте (см. `Mark`). Все вызывающие сессию уже
        держат — спрашивать tmux второй раз незачем.
        """
        self.expect_death(tmux_name, cwd, created_at)
        killed = await kill_tmux(tmux_name)
        if not killed:
            # Гашение не удалось — сессия жива, а метка на живой сессии переживёт
            # её и проглотит настоящее падение. Одноразовость важнее лишней карточки.
            self._expected.discard(tmux_name)
            if cwd is not None:
                self._marks.discard((cwd, created_at))
        return killed

    async def kill_all(self) -> int:
        killed = 0
        for session in await list_sessions():
            killed += await self.kill(session.tmux_name, session.cwd, session.created_at)
        return killed

    @staticmethod
    def _matches(mark: Mark, session: RemoteSession) -> bool:
        path, instance = mark
        return same_path(path, session.cwd) and instance in (None, session.created_at)

    def _take_mark(self, session: RemoteSession) -> bool:
        """Снимает метку, если она про эту сессию. Метка одноразовая."""
        found = next((m for m in self._marks if self._matches(m, session)), None)
        if found is None:
            return False
        self._marks.discard(found)
        return True

    async def poll(self, on_died: OnDied) -> None:
        current = {s.tmux_name: s for s in await list_sessions()}
        previous, self._known = self._known, current

        # Метки живут только пока жива сессия: иначе неудавшееся гашение оставило бы
        # вечное «не сообщать», и настоящее падение прошло бы молча.
        expected_gone = self._expected - set(current)
        self._expected &= set(current)

        # previous is None — первый снимок базовый: что бы в нём ни было,
        # падений ещё не видели.
        for tmux_name, session in (previous or {}).items():
            if tmux_name in current:
                continue
            # Имя пропало, но сессия в том же каталоге и той же давности жива —
            # значит её переименовали, а не потеряли: `await_url` даёт ей id
            # сессии Claude, как только тот появится. Одного каталога мало:
            # упавшую сессию могли тут же поднять заново, и настоящая смерть
            # прошла бы молча. `session_created` переименование сохраняет,
            # а перезапуск — нет.
            if any(
                same_path(alive.cwd, session.cwd) and alive.created_at == session.created_at
                for alive in current.values()
            ):
                continue
            if tmux_name in expected_gone or self._take_mark(session):
                continue  # погасили намеренно, пусть и под другим именем
            await on_died(Died(name=session.name, tmux_name=tmux_name, cwd=session.cwd))

        # Тот же расчёт одноразовости, что у меток по имени, но после разбора
        # снимка: метка, погасившая отчёт, уже снята выше, а из оставшихся
        # выживают только те, чей экземпляр ещё жив.
        self._marks = {
            mark
            for mark in self._marks
            if any(self._matches(mark, alive) for alive in current.values())
        }

    async def run(self, on_died: OnDied) -> None:
        while True:
            try:
                await self.poll(on_died)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watch poll failed")
            await asyncio.sleep(self._poll_s)
