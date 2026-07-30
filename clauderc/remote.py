"""RC-сессии Claude Code, живущие в tmux.

Сессия принадлежит tmux, а не боту: рестарт бота её не гасит, и с самой машины
к ней можно подсесть через `tmux attach -t rc-<имя>`.

`claude --remote-control` — интерактивная команда: без tty она уходит в режим
`--print` и падает. Панель tmux даёт tty, а заодно переживает нас.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass

log = logging.getLogger("clauderc.remote")

PREFIX = "rc-"
CLAUDE_BIN = "claude"
# Ширину/высоту задаём явно: у панели без клиента размер по умолчанию мелкий,
# и TUI переносит строки так, что ссылка рвётся пополам.
_COLS, _ROWS = "120", "40"
_URL = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")
# В незнакомом каталоге (а свежий worktree всегда незнакомый) claude сначала
# спрашивает, доверяем ли мы папке, и до ответа ссылку не печатает. Пропустить
# диалог можно только в `--print`, а Remote Control требует интерактива, так что
# отвечать приходится в панель. Решение оставляем человеку: наткнувшись на диалог,
# поднимаем TrustRequired, а Enter шлём только после явного подтверждения.
_TRUST_PROMPT = re.compile(r"Yes, I trust this folder")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_POLL_S = 0.7
_TAIL_CHARS = 400

# Ссылку кладём в user-опцию сессии: TUI перерисовывает панель и вытирает её
# из видимого буфера, а пережить рестарт бота она должна.
_URL_OPTION = "@rc_url"
_FORMAT = "#{session_name}\t#{session_path}\t#{session_created}\t#{@rc_url}"

# CLAUDE_CODE_* чистим уже внутри панели: tmux-сервер мог быть поднят из-под
# Claude Code, и унаследованный CLAUDE_CODE_CHILD_SESSION выключит сохранение
# транскрипта в новой сессии.
_SCRUB_ENV = 'for v in $(env | grep ^CLAUDE_CODE_ | cut -d= -f1); do unset "$v"; done; '


class LaunchError(RuntimeError):
    """Сессию не удалось поднять или tmux ответил ошибкой."""


class TrustRequired(RuntimeError):
    """claude ждёт подтверждения доверия каталогу. Сессия жива и держит вопрос."""

    def __init__(self, tmux_name: str, cwd: str) -> None:
        super().__init__(f"каталог {cwd} требует подтверждения доверия")
        self.tmux_name = tmux_name
        self.cwd = cwd


@dataclass(frozen=True)
class RemoteSession:
    name: str  # имя репозитория, как его видит пользователь
    tmux_name: str
    cwd: str
    url: str
    created_at: int  # unix-время создания tmux-сессии

    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.created_at)

    @property
    def attach_hint(self) -> str:
        return f"tmux attach -t {self.tmux_name}"


def session_name(repo: str) -> str:
    """Имя tmux-сессии. tmux не принимает `.` и `:`, поэтому чистим."""
    return PREFIX + (_UNSAFE.sub("-", repo).strip("-") or "session")


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


async def _run(*args: str, check: bool = True) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace")
    code = proc.returncode or 0
    if check and code != 0:
        raise LaunchError(f"tmux {args[0]}: {text.strip() or f'код {code}'}")
    return code, text


async def list_sessions() -> list[RemoteSession]:
    """Живые RC-сессии. Сервер не поднят — значит их просто нет."""
    code, out = await _run("list-sessions", "-F", _FORMAT, check=False)
    if code != 0:
        return []

    sessions: list[RemoteSession] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        tmux_name, path, created, url = parts
        if not tmux_name.startswith(PREFIX):
            continue  # чужие tmux-сессии не трогаем
        sessions.append(
            RemoteSession(
                name=tmux_name[len(PREFIX) :],
                tmux_name=tmux_name,
                cwd=path,
                url=url,
                created_at=int(created) if created.isdigit() else 0,
            )
        )
    return sorted(sessions, key=lambda s: s.name)


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


async def get_session(repo: str) -> RemoteSession | None:
    """Сессия по видимому имени — то, что показывает `/rc` и принимает `/rckill`."""
    name = session_name(repo)
    for session in await list_sessions():
        if session.tmux_name == name:
            return session
    return None


async def find(cwd: str) -> RemoteSession | None:
    """Сессия по рабочему каталогу.

    Каталог — единственный надёжный ключ: имя репозитория может повторяться в разных
    местах дерева (два клона одного репо), и поиск по имени вернул бы ссылку на сессию
    в чужом каталоге.
    """
    for session in await list_sessions():
        if _same_path(session.cwd, cwd):
            return session
    return None


async def _unique_name(repo: str, cwd: str) -> str:
    """Свободное имя сессии: базовое, а при коллизии — с хвостом от пути."""
    base = session_name(repo)
    taken = {s.tmux_name for s in await list_sessions()}
    if base not in taken:
        return base
    tag = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:6]
    return f"{base}-{tag}"


async def kill_tmux(tmux_name: str) -> bool:
    code, _ = await _run("kill-session", "-t", f"={tmux_name}", check=False)
    return code == 0


async def kill_session(repo: str) -> bool:
    return await kill_tmux(session_name(repo))


async def confirm_trust(tmux_name: str) -> None:
    """Подтверждает диалог доверия каталогу: выбор по умолчанию — «доверяю»."""
    await _run("send-keys", "-t", f"={tmux_name}:", "Enter", check=False)


async def kill_all() -> int:
    killed = 0
    for session in await list_sessions():
        code, _ = await _run("kill-session", "-t", f"={session.tmux_name}", check=False)
        killed += code == 0
    return killed


async def launch(repo: str, cwd: str, *, timeout_s: float = 90.0) -> RemoteSession:
    """Поднимает RC-сессию в `cwd` и ждёт, пока claude напечатает ссылку."""
    if not tmux_available():
        raise LaunchError("tmux не найден в PATH — поставь через `brew install tmux`")

    existing = await find(cwd)
    if existing is not None:
        return existing

    name = await _unique_name(repo, cwd)
    command = _SCRUB_ENV + f"exec {shlex.quote(CLAUDE_BIN)} --remote-control {shlex.quote(repo)}"
    await _run("new-session", "-d", "-s", name, "-x", _COLS, "-y", _ROWS, "-c", cwd, command)
    return await await_url(name, cwd, timeout_s=timeout_s)


async def await_url(
    name: str, cwd: str, *, timeout_s: float = 90.0, watch_trust: bool = True
) -> RemoteSession:
    """Ждёт ссылку в панели уже поднятой сессии.

    Наткнувшись на диалог доверия, поднимает `TrustRequired` и оставляет сессию
    живой: ответить за пользователя мы не вправе, но и терять запущенный процесс
    незачем — после подтверждения ожидание продолжится с `watch_trust=False`.
    """
    pane = ""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_S)
        code, pane = await _run("capture-pane", "-p", "-J", "-t", f"={name}:", check=False)
        if code != 0:
            raise LaunchError(_failure("сессия завершилась, не отдав ссылку", pane))
        match = _URL.search(pane)
        if match is None:
            if watch_trust and _TRUST_PROMPT.search(pane):
                raise TrustRequired(name, cwd)
            continue

        url = match.group(0)
        await _run("set-option", "-t", f"={name}:", _URL_OPTION, url, check=False)
        session = await find(cwd)
        if session is None:  # успела умереть между capture и list
            raise LaunchError(_failure("сессия исчезла сразу после запуска", pane))
        log.info("rc session %s up: %s", name, url)
        return session

    await _run("kill-session", "-t", f"={name}", check=False)
    raise LaunchError(_failure(f"ссылка не появилась за {int(timeout_s)}с", pane))


def _failure(reason: str, pane: str) -> str:
    tail = pane.strip()
    return f"{reason}\n{tail[-_TAIL_CHARS:]}" if tail else reason
