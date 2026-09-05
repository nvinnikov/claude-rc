"""RC-сессии Claude Code, живущие в tmux.

Сессия принадлежит tmux, а не боту: рестарт бота её не гасит, и подсесть к ней
можно с любой машины — `tmux attach -d -t =<id>`, где id тот же `session_…`, что
в ссылке: `await_url` переименовывает сессию в него, как только ссылка появилась.

`claude --remote-control` — интерактивная команда: без tty она уходит в режим
`--print` и падает. Панель tmux даёт tty, а заодно переживает нас.

Переменная `CLAUDE_RC_TMUX_SOCKET` переключает весь модуль на отдельный
tmux-сервер (`tmux -L <имя>`), полностью изолированный от сервера по
умолчанию — тот, где живут рабочие `rc-*` сессии. Полезно не только тестам:
так можно поднять песочницу, не рискуя случайно задеть боевые сессии.
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
# Режимы прав, которые понимает claude. Проверяем на своей стороне, чтобы
# опечатка называлась при чтении конфига, а не гасила сессию при старте.
#
# Список списан с `claude --help`, а не с документации: `default` — имя ручного
# режима для `permissions.defaultMode` в settings.json, но `--permission-mode`
# его не принимает и с ним не стартует. В примерах документации он при этом
# встречается, поэтому соблазн внести его сюда прямой — и он бы ровно ту ошибку
# и пропустил, ради предотвращения которой список заведён. Ручной режим здесь
# зовётся `manual`.
PERMISSION_MODES = (
    "manual",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
)
# Имя отдельного tmux-сервера (`tmux -L <имя>`). Пустая/отсутствующая — сервер
# по умолчанию, тот же, где живут рабочие сессии.
TMUX_SOCKET_ENV = "CLAUDE_RC_TMUX_SOCKET"
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
# Номер пункта «доверяю» в списке. Диалог печатает его сам, и это единственное,
# на что можно опереться: какой пункт подсвечен по умолчанию, решает claude.
_TRUST_CHOICE = re.compile(r"(\d+)[.)]\s*Yes, I trust this folder")
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
    """Сессию не удалось поднять или tmux ответил ошибкой.

    `tmux_name` заполняется, если о смерти сессии уже можно судить по этому вызову —
    watcher её видел живой и должен считать смерть ожидаемой, а не отчитываться о ней
    как о падении второй раз. Не только для гашения по таймауту: то же верно, если
    сессия завершилась сама или исчезла между capture-pane и list-sessions.
    """

    def __init__(self, message: str, *, tmux_name: str | None = None) -> None:
        super().__init__(message)
        self.tmux_name = tmux_name


class TrustRequired(RuntimeError):
    """claude ждёт подтверждения доверия каталогу. Сессия жива и держит вопрос."""

    def __init__(self, tmux_name: str, cwd: str) -> None:
        super().__init__(f"каталог {cwd} требует подтверждения доверия")
        self.tmux_name = tmux_name
        self.cwd = cwd


def attach_argv(tmux_name: str) -> list[str]:
    """Аргументы подсадки к сессии.

    CLAUDE_RC_TMUX_SOCKET переключает весь модуль на отдельный сервер (см.
    модульный докстринг); без `-L` подсадка пришла бы на сервер по умолчанию,
    где этой сессии нет — особенно заметно в песочнице, где рабочий сервер пуст.
    """
    socket = os.environ.get(TMUX_SOCKET_ENV)
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    # `-d` отцепляет прочих tmux-клиентов. Панель создаётся _COLS x _ROWS без
    # клиента, и второй клиент с узким окном ужал бы её всем сразу — TUI
    # переверстался бы под руками у того, кто работает прямо сейчас. Приложения
    # Claude это не касается: оно говорит с сессией через API, а не через tmux.
    # `=` — точное совпадение: без него `rc-oms` рискует поймать `rc-oms-2`.
    argv += ["attach", "-d", "-t", f"={tmux_name}"]
    return argv


def attach_command(tmux_name: str) -> str:
    """Команда подсадки одной строкой."""
    return shlex.join(attach_argv(tmux_name))


@dataclass(frozen=True)
class RemoteSession:
    name: str  # имя репозитория, как его видит пользователь
    tmux_name: str
    cwd: str
    url: str
    created_at: int  # unix-время создания tmux-сессии

    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.created_at)


def session_name(repo: str) -> str:
    """Имя tmux-сессии. tmux не принимает `.` и `:`, поэтому чистим."""
    return PREFIX + (_UNSAFE.sub("-", repo).strip("-") or "session")


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


async def _run(*args: str, check: bool = True) -> tuple[int, str]:
    socket = os.environ.get(TMUX_SOCKET_ENV)
    socket_flag = ("-L", socket) if socket else ()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *socket_flag,
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
        # Наши сессии — либо ещё не переименованные (префикс), либо уже
        # названные id сессии Claude (тогда имя ни о чём не говорит, но
        # `@rc_url` выставлен). Одного признака мало: по префиксу не видно
        # переименованных, а по опции — тех, кто умер, не дождавшись ссылки.
        if not tmux_name.startswith(PREFIX) and not url:
            continue  # чужие tmux-сессии не трогаем
        sessions.append(
            RemoteSession(
                name=_display_name(path, tmux_name),
                tmux_name=tmux_name,
                cwd=path,
                url=url,
                created_at=int(created) if created.isdigit() else 0,
            )
        )
    return sorted(sessions, key=lambda s: s.name)


def _display_name(cwd: str, tmux_name: str) -> str:
    """Как сессию зовут в карточке. Имя tmux после переименования — id сессии
    Claude, и человеку он не говорит ничего; каталог говорит.
    """
    return os.path.basename(cwd.rstrip(os.sep)) or tmux_name.removeprefix(PREFIX)


def same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


async def resolve(target: str) -> list[RemoteSession]:
    """Сессии, к которым относится строка от человека: id, имя или каталог.

    Список, а не одна: имя каталога в дереве не уникально (два клона одного
    репо), и «погасить oms», когда их три, — не то, что можно решить за
    человека. Уникален только id, поэтому им и различают.
    """
    wanted = target.strip()
    if not wanted:
        return []
    sessions = await list_sessions()
    exact = [s for s in sessions if s.tmux_name == wanted]
    if exact:
        return exact
    # Тильду разворачиваем здесь, а не у каждого вызывающего: `realpath` её не
    # трогает, и `~/code/oms` из сообщения не совпал бы ни с чем. Строке без
    # тильды `expanduser` ничего не делает, так что имени это не мешает.
    path = os.path.expanduser(wanted)
    return [s for s in sessions if s.name == wanted or same_path(s.cwd, path)]


async def find(cwd: str) -> RemoteSession | None:
    """Сессия по рабочему каталогу.

    Каталог — единственный надёжный ключ: имя репозитория может повторяться в разных
    местах дерева (два клона одного репо), и поиск по имени вернул бы ссылку на сессию
    в чужом каталоге.
    """
    for session in await list_sessions():
        if same_path(session.cwd, cwd):
            return session
    return None


async def _unique_name(repo: str, cwd: str) -> str:
    """Свободное имя сессии: базовое, а при коллизии — с родительскими каталогами.

    Имя сессии человек видит в карточке и набирает сам — в `/rckill` и в
    `tmux attach`. Хеш пути уникальность давал, но человеку не говорил ничего:
    `oms-a1b2c3` и `oms-9f8e7d` различимы только по строке пути рядом с ними.
    Родительский каталог различает их сам, поэтому в имя добавляется он, и
    ровно столько уровней, сколько нужно для уникальности.
    """
    base = session_name(repo)
    taken = {s.tmux_name for s in await list_sessions()}
    if base not in taken:
        return base

    parts = [part for part in os.path.realpath(cwd).split(os.sep) if part]
    for depth in range(2, len(parts) + 1):
        candidate = session_name("-".join(parts[-depth:]))
        if candidate not in taken:
            return candidate

    # Сюда попадают только пути, совпавшие целиком: занятый каталог и симлинк
    # на него. Живая сессия в том же каталоге до `_unique_name` не доходит —
    # `launch` возвращает её раньше.
    tag = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:6]
    return f"{base}-{tag}"


async def kill_tmux(tmux_name: str) -> bool:
    code, _ = await _run("kill-session", "-t", f"={tmux_name}", check=False)
    return code == 0


async def confirm_trust(tmux_name: str) -> None:
    """Подтверждает диалог доверия каталогу, выбирая пункт по его номеру.

    Слепой Enter подтверждает не «доверяю», а тот пункт, который подсвечен
    сейчас, — а какой подсвечен, решает claude, и это уже менялось. Подсвеченным
    оказался отказ, отказ означает выход claude, выход — конец tmux-сессии, и
    человек получал «сессия завершилась, не отдав ссылку» вместо ответа на свой
    же тап. Номер печатает сам диалог, поэтому опираемся на него.

    Enter следом безвреден в обоих случаях: список, который подтверждается
    номером сразу, к этому моменту уже закрыт, а пустой Enter в поле ввода
    claude игнорирует. Не нашли номера — остаётся прежнее поведение, лучше
    попытаться, чем не ответить вовсе.
    """
    code, pane = await _run("capture-pane", "-p", "-J", "-t", f"={tmux_name}:", check=False)
    choice = _TRUST_CHOICE.search(pane) if code == 0 else None
    if choice is not None:
        await _run("send-keys", "-t", f"={tmux_name}:", choice.group(1), check=False)
    await _run("send-keys", "-t", f"={tmux_name}:", "Enter", check=False)


async def kill_all() -> int:
    killed = 0
    for session in await list_sessions():
        code, _ = await _run("kill-session", "-t", f"={session.tmux_name}", check=False)
        killed += code == 0
    return killed


def _permission_flag(mode: str | None) -> str:
    """Хвост командной строки для режима прав.

    Права выдаются в приложении Claude, и гейта на нашей стороне не появляется:
    флаг только говорит claude, с чем начинать, чтобы сессия не останавливалась
    на каждом шаге у человека, который сейчас с телефоном в руках.
    """
    return f" --permission-mode {shlex.quote(mode)}" if mode else ""


def _resume_flag(resume: str | None) -> str:
    """Хвост командной строки для резюма диалога."""
    if resume is None:
        return ""
    if resume == "last":
        return " --continue"
    return f" --resume {shlex.quote(resume)}"


async def launch(
    repo: str,
    cwd: str,
    *,
    timeout_s: float = 90.0,
    resume: str | None = None,
    permission_mode: str | None = None,
) -> RemoteSession:
    """Поднимает RC-сессию в `cwd` и ждёт, пока claude напечатает ссылку.

    `resume` продолжает прежний диалог (`"last"` — последний, id — конкретный);
    проверка на уже живую сессию в `cwd` его не отменяет — сессия одна на каталог.

    `permission_mode` — с какими правами начинать (см. `PERMISSION_MODES`).
    """
    if not tmux_available():
        raise LaunchError("tmux не найден в PATH — поставь через `brew install tmux`")

    existing = await find(cwd)
    if existing is not None:
        return existing

    name = await _unique_name(repo, cwd)
    command = (
        _SCRUB_ENV
        + f"exec {shlex.quote(CLAUDE_BIN)} --remote-control {shlex.quote(repo)}"
        + _permission_flag(permission_mode)
        + _resume_flag(resume)
    )
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
    # Держим последнюю удачную панель, а не вывод неудачного capture: когда
    # сессия исчезла, tmux печатает «can't find session», и раньше именно это
    # уезжало человеку вместо того, что было на экране перед смертью, — то есть
    # вместо единственной подсказки, почему всё кончилось.
    pane = ""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_S)
        code, captured = await _run("capture-pane", "-p", "-J", "-t", f"={name}:", check=False)
        if code != 0:
            raise LaunchError(_failure("сессия завершилась, не отдав ссылку", pane), tmux_name=name)
        pane = captured
        match = _URL.search(pane)
        if match is None:
            if watch_trust and _TRUST_PROMPT.search(pane):
                raise TrustRequired(name, cwd)
            continue

        url = match.group(0)
        stored, why = await _run("set-option", "-t", f"={name}:", _URL_OPTION, url, check=False)
        if stored != 0:
            # Не фатально — сессия жива и работает, — но после рестарта бота
            # ссылку взять будет неоткуда, и переименования не будет тоже.
            log.warning("set %s on %s failed: %s", _URL_OPTION, name, why.strip())
        else:
            # Переименование только после сохранённой ссылки: своих `list_sessions`
            # узнаёт по префиксу `rc-` или по `@rc_url`, и сессия без обоих признаков
            # стала бы невидимой — живой процесс, до которого не дотянуться.
            name = await _rename_to_session_id(name, url)
        session = await find(cwd)
        if session is None:  # успела умереть между capture и list
            raise LaunchError(_failure("сессия исчезла сразу после запуска", pane), tmux_name=name)
        log.info("rc session %s up: %s", name, url)
        return session

    await _run("kill-session", "-t", f"={name}", check=False)
    raise LaunchError(_failure(f"ссылка не появилась за {int(timeout_s)}с", pane), tmux_name=name)


async def _rename_to_session_id(name: str, url: str) -> str:
    """Переименовывает tmux-сессию в id сессии Claude из ссылки.

    Смысл — один идентификатор на обе поверхности: тот же `session_...`, что
    человек видит в приложении, годится как цель `tmux attach -t =<id>`. Раньше
    цель приходилось выяснять отдельно, и при совпадении имён каталогов она ещё
    и обрастала различающим хвостом.

    Раньше ссылки имени взяться неоткуда: id выдаёт сервер, а не мы, и до
    первой печати в панели его не существует. Отсюда переименование по факту,
    а не имя сразу.

    Неудача не фатальна: сессия жива и работает под прежним именем, `@rc_url`
    уже выставлен, и `find(cwd)` найдёт её в любом случае.
    """
    session_id = url.rsplit("/", 1)[-1]
    if session_id == name:
        return name
    code, out = await _run("rename-session", "-t", f"={name}", session_id, check=False)
    if code != 0:
        log.warning("rename %s -> %s failed: %s", name, session_id, out.strip())
        return name
    return session_id


def _failure(reason: str, pane: str) -> str:
    tail = pane.strip()
    return f"{reason}\n{tail[-_TAIL_CHARS:]}" if tail else reason
