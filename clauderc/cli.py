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
import subprocess as subprocess  # тесты подменяют cli.subprocess.run
import sys as sys
import tempfile
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, NamedTuple

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
            # отсутствие или порча конфига долетает наружу трейсбеком вместо
            # внятного сообщения (тот же контракт, что и в _diagnose).
            config_path = paths.config_file()
            if not config_path.is_file():
                print(
                    f"--branch нужен config.toml, а его нет: {config_path}\n"
                    "Скопируй config.example.toml и заполни (см. README).",
                    file=sys.stderr,
                )
                return EXIT_ENVIRONMENT
            try:
                load_config(config_path)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                print(f"--branch нужен рабочий config.toml: {config_path}: {exc}", file=sys.stderr)
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
    # Путь показываем раньше любых вопросов: `config_file()` может вернуть
    # `./config.toml` из текущего каталога — документированный способ работы
    # в клоне, но и корень совсем чужого репозитория, если запустить визард
    # не там. Молчание здесь — то, как это выяснилось на живом PR: чужой файл
    # переписан, а на его месте — боевой токен бота.
    print(f"Конфиг будет записан сюда: {target}")
    if target != paths.default_config_file():
        print(
            "  ! Это не путь по умолчанию (~/.config/claude-rc/config.toml) — "
            "тулза и приложение ищут конфиг по своей цепочке (см. README), и не "
            "факт, что найдут именно этот файл.",
            file=sys.stderr,
        )

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

    # is_ours — не «config_exists», а именно «похоже на конфиг claude-rc»
    # (есть bot_token). Технические поля (_current_extras) переносим только
    # отсюда: из файла, который мы сами отказались бы переписывать без
    # подтверждения, читать что-либо как «своё» нельзя — это чужие данные.
    is_ours = False
    if config_exists:
        raw = _read_raw_toml(target)
        if raw is None:
            # Не разобрался как TOML вовсе — если это был наш конфиг с ручными
            # правками технических полей (worktree_root, scan_depth и т.п.),
            # перенести их не получится: render_config пишет только то, что
            # смог прочитать (см. _current_extras).
            print(
                "  ! Существующий файл не разобрался как TOML — если там были "
                "технические настройки (worktree_root, scan_depth и т.п.), "
                "перенести их не получится.",
                file=sys.stderr,
            )
        else:
            is_ours = "bot_token" in raw
            if not is_ours:
                print(
                    "  ! Файл не похож на конфиг claude-rc — технические "
                    "настройки из него (worktree_root, scan_depth и т.п.), "
                    "если они там есть, не переносятся.",
                    file=sys.stderr,
                )
        if not is_ours:
            # Не похоже на конфиг claude-rc: не парсится вовсе или нет ключа,
            # которым мы вообще узнаём свой файл. Переписывать такой без
            # подтверждения нельзя — Enter не должен молча означать «да».
            if not _agrees(
                f"По этому пути уже есть файл, непохожий на конфиг claude-rc "
                f"({target}). Переписать его?",
                default=False,
            ):
                print("Отменено — файл не тронут.", file=sys.stderr)
                return EXIT_ENVIRONMENT
        else:
            # Свой файл — перезапись обычна и вопросов не добавляет, но про
            # потерю комментариев и полей вне известного списка визард раньше
            # молчал (хотя про непереносимый тип поля — предупреждает ниже):
            # асимметрия, а не тишина по делу.
            print(
                "  Существующий конфиг будет переписан — комментарии и любые "
                "поля вне тех, что знает визард, будут потеряны.",
                file=sys.stderr,
            )

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
    # Технические поля переносим только из файла, который опознали как свой —
    # у чужого файла (Hugo, что угодно) worktree_root или scan_depth к нам
    # отношения не имеют, даже если человек согласился его переписать.
    extras = _current_extras(target) if is_ours else {}
    for key, type_name in setup.unsupported_extra_keys(extras):
        # Человек должен узнать, что поле пропало, а не найти это через полгода,
        # разбирая, откуда в конфиге нет его правки.
        print(
            f"  ! поле «{key}» ({type_name}) не перенесено — не умею записать такой "
            "тип в TOML, правь его в файле руками.",
            file=sys.stderr,
        )
    try:
        parent_existed = target.parent.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            # Сужаем права только каталогу, который создали сами. paths.config_file()
            # может вернуть `./config.toml` из текущего каталога (документированный
            # способ работы в клоне) или путь из $CLAUDE_RC_CONFIG — в обоих случаях
            # это не наш каталог, и его права трогать нельзя (первое — вообще корень
            # чужого репозитория).
            target.parent.chmod(0o700)
        _write_config(target, setup.render_config(answers, extra=extras))
    except OSError as exc:
        print(f"не удалось записать {target}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"\nГотово: {target}")
    print("Запусти приложение ClaudeRC или `claude-rc bot`.")
    return 0


def _write_config(target: Path, content: str) -> None:
    """Пишет во временный файл рядом и атомарно подменяет им целевой путь.

    `O_TRUNC` прямо по `target` сначала обнуляет файл, потом пишет — если
    процесс умрёт между этими моментами, конфиг уничтожен, а токен записан
    наполовину. Именно этот файл человек не коммитит и нигде не дублирует:
    восстановить неоткуда. `os.replace` атомарен в пределах одной файловой
    системы (временный файл лежит в том же каталоге, что и `target`, — это
    условие и обеспечивает), поэтому целевой путь в любой момент либо старый
    целиком, либо новый целиком, третьего не бывает.

    Права `0600` выставляем на временный файл `os.fchmod`'ом ДО записи — если
    выставить после, окно «файл на диске читаем всем» просто переедет с
    `target` на временный файл, а не исчезнет.
    """
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _current_answers(target: Path) -> setup.Answers | None:
    """Что уже настроено. Битый конфиг — то же, что его отсутствие для этих трёх полей."""
    try:
        config = load_config(target)
    except (OSError, ValueError, KeyError, TypeError):
        # TypeError — например, scan_depth в файле оказался списком, а не
        # числом: int(raw.get("scan_depth", 3)) падает не ValueError. Без
        # этого визард крашился бы трейсбеком на файле с любым битым по типу
        # техническим полем, а не только с плохим rc_roots.
        return None
    return setup.Answers(
        bot_token=config.bot_token,
        allowed_user_id=config.allowed_user_id,
        rc_roots=config.rc_roots,
    )


def _read_raw_toml(target: Path) -> dict[str, object] | None:
    """Сырой разбор файла — без валидации `load_config` (типов, `rc_roots` и т.п.).

    `None` — файла нет, нет доступа или содержимое вообще не TOML. Используется
    и чтобы отличить «наш конфиг с чем-то битым» от «чужой файл» (по наличию
    `bot_token`), и чтобы перенести технические поля (`_current_extras`) — оба
    вопроса не требуют полной валидации, которую делает `load_config`.
    """
    try:
        with target.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


_EXTRA_KEYS = ("worktree_root", "state_path", "scan_depth", "launch_timeout_s")


def _current_extras(target: Path) -> dict[str, object]:
    """Поля, которые визард не спрашивает, но не должен стирать при перезаписи."""
    raw = _read_raw_toml(target)
    if raw is None:
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


class _ForeignBotCheck(NamedTuple):
    """Результат поиска живого `claude-rc bot`.

    `checked=False` — не «бота нет», а «не смогли проверить» (pgrep не ответил
    вовремя или не установлен). Разница важна: молча трактовать её как «нет
    бота» значит идти на автоподхват вслепую именно тогда, когда проверка
    нужнее всего.
    """

    pid: int | None
    checked: bool


def _foreign_bot_pid() -> _ForeignBotCheck:
    """Ищет живой `claude-rc bot`.

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
        return _ForeignBotCheck(pid=None, checked=False)

    own_pid = os.getpid()
    for line in result.stdout.splitlines():
        token = line.split(maxsplit=1)
        if not token or not token[0].isdigit():
            continue
        pid = int(token[0])
        if pid != own_pid:
            return _ForeignBotCheck(pid=pid, checked=True)
    return _ForeignBotCheck(pid=None, checked=True)


def _should_attempt_autopickup() -> bool:
    """Спрашивает разрешения на автоподхват, учитывая проверку живого бота."""
    check = _foreign_bot_pid()
    if check.pid is not None:
        print(f"  Бот уже запущен (pid {check.pid}) — введи user_id вручную.")
        return False
    if not check.checked:
        # Swift-версия в этом месте пишет в лог — здесь того же требует человек:
        # без предупреждения он не узнает, что guard не сработал, и согласится
        # на автоподхват с полной уверенностью, что чужого бота нет.
        print(
            "  ! Не удалось проверить, не запущен ли уже бот — pgrep не ответил "
            "вовремя или не найден в PATH.",
            file=sys.stderr,
        )
        # default=False: пустой ответ здесь не должен означать «пробуем» — согласие
        # рядом с работающим ботом может его сломать.
        return _agrees(
            "Проверить не вышло. Если бот уже работает, автоподхват его сломает — "
            "всё равно попробовать?",
            default=False,
        )
    return _agrees("Узнать твой user_id автоматически? Напишешь боту любое сообщение.")


async def _ask_user_id(current: int | None, token: str, *, config_exists: bool) -> int | None:
    # Автоподхват предлагаем только на действительно первом запуске: если файл
    # уже есть (пусть даже не читается), бот, скорее всего, настроен и работает,
    # и поллинг рядом с ним поднимет второго поллера того же токена.
    if current is None and not config_exists and _should_attempt_autopickup():
        print("  Жду сообщение боту… до 2 минут, Ctrl+C — прервать.")
        caught: setup.CaughtSender | None = None
        try:
            caught = await setup.catch_user_id(token)
        except setup.PollingConflict:
            # Кто-то ещё опрашивает getUpdates этим же токеном прямо сейчас —
            # выглядит как истёкший таймаут, но причина другая и человеку стоит
            # её знать: скорее всего, где-то уже работает второй бот.
            print("  ✗ Telegram ответил конфликтом — похоже, токен уже опрашивает кто-то ещё.")
        except setup.TokenRejected:
            print("  ✗ Telegram отверг токен во время ожидания.")
        else:
            if caught is None:
                print("  Не дождался.")

        if caught is not None:
            # Сверить пойманный id не с чем — показываем, кого поймали, и просим
            # подтвердить. Это второй рубеж после сброса бэклога в catch_user_id:
            # даже если чужое сообщение как-то проскочит, человек увидит чужое
            # имя и откажется. Печатаем отдельным print, а не в подсказку
            # input() — та не гарантированно долетает до stdout (см. _agrees).
            handle = f" (@{caught.username})" if caught.username else ""
            print(f"  Поймал: {caught.display_name}{handle}, id {caught.user_id}.")
            # default=False: это ровно тот вопрос, где ошибка отдаёт машину
            # постороннему — Enter не должен молча означать «да».
            if _agrees("Это ты?", default=False):
                print(f"  ✓ user_id: {caught.user_id}")
                return caught.user_id
            print("  Не совпало — введи вручную.")
        else:
            print("  Введи вручную.")

    hint = f" [{current}]" if current else ""
    for _ in range(_ATTEMPTS):
        raw = input(f"Твой Telegram user_id{hint}: ").strip()
        # current > 0: сохранённое значение тоже могло быть 0 или отрицательным
        # (правки руками, старая версия визарда без этой проверки) — Enter не
        # должен молча увековечить уже битое значение.
        if not raw and current is not None and current > 0:
            return current
        try:
            value = int(raw)
        except ValueError:
            print("  ✗ нужно число. Узнать своё: напиши @userinfobot.")
            continue
        # Telegram user_id всегда положительный. int(raw) охотно принял бы и 0,
        # и отрицательное число — визард сказал бы «Готово», а бот с таким
        # id никогда бы не ответил владельцу: человек не узнал бы, что что-то
        # не так, до следующего письма в поддержку.
        if value <= 0:
            print("  ✗ Telegram id всегда положительный. Узнать своё: напиши @userinfobot.")
            continue
        return value
    print("user_id не получен.", file=sys.stderr)
    return None


def _agrees(question: str, *, default: bool = True) -> bool:
    """Да/нет с явным умолчанием на пустой ответ — не всегда «да».

    `default=False` — для вопросов, где ошибка дорого стоит (принять чужой
    id, поллить рядом с уже работающим ботом): Enter там не должен молча
    означать согласие. Подсказка `[y/N]`/`[Y/n]` показывает, что выберет тишина.
    """
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "д", "да"}


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
    except (ValueError, KeyError, OSError, TypeError) as exc:
        # TypeError — например, scan_depth в файле оказался списком, а не
        # числом (int(raw.get("scan_depth", 3)) кидает не ValueError). Тот же
        # довод, что и в _current_answers: doctor теперь ворота приложения —
        # именно по его ответу оно решает, показывать ли Run setup….
        # Необработанное исключение здесь — тот самый тупик «не настроено»
        # без способа настроить, который этот PR и закрывает.
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
