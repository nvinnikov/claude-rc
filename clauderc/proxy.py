"""`claude-rc --host m1 …` — та же команда, выполненная на другой машине.

Транспорт — ssh и только он: аутентификация, шифрование и ключи у него уже
есть. Процесс замещается через execvp: коды возврата, stdout и Ctrl+C
достаются ssh без нашего посредничества. Никакой роли «управляющий» у машины
нет — любая с claude-rc может звать любую другую.
"""

from __future__ import annotations

import os as os  # тесты подменяют proxy.os.execvp
import shlex
import subprocess as subprocess  # тесты подменяют proxy.subprocess.run

HOST_ENV = "CLAUDE_RC_HOST"
# bot — умер бы вместе с ssh-сессией; update — гасит приложение на той машине
# и должен идти из её Терминала; forward — исполняется здесь по определению.
LOCAL_ONLY = frozenset({"bot", "forward", "update"})
# Команды, где человек отвечает на вопрос в терминале.
TTY_COMMANDS = frozenset({"connect", "start", "setup"})
# Команды с путём в позиционном аргументе: относительный путь означал бы
# каталог этой машины, а исполняется команда на той.
PATH_COMMANDS = frozenset({"start", "whoami", "sync", "connect"})
# Опции этих команд, у которых есть значение — чтобы не принять его за путь.
_VALUED_OPTIONS = frozenset({"--branch", "--resume", "--permission-mode", "--name", "--mode"})
# Неинтерактивный ssh не читает .zshrc; uv tool кладёт бинарь в ~/.local/bin.
_PATH_PREFIX = 'export PATH="$HOME/.local/bin:$PATH"; '


class HostError(ValueError):
    """`--host` дан без имени хоста — не «выполнить локально», а ошибка."""


def strip_host(argv: list[str]) -> tuple[str | None, list[str]]:
    host: str | None = None
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--":
            # Всё после одиночного «--» — аргументы самой команды, а не наши
            # опции: `send oms -- --host` посылает в сессию текст «--host»,
            # а не требует имя машины, которого там нет.
            rest.append(arg)
            rest.extend(it)
            break
        if arg == "--host":
            value = next(it, None)
            if not value or value.startswith("-"):
                raise HostError("--host требует имя хоста")
            host = value
        elif arg.startswith("--host="):
            value = arg.removeprefix("--host=")
            if not value:
                raise HostError("--host требует имя хоста")
            host = value
        else:
            rest.append(arg)
    return host, rest


def command_of(argv: list[str]) -> str | None:
    return next((a for a in argv if not a.startswith("-")), None)


def _looks_like_path(arg: str) -> bool:
    """Похоже ли на путь то, что может быть и ярлыком, и id сессии.

    У `connect` позиционный аргумент — цель в широком смысле: `oms@x`,
    `session_01A` или каталог. Отличаем каталог по разделителю или по явным
    `.`/`..`. Но одного разделителя мало: канонический ярлык worktree-сессии —
    `oms@wt/feature-x`, слэш в нём от имени ветки. `@` в ярлыке есть всегда, а в
    пути (`code/oms`, `./x`) — нет, поэтому он и решает.
    """
    return "@" not in arg and (os.sep in arg or arg in (".", ".."))


def relative_paths(argv: list[str]) -> list[str]:
    command = command_of(argv)
    if command not in PATH_COMMANDS:
        return []
    positionals: list[str] = []
    options: list[str] = []
    skip = False
    for arg in argv[argv.index(command) + 1 :]:
        if skip:
            skip = False
            continue
        if arg in _VALUED_OPTIONS:
            skip = True
            continue
        if arg.startswith("-"):
            options.append(arg)
            continue
        positionals.append(arg)
    if command == "connect":
        # Цель, не похожая на путь, — ярлык или session_…: её резолвит та
        # сторона, и относительной она не бывает. Пустая цель означает каталог
        # только вместе с `--start`; иначе это «единственная живая сессия».
        # Пустоту считаем до фильтрации: отфильтрованный ярлык — это заданная
        # цель, а не отсутствующая, и подменять его точкой нельзя.
        had_target = bool(positionals)
        positionals = [p for p in positionals if _looks_like_path(p)]
        if not had_target and "--start" in options:
            positionals = ["."]
    elif not positionals and command != "sync":
        positionals = ["."]
    return [p for p in positionals if not (os.path.isabs(p) or p.startswith("~"))]


def remote_argv(host: str, args: list[str], *, tty: bool) -> list[str]:
    command = _PATH_PREFIX + shlex.join(["claude-rc", *args])
    # "--" перед host: хост, начинающийся с "-", не должен читаться как опция ssh.
    return ["ssh", "-t" if tty else "-T", "--", host, command]


def exec_remote(host: str, args: list[str]) -> None:
    argv = remote_argv(host, args, tty=command_of(args) in TTY_COMMANDS)
    os.execvp(argv[0], argv)


def run_remote(host: str, args: list[str], *, timeout_s: float = 30.0) -> tuple[int, str]:
    """Выполнить и вернуть вывод — для forward, которому нужен sessions --json той стороны."""
    argv = remote_argv(host, args, tty=False)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return 1, f"ssh {host} не ответил за {timeout_s:.0f}с"
    return done.returncode, done.stdout if done.returncode == 0 else done.stderr
