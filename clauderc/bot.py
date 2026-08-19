import asyncio
import html
import logging
import os
import time
import uuid
from collections import Counter
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
)

from clauderc import browse, history, paths, worktrees
from clauderc.browse import BrowseError
from clauderc.config import Config, load_config
from clauderc.remote import (
    PREFIX as REMOTE_PREFIX,
)
from clauderc.remote import (
    LaunchError,
    RemoteSession,
    TrustRequired,
    await_url,
    confirm_trust,
    find,
    launch,
    list_sessions,
    tmux_available,
)
from clauderc.repos import discover, resolve
from clauderc.state import State
from clauderc.sync import Outcome, RepoStatus, SyncResult
from clauderc.watch import Died, Watcher
from clauderc.worktrees import Worktree, WorktreeError

log = logging.getLogger("clauderc")

DISCOVERY_TTL_S = 60.0
MAX_CHOICES = 8
MAX_PROJECT_BUTTONS = 60
MAX_TREE_CARDS = 10
HELP = (
    "Поднимаю сессии Claude Code с Remote Control — дальше работа в приложении Claude.\n\n"
    "<b>Куда идём</b>\n"
    "<b>📁 PWD</b> (<code>/pwd</code>) — где я, с кнопками по подкаталогам\n"
    "<b>📚 Projects</b> (<code>/repos</code>) — все git-репозитории, тап переносит внутрь\n"
    "<code>/cd</code> &lt;путь&gt; — перейти "
    "(<code>..</code>, <code>~</code>, относительный, абсолютный)\n\n"
    "<b>Запуск</b>\n"
    "<b>▶️ Start Claude RC</b> — сессия в текущем каталоге\n"
    "<b>🌿 New worktree</b> — сессия в свежем worktree, ветка по времени; "
    "так работают параллельно с уже запущенной\n"
    "<code>/rc</code> &lt;репо&gt; [ветка] — то же по имени, без хождения\n\n"
    "<b>Что запущено</b>\n"
    "<b>💬 Chats</b> (<code>/rc</code>) — живые сессии с кнопками "
    "<b>Open in Claude</b> и <b>⏹ Stop</b>, а следом worktree, оставшиеся без сессии: "
    "их можно поднять заново или удалить\n"
    "<code>/wt</code> — все worktree, включая занятые\n"
    "<code>/rckill</code> &lt;имя&gt; — погасить сессию (без имени — все)\n"
    "<code>/wtrm</code> &lt;имя&gt; [force] — удалить worktree\n\n"
    "Сессии живут в tmux и переживают рестарт бота."
)

# Постоянная клавиатура: команд немного, и на телефоне они должны быть под пальцем.
BTN_PWD = "📁 PWD"
BTN_SESSIONS = "💬 Chats"
BTN_REPOS = "📚 Projects"
BTN_HELP = "⌨️ Commands"

# Worktree своей кнопки внизу не имеет: без сессии он — остаток работы, и место
# ему рядом с сессиями в Chats, а не в отдельном разделе.
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_PWD), KeyboardButton(text=BTN_SESSIONS)],
        [KeyboardButton(text=BTN_REPOS), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def _is_authorized(from_user: User | None, allowed_user_id: int) -> bool:
    """Пропускаем только владельца. from_user=None (канал/анонимный админ) → отказ (fail-closed)."""
    return from_user is not None and from_user.id == allowed_user_id


def _live_message(query: CallbackQuery) -> Message | None:
    """Сообщение кнопки, если его ещё можно редактировать.

    Для слишком старых сообщений Telegram присылает InaccessibleMessage: у него нет
    ни edit_text, ни answer, и обращение к ним падает.
    """
    return query.message if isinstance(query.message, Message) else None


def _open_keyboard(url: str) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open in Claude", url=url)]]
    )


def _resume_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Кнопки выбора диалога: по строке на вариант, в callback_data — только токен."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"res:{token}")]
            for token, label in items
        ]
    )


ResumeChoice = tuple[Path, str | None, str | None]


def _pop_resume_group(
    pending: dict[str, tuple[str, ResumeChoice]], token: str
) -> ResumeChoice | None:
    """Достаёт выбранный вариант и гасит остальные токены той же карточки.

    Варианты одной карточки независимы только с виду: выбор любого из них должен
    сделать соседние недействительными, иначе два быстрых тапа по разным кнопкам
    поднимут две RC-сессии в одном каталоге.
    """
    entry = pending.pop(token, None)
    if entry is None:
        return None
    group, choice = entry
    for other in [key for key, (other_group, _) in pending.items() if other_group == group]:
        pending.pop(other, None)
    return choice


def _died_text(died: Died) -> str:
    return (
        f"⚠️ Сессия <b>{html.escape(died.name)}</b> завершилась\n"
        f"<code>{html.escape(died.cwd)}</code>"
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


def _list_item(session: RemoteSession, tree: Worktree | None = None) -> str:
    lines = [
        f"▸ <b>{html.escape(session.name)}</b> · {_uptime(session.uptime_s())}",
        f"<code>{html.escape(session.cwd)}</code>",
    ]
    if tree is not None:
        lines.append(f"🌿 <code>{html.escape(tree.branch)}</code> · {_tree_state(tree)}")
    lines.append(_link_line(session))
    return "\n".join(lines)


def _tree_state(tree: Worktree) -> str:
    return html.escape("; ".join(tree.blockers)) or "чисто"


def _tree_text(tree: Worktree, session: RemoteSession | None) -> str:
    head = "🌿 <b>{}</b> · {}".format(
        html.escape(tree.name), "сессия жива" if session is not None else "без сессии"
    )
    body = (
        f"{html.escape(tree.repo)} · <code>{html.escape(tree.branch)}</code> · {_tree_state(tree)}"
    )
    if session is not None and session.url:
        return f"{head}\n{body}\n{html.escape(session.url)}"
    return f"{head}\n{body}"


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

    launch_row = [InlineKeyboardButton(text="▶️ Start Claude RC", callback_data="nav:here")]
    # Вторая сессия в том же каталоге дралась бы за индекс и ветку — только через worktree.
    if browse.is_repo(cwd):
        launch_row.append(InlineKeyboardButton(text="🌿 New worktree", callback_data="nav:newwt"))
    rows.append(launch_row)

    text = f"📁 <code>{html.escape(str(cwd))}</code>{mark}"
    if not children:
        text += "\nПодкаталогов нет."
    elif total > len(children):
        text += f"\nПоказал {len(children)} из {total} — остальные через <b>/cd</b>."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _projects_card(
    paths: list[Path], roots: tuple[Path, ...], limit: int = MAX_PROJECT_BUTTONS
) -> tuple[str, InlineKeyboardMarkup]:
    """Плоский список git-репозиториев кнопками: прыжок туда, куда ходить долго."""
    shown = paths[:limit]
    # Одно и то же имя встречается в дереве не раз (два клона репозитория) —
    # одинаковые подписи на кнопках выбрать не дают, поэтому добавляем родителя.
    seen = Counter(path.name for path in shown)

    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for index, path in enumerate(shown):
        title = path.name if seen[path.name] == 1 else f"{path.parent.name}/{path.name}"
        pair.append(InlineKeyboardButton(text=title, callback_data=f"jump:{index}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    text = "📚 Проекты — тап переносит в каталог."
    if len(paths) > len(shown):
        text += f"\nПоказал {len(shown)} из {len(paths)}, остальные через <b>/cd</b>."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _has_repos(cwd: Path) -> bool:
    """Есть ли смысл предлагать Sync: хотя бы один подкаталог — git-репозиторий."""
    try:
        children = list(cwd.iterdir())
    except OSError:
        return False
    return any(browse.is_repo(child) for child in children)


def _sync_line(status: RepoStatus, *, selected: bool) -> str:
    """Строка репозитория в режиме выбора: имя, ветка и в каком он состоянии."""
    box = "☑" if selected else "☐"
    marks = []
    if status.upstream is None:
        marks.append("⚠")
    if status.behind:
        marks.append(f"↓{status.behind}")
    if status.ahead:
        marks.append(f"↑{status.ahead}")
    marks.append("✎" if status.dirty else "✓")
    return (
        f"{box} <b>{html.escape(status.path.name)}</b> "
        f"<code>{html.escape(status.branch)}</code> {' '.join(marks)}"
    )


def _sync_report_line(result: SyncResult) -> str:
    mark = {
        Outcome.updated: "⤵",
        Outcome.already: "=",
        Outcome.skipped: "·",
        Outcome.failed: "❌",
    }[result.outcome]
    return (
        f"{mark} <b>{html.escape(result.path.name)}</b> "
        f"<code>{html.escape(result.branch)}</code> — {html.escape(result.detail)}"
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config_path = paths.config_file()
    try:
        config = load_config(config_path)
    except (OSError, ValueError, KeyError) as exc:
        # Тот же контракт, что у doctor/--branch в cli.py: битый конфиг —
        # понятное сообщение и чистый выход, а не трейсбек в логе приложения.
        raise SystemExit(f"config.toml не годится: {config_path}: {exc}") from exc
    if not tmux_available():
        raise SystemExit("tmux не найден в PATH — поставь через `brew install tmux`")

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    discovery = Discovery(config)
    state = State(config.state_path, config.rc_roots[0])
    watcher = Watcher()
    # Кандидаты для кнопок выбора при неоднозначном запросе. Токен короткий,
    # потому что в callback_data влезает 64 байта — полный путь туда не поместится.
    pending: dict[str, tuple[Path, str | None]] = {}
    # Сессии, которые ждут ответа на диалог доверия каталогу.
    trust_pending: dict[str, tuple[str, str]] = {}
    stop_pending: dict[str, str] = {}
    tree_pending: dict[str, Path] = {}
    # Значение — (id карточки, выбор): выбор любого варианта гасит остальные
    # токены той же карточки, чтобы два тапа не подняли две сессии в одном каталоге.
    resume_pending: dict[str, tuple[str, ResumeChoice]] = {}

    async def start_session(
        message: Message, target: Path, branch: str | None, resume: str | None = None
    ) -> None:
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
            session = await launch(
                cwd.name, str(cwd), timeout_s=config.launch_timeout_s, resume=resume
            )
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
                            InlineKeyboardButton(text="Trust", callback_data=f"trust:{token}"),
                            InlineKeyboardButton(text="Cancel", callback_data=f"notrust:{token}"),
                        ]
                    ]
                ),
            )
            return
        except LaunchError as exc:
            log.warning("launch failed for %s: %s", cwd, exc)
            if exc.tmux_name:
                # await_url уже видел сессию мёртвой (таймаут, упала сама
                # или исчезла между capture и list) — без метки watcher
                # опросил бы её как упавшую следом за этим же сообщением.
                watcher.expect_death(exc.tmux_name)
            await notice.edit_text(
                f"❌ Не поднялось.\n<pre>{html.escape(str(exc))}</pre>"[:3800],
                parse_mode="HTML",
            )
            return

        await notice.edit_text(
            _fresh_text(session), parse_mode="HTML", reply_markup=_open_keyboard(session.url)
        )

    async def offer_start(message: Message, target: Path, branch: str | None) -> None:
        """Запуск с выбором диалога, если в каталоге уже есть история.

        Для новой ветки истории быть не может — там свежий worktree, и лишний
        шаг только мешал бы.
        """
        if branch is not None:
            await start_session(message, target, branch)
            return

        found = history.conversations(str(target))
        if not found:
            await start_session(message, target, None)
            return

        group = uuid.uuid4().hex[:8]
        items: list[tuple[str, str]] = []
        for label, resume in [("New session", None), ("Continue last", "last")]:
            token = uuid.uuid4().hex[:8]
            resume_pending[token] = (group, (target, None, resume))
            items.append((token, label))
        for conversation in found:
            token = uuid.uuid4().hex[:8]
            resume_pending[token] = (group, (target, None, conversation.session_id))
            items.append((token, conversation.preview))

        await message.answer(
            f"В <code>{html.escape(str(target))}</code> уже есть диалоги. Что поднимаем?",
            parse_mode="HTML",
            reply_markup=_resume_keyboard(items),
        )

    async def send_tree_card(
        message: Message, tree: Worktree, session: RemoteSession | None
    ) -> None:
        token = uuid.uuid4().hex[:8]
        tree_pending[token] = tree.path
        row = []
        if session is not None and session.url:
            row.append(InlineKeyboardButton(text="Open in Claude", url=session.url))
        elif session is None:
            row.append(InlineKeyboardButton(text="▶️ Start", callback_data=f"wtstart:{token}"))
        row.append(InlineKeyboardButton(text="🗑 Remove", callback_data=f"wtrm:{token}"))
        await message.answer(
            _tree_text(tree, session),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[row]),
        )

    async def show_chats(message: Message) -> None:
        """Всё, что запущено или осталось: живые сессии плюс worktree без сессии."""
        sessions = await list_sessions()
        trees = {
            os.path.realpath(t.path): t for t in await worktrees.list_all(config.worktree_root)
        }
        occupied: set[str] = set()

        # По сообщению на сессию: гасить надо конкретную, и кнопка должна быть рядом
        # со своей ссылкой, а не в общей простыне.
        for session in sessions:
            real = os.path.realpath(session.cwd)
            occupied.add(real)
            token = uuid.uuid4().hex[:8]
            # Имя в callback_data не кладём: там 64 байта, а путь worktree с кириллицей
            # за лимит выходит легко.
            stop_pending[token] = session.tmux_name
            rows = []
            if session.url:
                rows.append([InlineKeyboardButton(text="Open in Claude", url=session.url)])
            rows.append([InlineKeyboardButton(text="⏹ Stop", callback_data=f"stop:{token}")])
            await message.answer(
                _list_item(session, trees.get(real)),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )

        orphans = [tree for real, tree in trees.items() if real not in occupied]
        if not sessions and not orphans:
            await message.reply("Живых сессий нет. <b>/rc</b> &lt;репо&gt;", parse_mode="HTML")
            return

        for tree in orphans[:MAX_TREE_CARDS]:
            await send_tree_card(message, tree, None)
        if len(orphans) > MAX_TREE_CARDS:
            await message.answer(
                f"…и ещё {len(orphans) - MAX_TREE_CARDS} worktree без сессии — <code>/wt</code>",
                parse_mode="HTML",
            )

    @dp.message(lambda event: not _is_authorized(event.from_user, config.allowed_user_id))
    async def reject_strangers(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        log.warning("dropped message from user_id=%s", uid)

    @dp.message(Command("start", "help"))
    @dp.message(F.text == BTN_HELP)
    async def cmd_help(message: Message) -> None:
        await message.reply(HELP, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)

    @dp.message(Command("pwd", "ls"))
    @dp.message(F.text == BTN_PWD)
    async def cmd_pwd(message: Message) -> None:
        text, keyboard = _browse_card(state.cwd)
        await message.reply(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.message(F.text == BTN_SESSIONS)
    async def cmd_sessions(message: Message) -> None:
        await show_chats(message)

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
    @dp.message(F.text == BTN_REPOS)
    async def cmd_repos(message: Message) -> None:
        paths = await discovery.paths(refresh=True)
        if not paths:
            await message.reply("Ничего не нашёл. Проверь rc_roots в config.toml.")
            return
        text, keyboard = _projects_card(paths, config.rc_roots)
        await message.reply(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.message(Command("rc"))
    async def cmd_rc(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 2:
            await show_chats(message)
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
            await offer_start(message, matches[0], branch)
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
            killed = await watcher.kill_all()
            await message.reply(f"Погашено сессий: {killed}." if killed else "Гасить нечего.")
            return
        name = parts[1]
        if await watcher.kill_named(name):
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
        for tree in items[:MAX_TREE_CARDS]:
            await send_tree_card(message, tree, await find(str(tree.path)))
        if len(items) > MAX_TREE_CARDS:
            await message.answer(f"…и ещё {len(items) - MAX_TREE_CARDS}.")

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
            await watcher.kill(session.tmux_name)

        try:
            await worktrees.remove(config.worktree_root, name, force=force)
        except WorktreeError as exc:
            await message.reply(
                f"❌ Не удалился.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML"
            )
            return

        note = " Сессия погашена." if session is not None else ""
        await message.reply(f"Worktree <b>{html.escape(name)}</b> удалён.{note}", parse_mode="HTML")

    @dp.callback_query(F.data.startswith("wtstart:"))
    async def on_tree_start(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        path = tree_pending.get((query.data or "").removeprefix("wtstart:"))
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        if path is None or not path.is_dir():
            await message.answer("Список устарел, повтори /wt.")
            return
        await message.edit_reply_markup(reply_markup=None)
        # Ветка уже выкачена в этом каталоге — второй worktree заводить не нужно.
        await offer_start(message, path, None)

    @dp.callback_query(F.data.startswith(("wtrm:", "wtrmf:")))
    async def on_tree_remove(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        verdict, _, token = (query.data or "").partition(":")
        forced = verdict == "wtrmf"
        path = tree_pending.get(token)
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        if path is None:
            await message.edit_reply_markup(reply_markup=None)
            await message.answer("Список устарел, повтори /wt.")
            return

        info = await worktrees.inspect(path) if path.is_dir() else None
        if info is None:
            tree_pending.pop(token, None)
            await message.edit_text("Worktree уже нет.")
            return

        if info.blockers and not forced:
            # Токен оставляем: он нужен кнопке подтверждения.
            await message.edit_text(
                f"{_tree_text(info, None)}\n\n❌ Не удаляю: {_tree_state(info)}.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🗑 Remove anyway", callback_data=f"wtrmf:{token}"
                            )
                        ]
                    ]
                ),
            )
            return

        tree_pending.pop(token, None)
        # Сессия могла подняться уже после показа карточки.
        session = await find(str(path))
        if session is not None:
            await watcher.kill(session.tmux_name)
        try:
            await worktrees.remove(config.worktree_root, path.name, force=forced)
        except WorktreeError as exc:
            await message.edit_text(
                f"❌ Не удалился.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML"
            )
            return

        note = " Сессия погашена." if session is not None else ""
        await message.edit_text(
            f"🗑 Worktree <b>{html.escape(path.name)}</b> удалён.{note}", parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("stop:"))
    async def on_stop(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        tmux_name = stop_pending.pop((query.data or "").removeprefix("stop:"), None)
        message = _live_message(query)
        if tmux_name is None:
            await query.answer("Список устарел")
            if message is not None:
                await message.edit_reply_markup(reply_markup=None)
            return

        killed = await watcher.kill(tmux_name)
        await query.answer("Погашена" if killed else "Уже не жива")
        if message is None:
            return
        name = tmux_name.removeprefix(REMOTE_PREFIX)
        # Worktree намеренно остаётся: там может лежать несохранённая работа.
        await message.edit_text(
            f"⏹ Сессия <b>{html.escape(name)}</b> погашена."
            if killed
            else f"Сессия <b>{html.escape(name)}</b> уже не жива.",
            parse_mode="HTML",
        )

    @dp.callback_query(F.data.startswith("jump:"))
    async def on_jump(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        # Без refresh: список должен совпадать с тем, по которому только что тапнули.
        paths = await discovery.paths()
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        try:
            state.set_cwd(paths[int((query.data or "").removeprefix("jump:"))])
        except (ValueError, IndexError):
            await message.answer("Список устарел, повтори /repos.")
            return
        text, keyboard = _browse_card(state.cwd)
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.callback_query(F.data.startswith("nav:"))
    async def on_nav(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        action = (query.data or "").removeprefix("nav:")
        await query.answer()
        message = _live_message(query)
        if message is None:
            return

        if action == "here":
            await offer_start(message, state.cwd, None)
            return

        if action == "newwt":
            await start_session(message, state.cwd, worktrees.generate_branch())
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
                await message.answer("Список устарел, повтори /pwd.")
                return

        text, keyboard = _browse_card(state.cwd)
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:  # редактирование тем же текстом даёт ошибку — она не важна
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    @dp.callback_query(F.data.startswith(("trust:", "notrust:")))
    async def on_trust(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        verdict, _, token = (query.data or "").partition(":")
        waiting = trust_pending.pop(token, None)
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        await message.edit_reply_markup(reply_markup=None)
        if waiting is None:
            await message.answer("Запрос устарел, повтори /rc.")
            return

        tmux_name, cwd = waiting
        if verdict == "notrust":
            await watcher.kill(tmux_name)
            await message.answer("Отменил, сессия погашена.")
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
            if exc.tmux_name:
                # await_url уже видел сессию мёртвой (таймаут, упала сама
                # или исчезла между capture и list) — без метки watcher
                # опросил бы её как упавшую следом за этим же сообщением.
                watcher.expect_death(exc.tmux_name)
            await message.answer(
                f"❌ Не поднялось.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML"
            )
            return

        await message.answer(
            _fresh_text(session), parse_mode="HTML", reply_markup=_open_keyboard(session.url)
        )

    @dp.callback_query(F.data.startswith("rc:"))
    async def on_pick(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        choice = pending.pop((query.data or "").removeprefix("rc:"), None)
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        await message.edit_reply_markup(reply_markup=None)
        if choice is None:
            await message.answer("Выбор устарел, повтори /rc.")
            return
        target, branch = choice
        await offer_start(message, target, branch)

    @dp.callback_query(F.data.startswith("res:"))
    async def on_resume(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        choice = _pop_resume_group(resume_pending, (query.data or "").removeprefix("res:"))
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        if choice is None:
            await message.answer("Выбор устарел, повтори запуск.")
            return
        await message.edit_reply_markup(reply_markup=None)
        target, branch, resume = choice
        await start_session(message, target, branch, resume)

    async def on_died(died: Died) -> None:
        # Watcher зовёт колбэк в цикле по всем упавшим за один опрос сессиям;
        # необработанное исключение оборвёт цикл, а его снимок уже сменился —
        # оставшиеся падения того же опроса тогда пропадут без следа.
        try:
            token = uuid.uuid4().hex[:8]
            resume_pending[token] = (token, (Path(died.cwd), None, "last"))
            await bot.send_message(
                config.allowed_user_id,
                _died_text(died),
                parse_mode="HTML",
                reply_markup=_resume_keyboard([(token, "↻ Resume")]),
            )
        except Exception:
            log.warning("failed to report died session %s", died.tmux_name, exc_info=True)

    # Список — удобство при рестарте бота, а не условие запуска: ошибка Telegram
    # здесь (бота заблокировали, сеть недоступна) не должна срывать поллинг.
    try:
        for session in await list_sessions():
            await bot.send_message(
                config.allowed_user_id,
                _list_item(session),
                parse_mode="HTML",
                reply_markup=_open_keyboard(session.url),
            )
    except Exception:
        log.warning("failed to send startup session list", exc_info=True)

    watch_task = asyncio.create_task(watcher.run(on_died))
    try:
        await dp.start_polling(bot)
    finally:
        watch_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
