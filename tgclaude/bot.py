import asyncio
import html
import logging
import time
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from tgclaude.config import Config, load_config
from tgclaude.remote import (
    LaunchError,
    RemoteSession,
    get_session,
    kill_all,
    kill_session,
    launch,
    list_sessions,
    tmux_available,
)
from tgclaude.render import chunks
from tgclaude.repos import discover, resolve

log = logging.getLogger("tgclaude")

DISCOVERY_TTL_S = 60.0
MAX_CHOICES = 8
HELP = (
    "Поднимаю сессии Claude Code с Remote Control — дальше работа в приложении Claude.\n\n"
    "<b>/rc</b> &lt;репо&gt; — поднять сессию\n"
    "<b>/rc</b> — живые сессии\n"
    "<b>/repos</b> — доступные репозитории\n"
    "<b>/rckill</b> &lt;имя&gt; — погасить сессию (без имени — все)\n\n"
    "Сессии живут в tmux и переживают рестарт бота."
)


def _is_authorized(from_user, allowed_user_id: int) -> bool:
    """Пропускаем только владельца. from_user=None (канал/анонимный админ) → отказ (fail-closed)."""
    return from_user is not None and from_user.id == allowed_user_id


def _open_keyboard(url: str) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть в Claude", url=url)]]
    )


def _uptime(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


def _label(path: Path, roots: tuple[Path, ...]) -> str:
    """Короткое имя для списка: относительный путь от ближайшего корня."""
    for root in roots:
        if path == root:
            return path.name
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


def _link_line(session: RemoteSession) -> str:
    if session.url:
        return html.escape(session.url)
    return f"ссылка неизвестна, подсядь: <code>{html.escape(session.attach_hint)}</code>"


def _fresh_text(session: RemoteSession) -> str:
    return (
        f"✅ Сессия <b>{html.escape(session.name)}</b> поднята\n"
        f"<code>{html.escape(session.cwd)}</code>\n"
        f"{_link_line(session)}"
    )


def _list_item(session: RemoteSession) -> str:
    return (
        f"▸ <b>{html.escape(session.name)}</b> · {_uptime(session.uptime_s())}\n"
        f"<code>{html.escape(session.cwd)}</code>\n"
        f"{_link_line(session)}"
    )


class Discovery:
    """Кэш обхода файловой системы: скан на глубину 3 не бесплатный."""

    def __init__(self, config: Config, ttl_s: float = DISCOVERY_TTL_S) -> None:
        self._config = config
        self._ttl_s = ttl_s
        self._cached: list[Path] = []
        self._at = 0.0

    async def paths(self, *, refresh: bool = False) -> list[Path]:
        if refresh or not self._cached or time.monotonic() - self._at > self._ttl_s:
            self._cached = await asyncio.to_thread(
                discover, list(self._config.rc_roots), self._config.scan_depth
            )
            self._at = time.monotonic()
        return self._cached


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    root = Path(__file__).resolve().parent.parent
    config = load_config(root / "config.toml")
    if not tmux_available():
        raise SystemExit("tmux не найден в PATH — поставь через `brew install tmux`")

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    discovery = Discovery(config)
    # Кандидаты для кнопок выбора при неоднозначном запросе. Токен короткий,
    # потому что в callback_data влезает 64 байта — полный путь туда не поместится.
    pending: dict[str, Path] = {}

    async def start_session(message: Message, target: Path) -> None:
        name = target.name
        alive = await get_session(name)
        if alive is not None:
            await message.answer(
                f"Уже поднята.\n{_list_item(alive)}",
                parse_mode="HTML",
                reply_markup=_open_keyboard(alive.url),
            )
            return

        notice = await message.answer(
            f"⏳ Поднимаю сессию в <code>{html.escape(str(target))}</code>…",
            parse_mode="HTML",
        )
        try:
            session = await launch(name, str(target), timeout_s=config.launch_timeout_s)
        except LaunchError as exc:
            log.warning("launch failed for %s: %s", target, exc)
            await notice.edit_text(
                f"❌ Не поднялось.\n<pre>{html.escape(str(exc))}</pre>"[:3800],
                parse_mode="HTML",
            )
            return

        await notice.edit_text(
            _fresh_text(session), parse_mode="HTML", reply_markup=_open_keyboard(session.url)
        )

    @dp.message(lambda event: not _is_authorized(event.from_user, config.allowed_user_id))
    async def reject_strangers(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        log.warning("dropped message from user_id=%s", uid)

    @dp.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        await message.reply(HELP, parse_mode="HTML")

    @dp.message(Command("repos"))
    async def cmd_repos(message: Message) -> None:
        paths = await discovery.paths(refresh=True)
        if not paths:
            await message.reply("Ничего не нашёл. Проверь rc_roots в config.toml.")
            return
        body = "\n".join(f"• <code>{html.escape(_label(p, config.rc_roots))}</code>" for p in paths)
        for part in chunks(body):
            await message.answer(part, parse_mode="HTML")

    @dp.message(Command("rc"))
    async def cmd_rc(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            sessions = await list_sessions()
            if not sessions:
                await message.reply("Живых сессий нет. <b>/rc</b> &lt;репо&gt;", parse_mode="HTML")
                return
            await message.reply("\n\n".join(_list_item(s) for s in sessions), parse_mode="HTML")
            return

        query = parts[1]
        matches = resolve(query, await discovery.paths())
        if not matches:
            await message.reply(
                f"Не нашёл <code>{html.escape(query)}</code>. Список — /repos", parse_mode="HTML"
            )
            return
        if len(matches) == 1:
            await start_session(message, matches[0])
            return

        rows = []
        for path in matches[:MAX_CHOICES]:
            token = uuid.uuid4().hex[:8]
            pending[token] = path
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_label(path, config.rc_roots), callback_data=f"rc:{token}"
                    )
                ]
            )
        tail = "" if len(matches) <= MAX_CHOICES else f"\n(показал {MAX_CHOICES} из {len(matches)})"
        await message.reply(
            f"Несколько совпадений — выбери:{tail}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @dp.message(Command("rckill"))
    async def cmd_rckill(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            killed = await kill_all()
            await message.reply(f"Погашено сессий: {killed}." if killed else "Гасить нечего.")
            return
        name = parts[1].strip()
        if await kill_session(name):
            await message.reply(f"Сессия <b>{html.escape(name)}</b> погашена.", parse_mode="HTML")
        else:
            await message.reply(
                f"Нет живой сессии <code>{html.escape(name)}</code>.", parse_mode="HTML"
            )

    @dp.callback_query(F.data.startswith("rc:"))
    async def on_pick(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        target = pending.pop(query.data[3:], None)
        await query.answer()
        if query.message is None:
            return
        await query.message.edit_reply_markup(reply_markup=None)
        if target is None:
            await query.message.answer("Выбор устарел, повтори /rc.")
            return
        await start_session(query.message, target)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
