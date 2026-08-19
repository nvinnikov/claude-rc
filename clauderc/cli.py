"""Команда `claude-rc` — тот же набор действий, что у бота, но из терминала.

Всё, кроме разбора аргументов и печати, живёт в модулях ядра: CLI и бот — две
равноправные морды над одним кодом, а не копия логики.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass as getpass  # тесты подменяют cli.getpass.getpass — см. ниже
import json
import os as os  # тесты подменяют cli.os.path.lexists — реэкспорт для mypy --strict
import shutil as shutil  # тесты подменяют cli.shutil/cli.sys.stdin — реэкспорт для mypy --strict
import subprocess
import sys as sys
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from clauderc import paths as paths  # тесты подменяют cli.paths.config_file — см. выше
from clauderc import setup as setup  # тесты подменяют cli.setup.verify_token/catch_user_id
from clauderc import worktrees as worktrees  # тесты подменяют cli.worktrees.ensure
from clauderc.config import load_config as load_config  # тесты читают cli.load_config
from clauderc.remote import (
    LaunchError,
    RemoteSession,
    TrustRequired,
    await_url,
    confirm_trust,
    kill_all,
    kill_tmux,
    launch,
    list_sessions,
)
from clauderc.worktrees import WorktreeError

# Коды возврата: 1 — не получилось сделать, 2 — не с чем работать.
EXIT_FAILED = 1
EXIT_ENVIRONMENT = 2


def run() -> None:
    """Точка входа консольного скрипта."""
    sys.exit(main())


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return EXIT_ENVIRONMENT

    handler: Any = getattr(_Commands, args.command)
    return int(handler(args))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-rc", description="RC-сессии Claude Code")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bot", help="запустить Telegram-бота на переднем плане")
    sub.add_parser("version", help="версия")

    sessions = sub.add_parser("sessions", help="живые RC-сессии")
    sessions.add_argument("--json", action="store_true", dest="as_json")

    start = sub.add_parser("start", help="поднять сессию")
    start.add_argument("path", nargs="?", default=".", help="каталог (по умолчанию текущий)")
    start.add_argument("--branch", help="создать worktree под ветку")
    start.add_argument("--resume", help="продолжить диалог: last или id")

    stop = sub.add_parser("stop", help="погасить сессию")
    stop.add_argument("target", nargs="?", help="имя сессии или каталог")
    stop.add_argument("--all", action="store_true", dest="every")

    doctor = sub.add_parser("doctor", help="проверить окружение")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    sub.add_parser("setup", help="заполнить config.toml: токен, user_id, каталоги")

    return parser


class _Commands:
    """Обработчики подкоманд. Имя метода совпадает с именем команды."""

    @staticmethod
    def bot(args: argparse.Namespace) -> int:
        from clauderc.bot import main as bot_main

        asyncio.run(bot_main())
        return 0

    @staticmethod
    def version(args: argparse.Namespace) -> int:
        try:
            print(package_version("claude-rc"))
        except PackageNotFoundError:
            print("unknown (пакет не установлен)")
        return 0

    @staticmethod
    def sessions(args: argparse.Namespace) -> int:
        found = asyncio.run(list_sessions())
        if args.as_json:
            print(json.dumps({"sessions": [_as_dict(s) for s in found]}, ensure_ascii=False))
            return 0
        if not found:
            print("Живых сессий нет.")
            return 0
        for session in found:
            print(f"{session.name}\t{session.cwd}\t{int(session.uptime_s())}s\t{session.url}")
        return 0

    @staticmethod
    def start(args: argparse.Namespace) -> int:
        target = Path(args.path).expanduser().resolve()
        if not target.is_dir():
            print(f"Каталог не найден: {target}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        # remote._resume_flag("") даёт `--resume ''` в командной строке tmux —
        # пустую строку отклоняем здесь, а не в remote.py.
        if args.resume == "":
            print("--resume не может быть пустой строкой.", file=sys.stderr)
            return EXIT_ENVIRONMENT
        if args.branch:
            # _start дальше зовёт load_config напрямую — без этой проверки
            # отсутствие конфига долетает наружу как FileNotFoundError с
            # трейсбеком вместо внятного сообщения (см. _diagnose).
            config_path = paths.config_file()
            if not config_path.is_file():
                print(
                    f"--branch нужен config.toml, а его нет: {config_path}\n"
                    "Скопируй config.example.toml и заполни (см. README).",
                    file=sys.stderr,
                )
                return EXIT_ENVIRONMENT
        try:
            session = asyncio.run(_start(target, args.branch, args.resume))
        except (LaunchError, WorktreeError) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        except _TrustDeclined as exc:
            print(str(exc), file=sys.stderr)
            return exc.exit_code
        print(f"{session.name}\t{session.cwd}\n{session.url}")
        return 0

    @staticmethod
    def stop(args: argparse.Namespace) -> int:
        if args.every:
            print(f"Погашено сессий: {asyncio.run(kill_all())}.")
            return 0
        if not args.target:
            print("Укажи имя сессии, каталог или --all.", file=sys.stderr)
            return EXIT_ENVIRONMENT
        try:
            killed = asyncio.run(_stop(args.target))
        except _StopFailed as exc:
            print(f"Нашёл, но не погасил: {exc}", file=sys.stderr)
            return EXIT_FAILED
        if killed is None:
            print(f"Сессия не найдена: {args.target}", file=sys.stderr)
            return EXIT_FAILED
        print(f"Погашена: {killed}")
        return 0

    @staticmethod
    def doctor(args: argparse.Namespace) -> int:
        checks = _diagnose()
        if args.as_json:
            print(json.dumps({"checks": checks}, ensure_ascii=False))
        else:
            for check in checks:
                print(f"{'✓' if check['ok'] else '✗'} {check['name']}: {check['detail']}")
        return 0 if all(check["ok"] for check in checks) else EXIT_ENVIRONMENT

    @staticmethod
    def setup(args: argparse.Namespace) -> int:
        if not sys.stdin.isatty():
            print(
                "setup — интерактивная команда, а stdin не терминал.\n"
                "Запусти её в терминале: claude-rc setup",
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        try:
            return asyncio.run(_run_setup(paths.config_file()))
        except KeyboardInterrupt:
            # Ctrl+C во время asyncio.run прилетает в кадр цикла, а не в
            # приостановленную корутину — try/except внутри неё это не ловит.
            # Ловим тут, чтобы человек увидел сообщение, а не трейсбек.
            print("\nПрервано.", file=sys.stderr)
            return EXIT_ENVIRONMENT


class _TrustDeclined(RuntimeError):
    """Каталог требует подтверждения доверия, а подтвердить некому или отказались."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class _StopFailed(RuntimeError):
    """Сессия нашлась, но tmux не смог её погасить — не путать с «не найдена»."""


def _as_dict(session: RemoteSession) -> dict[str, Any]:
    return {
        "name": session.name,
        "tmux_name": session.tmux_name,
        "cwd": session.cwd,
        "url": session.url,
        "uptime_s": int(session.uptime_s()),
    }


async def _start(target: Path, branch: str | None, resume: str | None) -> RemoteSession:
    cwd = target
    if branch:
        config = load_config(paths.config_file())
        cwd = await worktrees.ensure(target, branch, config.worktree_root)
    try:
        return await launch(cwd.name, str(cwd), resume=resume)
    except TrustRequired as need:
        return await _ask_trust(need)


async def _ask_trust(need: TrustRequired) -> RemoteSession:
    """Диалог доверия каталогу. За терминалом человек — его «да» и есть решение."""
    if not sys.stdin.isatty():
        # Нечем спросить — это «не с чем работать», EXIT_ENVIRONMENT.
        raise _TrustDeclined(
            f"Каталог {need.cwd} ждёт подтверждения доверия, а stdin не терминал.\n"
            f"Подтверди в панели: tmux attach -t {need.tmux_name}",
            exit_code=EXIT_ENVIRONMENT,
        )
    answer = input(f"Claude впервые видит {need.cwd}. Доверяешь каталогу? [y/N] ")
    if answer.strip().lower() not in {"y", "yes", "д", "да"}:
        await kill_tmux(need.tmux_name)
        # Спросили и получили «нет» — это «попробовали, не вышло», EXIT_FAILED.
        raise _TrustDeclined("Отменено, сессия погашена.", exit_code=EXIT_FAILED)
    await confirm_trust(need.tmux_name)
    # watch_trust=False: диалог ещё мгновение висит на экране и был бы принят
    # за неотвеченный (та же причина, что в боте).
    return await await_url(need.tmux_name, need.cwd, watch_trust=False)


# Две попытки на каждый вопрос: одна на опечатку, вторая на исправление.
_ATTEMPTS = 2


async def _run_setup(target: Path) -> int:
    """Спрашивает три значения и пишет config.toml."""
    current = _current_answers(target)
    # Файла может не быть — тогда автоподхват user_id уместен, это первый запуск.
    # А может быть, но не читаться (например, каталог из rc_roots исчез) — тогда
    # это не первый запуск: бот, скорее всего, настроен и работает, и предлагать
    # автоподхват нельзя (см. _ask_user_id). lexists, а не exists: битый симлинк
    # exists() посчитал бы отсутствующим, а O_CREAT потом запишет прямо сквозь
    # него в цель симлинка — то есть, возможно, в чужой файл.
    try:
        config_exists = os.path.lexists(target)
    except OSError as exc:
        print(f"не удалось проверить {target}: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    token = await _ask_token(current.bot_token if current else None)
    if token is None:
        return EXIT_ENVIRONMENT

    user_id = await _ask_user_id(
        current.allowed_user_id if current else None, token, config_exists=config_exists
    )
    if user_id is None:
        return EXIT_ENVIRONMENT

    try:
        roots = _ask_roots(current.rc_roots if current else None)
    except setup.RootsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT

    answers = setup.Answers(bot_token=token, allowed_user_id=user_id, rc_roots=roots)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        _write_config(target, setup.render_config(answers, extra=_current_extras(target)))
    except OSError as exc:
        print(f"не удалось записать {target}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"\nГотово: {target}")
    print("Запусти приложение ClaudeRC или `claude-rc bot`.")
    return 0


def _write_config(target: Path, content: str) -> None:
    """Пишет файл сразу с правами 600 и дожимает их, даже если файл уже был.

    `write_text` создаёт файл по umask (обычно 0644): до отдельного `chmod`
    токен в нём читаем любым пользователем машины. Но и `0o600` в `os.open`
    не панацея: этот режим действует только при *создании* файла — если
    `config.toml` уже лежал с более широкими правами (например, от версии
    без этой правки), `O_CREAT` их не тронет. Поэтому дожимаем `os.fchmod` по
    дескриптору — не `os.chmod` по пути, чтобы между открытием и сужением
    прав путь не успел подмениться (например, на симлинк).
    """
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(content)


def _current_answers(target: Path) -> setup.Answers | None:
    """Что уже настроено. Битый конфиг — то же, что его отсутствие для этих трёх полей."""
    try:
        config = load_config(target)
    except (OSError, ValueError, KeyError):
        return None
    return setup.Answers(
        bot_token=config.bot_token,
        allowed_user_id=config.allowed_user_id,
        rc_roots=config.rc_roots,
    )


_EXTRA_KEYS = ("worktree_root", "state_path", "scan_depth", "launch_timeout_s")


def _current_extras(target: Path) -> dict[str, object]:
    """Поля, которые визард не спрашивает, но не должен стирать при перезаписи.

    Читаем сырым `tomllib`, а не через `load_config`: если `rc_roots` указывает
    на исчезнувший каталог, `load_config` падает целиком, а эти поля к
    `rc_roots` отношения не имеют и читаются нормально.
    """
    try:
        with target.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {key: raw[key] for key in _EXTRA_KEYS if key in raw}


async def _ask_token(current: str | None) -> str | None:
    hint = f" [{setup.mask_token(current)}]" if current else ""
    for _ in range(_ATTEMPTS):
        # getpass, а не input: обычный ввод остаётся в scrollback панели и в
        # tmux capture-pane — а весь проект как раз построен вокруг чтения
        # панелей через tmux.
        raw = getpass.getpass(f"Токен бота от @BotFather{hint}: ").strip()
        token = raw or (current or "")
        if not token:
            print("Токен нужен: заведи бота у @BotFather и вставь сюда.")
            continue

        check = await setup.verify_token(token)
        if check.ok:
            print(f"  ✓ бот @{check.bot_name}" if check.bot_name else "  ✓ токен принят")
            return token
        if check.offline:
            print(f"  ! {check.detail} — продолжаю, проверить не смог.")
            return token
        print(f"  ✗ {check.detail}")
    print("Токен так и не принят.", file=sys.stderr)
    return None


# Тот же якорь, что и в Swift-части (BotSupervisor.foreignBotPID): левая граница
# сужена до начала строки или "/" — иначе шаблон подошёл бы под что угодно с
# этими словами внутри, а точка в "clauderc.bot" экранирована.
_BOT_PROCESS_PATTERN = r"(^|/)claude-rc bot$|-m clauderc\.bot$"


def _foreign_bot_pid() -> int | None:
    """PID уже работающего `claude-rc bot`, если такой процесс есть.

    `config_exists`/`current is None` — только прокси для «бот, скорее всего,
    работает». Это прямая проверка того факта, который на самом деле опасен:
    поллинг рядом с живым ботом того же токена.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-fl", _BOT_PROCESS_PATTERN],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Тот же выбор, что в Swift-части: на решение "поднять поллинг рядом с
        # ботом" отвечаем консервативно только когда проверка вообще сработала.
        # Если pgrep не ответил или его нет — считаем, что чужого бота нет.
        return None

    own_pid = os.getpid()
    for line in result.stdout.splitlines():
        token = line.split(maxsplit=1)
        if not token or not token[0].isdigit():
            continue
        pid = int(token[0])
        if pid != own_pid:
            return pid
    return None


async def _ask_user_id(current: int | None, token: str, *, config_exists: bool) -> int | None:
    # Автоподхват предлагаем только на действительно первом запуске: если файл
    # уже есть (пусть даже не читается), бот, скорее всего, настроен и работает,
    # и поллинг рядом с ним поднимет второго поллера того же токена.
    if current is None and not config_exists:
        foreign_pid = _foreign_bot_pid()
        if foreign_pid is not None:
            print(f"  Бот уже запущен (pid {foreign_pid}) — введи user_id вручную.")
        elif _agrees("Узнать твой user_id автоматически? Напишешь боту любое сообщение."):
            print("  Жду сообщение боту… до 2 минут, Ctrl+C — прервать.")
            caught = await setup.catch_user_id(token)
            if caught is not None:
                print(f"  ✓ user_id: {caught}")
                return caught
            print("  Не дождался — введи вручную.")

    hint = f" [{current}]" if current else ""
    for _ in range(_ATTEMPTS):
        raw = input(f"Твой Telegram user_id{hint}: ").strip()
        if not raw and current:
            return current
        try:
            return int(raw)
        except ValueError:
            print("  ✗ нужно число. Узнать своё: напиши @userinfobot.")
    print("user_id не получен.", file=sys.stderr)
    return None


def _agrees(question: str) -> bool:
    return input(f"{question} [Y/n]: ").strip().lower() in {"", "y", "yes", "д", "да"}


def _ask_roots(current: tuple[Path, ...] | None) -> tuple[Path, ...]:
    default = current or setup.default_roots()
    shown = ", ".join(str(item) for item in default)
    last: setup.RootsError | None = None
    for _ in range(_ATTEMPTS):
        raw = input(f"Каталоги с репозиториями, через запятую [{shown}]: ")
        try:
            return setup.parse_roots(raw, default=default)
        except setup.RootsError as exc:
            last = exc
            print(f"  ✗ {exc}")
    raise last if last else setup.RootsError("каталоги не получены")


async def _stop(target: str) -> str | None:
    """Гасит сессию по имени или каталогу.

    Существования каталога не требуем: worktree мог быть удалён, а сессия в нём
    остаться — именно её и нужно погасить.
    """
    wanted = os.path.realpath(Path(target).expanduser())
    for session in await list_sessions():
        if session.name == target or os.path.realpath(session.cwd) == wanted:
            if not await kill_tmux(session.tmux_name):
                raise _StopFailed(session.name)
            return session.name
    return None


def _diagnose() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for binary in ("tmux", "claude"):
        found = shutil.which(binary)
        checks.append(
            {
                "name": binary,
                "ok": found is not None,
                "detail": found or "не найден в PATH",
            }
        )

    config_path = paths.config_file()
    if not config_path.is_file():
        checks.append({"name": "config", "ok": False, "detail": f"нет файла {config_path}"})
        return checks

    try:
        config = load_config(config_path)
    except (ValueError, KeyError, OSError) as exc:
        checks.append({"name": "config", "ok": False, "detail": f"{config_path}: {exc}"})
        return checks

    checks.append({"name": "config", "ok": True, "detail": str(config_path)})
    # Значение токена не печатаем никогда — только факт, что он не пуст.
    checks.append(
        {
            "name": "bot_token",
            "ok": bool(config.bot_token),
            "detail": "задан" if config.bot_token else "пуст",
        }
    )
    checks.append(
        {
            "name": "allowed_user_id",
            "ok": config.allowed_user_id != 0,
            "detail": "задан" if config.allowed_user_id else "не задан",
        }
    )
    checks.append(
        {
            "name": "rc_roots",
            "ok": True,
            "detail": ", ".join(str(root) for root in config.rc_roots),
        }
    )
    return checks
