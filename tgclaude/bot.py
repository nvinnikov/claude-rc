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

from tgclaude import browse, worktrees
from tgclaude.browse import BrowseError
from tgclaude.config import Config, load_config
from tgclaude.remote import (
    LaunchError,
    RemoteSession,
    TrustRequired,
    await_url,
    confirm_trust,
    find,
    kill_all,
    kill_session,
    kill_tmux,
    launch,
    list_sessions,
    tmux_available,
)
from tgclaude.render import chunks
from tgclaude.repos import discover, resolve
from tgclaude.state import State
from tgclaude.worktrees import WorktreeError

log = logging.getLogger("tgclaude")

DISCOVERY_TTL_S = 60.0
MAX_CHOICES = 8
HELP = (
    "Поднимаю сессии Claude Code с Remote Control — дальше работа в приложении Claude.\n\n"
    "<b>Дойти ногами</b>\n"
    "<b>/pwd</b> — где я сейчас, с кнопками по подкаталогам\n"
    "<b>/cd</b> &lt;путь&gt; — перейти (<code>..</code>, <code>~</code>, относительный, абсолютный)\n"
    "Кнопка «Запустить здесь» поднимает сессию в текущем каталоге.\n\n"
    "<b>По имени</b>\n"
    "<b>/rc</b> &lt;репо&gt; — поднять сессию\n"
    "<b>/rc</b> &lt;репо&gt; &lt;ветка&gt; — то же, но в отдельном worktree\n"
    "<b>/repos</b> — доступные репозитории\n\n"
    "<b>Хозяйство</b>\n"
    "<b>/rc</b> — живые сессии\n"
    "<b>/rckill</b> &lt;имя&gt; — погасить сессию (без имени — все)\n"
    "<b>/wt</b> — созданные worktree\n"
    "<b>/wtrm</b> &lt;имя&gt; — удалить worktree\n\n"
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


def _browse_card(cwd: Path) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка навигации: где мы и куда можно шагнуть одним тапом."""
    mark = " · git-репозиторий" if browse.is_repo(cwd) else ""
    try:
        children, total = browse.entries(cwd)
    except BrowseError as exc:
        return f"📁 <code>{html.escape(str(cwd))}</code>\n❌ {html.escape(str(exc))}", (
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬆️ ..", callback_data="nav:up")]]
            )
        )

    rows: list[list[InlineKeyboardButton]] = []
    if cwd.parent != cwd:
        rows.append([InlineKeyboardButton(text="⬆️ ..", callback_data="nav:up")])

    # По две кнопки в ряд: на телефоне длинные имена иначе не читаются.
    pair: list[InlineKeyboardButton] = []
    for index, child in enumerate(children):
        title = ("● " if browse.is_repo(child) else "") + child.name
        pair.append(InlineKeyboardButton(text=title, callback_data=f"nav:{index}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    rows.append([InlineKeyboardButton(text="▶️ Запустить здесь", callback_data="nav:here")])

    text = f"📁 <code>{html.escape(str(cwd))}</code>{mark}"
    if not children:
        text += "\nПодкаталогов нет."
    elif total > len(children):
        text += f"\nПоказал {len(children)} из {total} — остальные через <b>/cd</b>."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


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
    state = State(config.state_path, config.rc_roots[0])
    # Кандидаты для кнопок выбора при неоднозначном запросе. Токен короткий,
    # потому что в callback_data влезает 64 байта — полный путь туда не поместится.
    pending: dict[str, tuple[Path, str | None]] = {}
    # Сессии, которые ждут ответа на диалог доверия каталогу.
    trust_pending: dict[str, tuple[str, str]] = {}

    async def start_session(message: Message, target: Path, branch: str | None) -> None:
        head = f"⏳ Поднимаю сессию в <code>{html.escape(str(target))}</code>"
        if branch:
            head += f"\nветка <code>{html.escape(branch)}</code>"
        notice = await message.answer(head + "…", parse_mode="HTML")

        cwd = target
        if branch:
            try:
                cwd = await worktrees.ensure(target, branch, config.worktree_root)
            except WorktreeError as exc:
                await notice.edit_text(
                    f"❌ Worktree не создан.\n<pre>{html.escape(str(exc))}</pre>"[:3800],
                    parse_mode="HTML",
                )
                return

        alive = await find(str(cwd))
        if alive is not None:
            await notice.edit_text(
                f"Уже поднята.\n{_list_item(alive)}",
                parse_mode="HTML",
                reply_markup=_open_keyboard(alive.url),
            )
            return

        try:
            session = await launch(cwd.name, str(cwd), timeout_s=config.launch_timeout_s)
        except TrustRequired as need:
            token = uuid.uuid4().hex[:8]
            trust_pending[token] = (need.tmux_name, need.cwd)
            await notice.edit_text(
                "🔐 Claude впервые видит этот каталог и ждёт подтверждения.\n"
                f"<code>{html.escape(need.cwd)}</code>\n\n"
                "Он получит право читать, менять и запускать здесь файлы.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Доверяю", callback_data=f"trust:{token}"
                            ),
                            InlineKeyboardButton(
                                text="Отмена", callback_data=f"notrust:{token}"
                            ),
                        ]
                    ]
                ),
            )
            return
        except LaunchError as exc:
            log.warning("launch failed for %s: %s", cwd, exc)
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

    @dp.message(Command("pwd", "ls"))
    async def cmd_pwd(message: Message) -> None:
        text, keyboard = _browse_card(state.cwd)
        await message.reply(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.message(Command("cd"))
    async def cmd_cd(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        target = parts[1] if len(parts) > 1 else ""
        try:
            state.set_cwd(browse.change_dir(state.cwd, target))
        except BrowseError as exc:
            await message.reply(f"❌ {html.escape(str(exc))}", parse_mode="HTML")
            return
        text, keyboard = _browse_card(state.cwd)
        await message.reply(text, parse_mode="HTML", reply_markup=keyboard)

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
        parts = (message.text or "").split()
        if len(parts) < 2:
            sessions = await list_sessions()
            if not sessions:
                await message.reply("Живых сессий нет. <b>/rc</b> &lt;репо&gt;", parse_mode="HTML")
                return
            await message.reply("\n\n".join(_list_item(s) for s in sessions), parse_mode="HTML")
            return

        query = parts[1]
        branch = parts[2] if len(parts) > 2 else None
        matches = resolve(query, await discovery.paths())
        if not matches:
            await message.reply(
                f"Не нашёл <code>{html.escape(query)}</code>. Список — /repos", parse_mode="HTML"
            )
            return
        if len(matches) == 1:
            await start_session(message, matches[0], branch)
            return

        rows = []
        for path in matches[:MAX_CHOICES]:
            token = uuid.uuid4().hex[:8]
            pending[token] = (path, branch)
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
        parts = (message.text or "").split()
        if len(parts) < 2:
            killed = await kill_all()
            await message.reply(f"Погашено сессий: {killed}." if killed else "Гасить нечего.")
            return
        name = parts[1]
        if await kill_session(name):
            # Worktree намеренно остаётся: в нём может лежать несохранённая работа.
            await message.reply(f"Сессия <b>{html.escape(name)}</b> погашена.", parse_mode="HTML")
        else:
            await message.reply(
                f"Нет живой сессии <code>{html.escape(name)}</code>.", parse_mode="HTML"
            )

    @dp.message(Command("wt"))
    async def cmd_wt(message: Message) -> None:
        items = await worktrees.list_all(config.worktree_root)
        if not items:
            await message.reply(
                "Worktree нет. <b>/rc</b> &lt;репо&gt; &lt;ветка&gt;", parse_mode="HTML"
            )
            return
        lines = [
            f"▸ <b>{html.escape(wt.name)}</b> · {'; '.join(wt.blockers) or 'чисто'}\n"
            f"{html.escape(wt.repo)} · <code>{html.escape(wt.branch)}</code>"
            for wt in items
        ]
        for part in chunks("\n\n".join(lines)):
            await message.answer(part, parse_mode="HTML")

    @dp.message(Command("wtrm"))
    async def cmd_wtrm(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.reply(
                "Использование: <b>/wtrm</b> &lt;имя&gt; [force]. Список — /wt", parse_mode="HTML"
            )
            return

        name = parts[1]
        force = len(parts) > 2 and parts[2].lower() == "force"
        path = config.worktree_root / name

        info = await worktrees.inspect(path) if path.is_dir() else None
        if info is None:
            await message.reply(
                f"Нет worktree <code>{html.escape(name)}</code>. Список — /wt", parse_mode="HTML"
            )
            return
        if info.blockers and not force:
            await message.reply(
                f"❌ Не удаляю: {html.escape('; '.join(info.blockers))}.\n"
                f"Если работа не нужна — <code>/wtrm {html.escape(name)} force</code>",
                parse_mode="HTML",
            )
            return

        # Сессия держит этот каталог: не погасив её, оставим Claude в исчезнувшем cwd.
        session = await find(str(path))
        if session is not None:
            await kill_session(session.name)

        try:
            await worktrees.remove(config.worktree_root, name, force=force)
        except WorktreeError as exc:
            await message.reply(
                f"❌ Не удалился.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML"
            )
            return

        note = " Сессия погашена." if session is not None else ""
        await message.reply(
            f"Worktree <b>{html.escape(name)}</b> удалён.{note}", parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("nav:"))
    async def on_nav(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        action = query.data[4:]
        await query.answer()
        if query.message is None:
            return

        if action == "here":
            await start_session(query.message, state.cwd, None)
            return

        if action == "up":
            state.set_cwd(state.cwd.parent)
        else:
            # Индекс, а не путь: в callback_data влезает 64 байта, а каталоги
            # переоткрываются от текущего cwd, так что ссылаться на них дёшево.
            children, _ = browse.entries(state.cwd)
            try:
                state.set_cwd(children[int(action)])
            except (ValueError, IndexError):
                await query.message.answer("Список устарел, повтори /pwd.")
                return

        text, keyboard = _browse_card(state.cwd)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:  # редактирование тем же текстом даёт ошибку — она не важна
            await query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.callback_query(F.data.startswith(("trust:", "notrust:")))
    async def on_trust(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        verdict, _, token = query.data.partition(":")
        waiting = trust_pending.pop(token, None)
        await query.answer()
        if query.message is None:
            return
        await query.message.edit_reply_markup(reply_markup=None)
        if waiting is None:
            await query.message.answer("Запрос устарел, повтори /rc.")
            return

        tmux_name, cwd = waiting
        if verdict == "notrust":
            await kill_tmux(tmux_name)
            await query.message.answer("Отменил, сессия погашена.")
            return

        await confirm_trust(tmux_name)
        try:
            # watch_trust=False: диалог ещё мгновение висит на экране после Enter,
            # и повторная проверка снова приняла бы его за неотвеченный.
            session = await await_url(
                tmux_name, cwd, timeout_s=config.launch_timeout_s, watch_trust=False
            )
        except LaunchError as exc:
            log.warning("launch failed after trust for %s: %s", cwd, exc)
            await query.message.answer(
                f"❌ Не поднялось.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML"
            )
            return

        await query.message.answer(
            _fresh_text(session), parse_mode="HTML", reply_markup=_open_keyboard(session.url)
        )

    @dp.callback_query(F.data.startswith("rc:"))
    async def on_pick(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        choice = pending.pop(query.data[3:], None)
        await query.answer()
        if query.message is None:
            return
        await query.message.edit_reply_markup(reply_markup=None)
        if choice is None:
            await query.message.answer("Выбор устарел, повтори /rc.")
            return
        target, branch = choice
        await start_session(query.message, target, branch)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
