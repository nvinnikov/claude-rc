"""Команда `claude-rc` — тот же набор действий, что у бота, но из терминала.

Всё, кроме разбора аргументов и печати, живёт в модулях ядра: CLI и бот — две
равноправные морды над одним кодом, а не копия логики.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil as shutil  # тесты подменяют cli.shutil/cli.sys.stdin — реэкспорт для mypy --strict
import sys as sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from clauderc import paths as paths  # тесты подменяют cli.paths.config_file — см. выше
from clauderc import worktrees as worktrees  # тесты подменяют cli.worktrees.ensure
from clauderc.config import load_config
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
