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

    def _forget(self, cwd: str | None, tmux_id: str | None) -> None:
        """Вычёркивает погашенную сессию из базового снимка.

        Поллер считает смертью исчезновение из снимка — значит убрать сессию из
        снимка и есть точный способ сказать «эта смерть ожидаемая». Метка рядом
        со снимком (по имени, по каталогу, по id) всегда хуже: она переживает
        сессию, а сессия на её месте появляется мгновенно. `restart` гасит и
        поднимает в одном каталоге, и различить их нечем — `#{session_created}`
        идёт целыми секундами, а `#{session_id}` начинает нумерацию заново, если
        гасимая была на сервере единственной и сервер ушёл вместе с ней. Из
        снимка же вычеркнута именно погашенная: поднятой в нём ещё нет, и её
        собственная смерть дойдёт до человека как положено.

        Ключ — tmux-id, когда он известен, иначе каталог (сессия в нём одна).
        """
        if self._known is None or (cwd is None and tmux_id is None):
            return
        self._known = {
            name: s
            for name, s in self._known.items()
            if not (s.tmux_id == tmux_id if tmux_id else same_path(s.cwd, cwd or ""))
        }

    def expect_death(
        self, tmux_name: str, cwd: str | None = None, tmux_id: str | None = None
    ) -> None:
        """Помечает смерть ожидаемой, не гася сессию: её уже погасил кто-то другой."""
        self._expected.add(tmux_name)
        self._forget(cwd, tmux_id or None)

    async def kill(
        self, tmux_name: str, cwd: str | None = None, tmux_id: str | None = None
    ) -> bool:
        """Гасит сессию, пометив смерть ожидаемой.

        `cwd` и `tmux_id` стоит передавать всегда, когда они известны: имя
        сессии меняется под ногами, и метка по имени не совпадёт с тем, что
        поллер снял под прежним. Все вызывающие сессию уже держат — спрашивать
        tmux второй раз незачем.

        Из снимка вычёркиваем только после удачного гашения: `_forget` на живой
        сессии стёр бы из базы то, чему ещё предстоит умереть, и настоящее
        падение прошло бы молча. Метка по имени остаётся страховкой на гонку —
        `poll` мог запросить `list-sessions` до гашения, а разложить ответ по
        снимку уже после, и тогда погашенная сессия попадёт в снимок живой.
        """
        self._expected.add(tmux_name)
        killed = await kill_tmux(tmux_name)
        if not killed:
            # Гашение не удалось — сессия жива, а метка на живой сессии переживёт
            # её и проглотит настоящее падение. Одноразовость важнее лишней карточки.
            self._expected.discard(tmux_name)
            return False
        self._forget(cwd, tmux_id or None)
        return True

    async def kill_all(self) -> int:
        killed = 0
        for session in await list_sessions():
            killed += await self.kill(session.tmux_name, session.cwd, session.tmux_id)
        return killed

    async def poll(self, on_died: OnDied) -> None:
        current = {s.tmux_name: s for s in await list_sessions()}
        previous, self._known = self._known, current

        # Метка по имени живёт, только пока жива сессия: иначе неудавшееся гашение
        # оставило бы вечное «не сообщать», и настоящее падение прошло бы молча.
        expected_gone = self._expected - set(current)
        self._expected &= set(current)

        # previous is None — первый снимок базовый: что бы в нём ни было,
        # падений ещё не видели.
        for tmux_name, session in (previous or {}).items():
            if tmux_name in current or tmux_name in expected_gone:
                continue
            # Имя пропало, но тот же экземпляр жив — значит сессию
            # переименовали, а не потеряли: `await_url` даёт ей id сессии
            # Claude, как только тот появится. Сверяем tmux-id, а не время
            # создания: `#{session_created}` — целые секунды, и перезапуск в ту
            # же секунду выглядел бы переименованием, то есть настоящая смерть
            # прошла бы молча. Переименование `$N` сохраняет, перезапуск — нет.
            if session.tmux_id and any(
                alive.tmux_id == session.tmux_id for alive in current.values()
            ):
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
