import dataclasses
import datetime
import re
import time
from pathlib import Path

from aiogram.types import Chat, Message, User
from clauderc import bot as bot_module
from clauderc import passport
from clauderc.bot import (
    LaunchRequest,
    _apply_name,
    _browse_card,
    _bypass_failed_text,
    _chunk_report,
    _died_text,
    _has_repos,
    _is_own_prompt,
    _name_prompt,
    _pop_resume_group,
    _pull_line,
    _resume_keyboard,
    _same_session,
    _selected_targets,
    _session_card,
    _session_keyboard,
    _sync_line,
    _sync_report_line,
    _sync_unavailable_line,
)
from clauderc.remote import RemoteSession
from clauderc.sync import Outcome, RepoStatus, SyncResult
from clauderc.watch import Died

# Гашение обязано идти через Watcher — иначе намеренно погашенная сессия
# попадает в отчёт как упавшая (см. CLAUDE.md, «Точки гашения»). Ловим прямой
# вызов remote.kill_* мимо `watcher.`, чтобы регрессия не держалась на ручном
# грепе при следующей правке bot.py.
_DIRECT_KILL = re.compile(r"(?<!watcher\.)\b(?:kill_tmux|kill_all|kill_session)\(")


def test_resume_keyboard_lists_new_continue_and_conversations() -> None:
    markup = _resume_keyboard(
        [
            ("t0", "New session"),
            ("t1", "Continue last"),
            ("t2", "сделай релиз"),
        ]
    )
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["New session", "Continue last", "сделай релиз"]


def test_resume_keyboard_callback_data_fits_telegram_limit() -> None:
    # В callback_data влезает 64 байта; id диалога туда не кладём — только токен.
    markup = _resume_keyboard([("deadbeef", "и" * 200)])
    (button,) = markup.inline_keyboard[0]
    assert button.callback_data == "res:deadbeef"
    assert len((button.callback_data or "").encode()) <= 64


def test_died_text_names_the_directory() -> None:
    text = _died_text(Died(name="oms", tmux_name="rc-oms", cwd="/repos/oms"))
    assert "oms" in text
    assert "/repos/oms" in text


def test_died_text_escapes_html() -> None:
    text = _died_text(Died(name="a&b", tmux_name="rc-a-b", cwd="/repos/<x>"))
    assert "&amp;" in text
    assert "<x>" not in text


def test_bypass_failed_text_mentions_previous_session() -> None:
    # Прежняя сессия к этому моменту уже погашена (kill в actions.restart
    # прошёл) — молчание об этом оставило бы карточку без единой подсказки.
    text = _bypass_failed_text("timeout")
    assert "погашена" in text
    assert "timeout" in text


def test_bypass_failed_text_escapes_html() -> None:
    text = _bypass_failed_text("<boom>")
    assert "&lt;boom&gt;" in text
    assert "<boom>" not in text


def test_pop_resume_group_clears_sibling_tokens() -> None:
    # Два быстрых тапа по разным кнопкам одной карточки не должны поднять две
    # сессии: выбор одного варианта гасит соседние токены той же карточки.
    choice_a = LaunchRequest(target=Path("/repos/oms"))
    choice_b = LaunchRequest(target=Path("/repos/oms"), resume="last")
    pending: dict[str, tuple[str, LaunchRequest]] = {
        "t0": ("g1", choice_a),
        "t1": ("g1", choice_b),
        "t2": ("g2", LaunchRequest(target=Path("/repos/geo"))),
    }

    picked = _pop_resume_group(pending, "t0")

    assert picked == choice_a
    assert "t1" not in pending, "сосед по карточке должен исчезнуть"
    assert "t2" in pending, "токен другой карточки трогать нельзя"


def test_pop_resume_group_unknown_token_returns_none() -> None:
    pending: dict[str, tuple[str, LaunchRequest]] = {
        "t0": ("g1", LaunchRequest(target=Path("/repos/oms")))
    }

    assert _pop_resume_group(pending, "stale") is None
    assert "t0" in pending, "неизвестный токен не должен трогать чужую карточку"


def test_apply_name_skips_on_dash() -> None:
    req = LaunchRequest(target=Path("/r"), new_worktree=True)
    out = _apply_name(req, "-")
    assert out.name is None
    assert out.branch is not None and out.branch.startswith("wt/")


def test_apply_name_derives_branch_for_new_worktree() -> None:
    out = _apply_name(LaunchRequest(target=Path("/r"), new_worktree=True), "MCP fix")
    assert out.name == "MCP fix"
    assert out.branch == "wt/mcp-fix"


def test_apply_name_keeps_explicit_branch() -> None:
    out = _apply_name(LaunchRequest(target=Path("/r"), branch="feat/x"), "x")
    assert out.branch == "feat/x"


def test_apply_name_truncates_long_name() -> None:
    # Длинное имя не должно уезжать в кнопку/заголовок целиком — обрезаем до
    # MAX_SESSION_NAME_LEN, и ветка для нового worktree считается уже от
    # обрезанного имени, а не от исходного.
    out = _apply_name(LaunchRequest(target=Path("/r"), new_worktree=True), "x" * 50)
    assert out.name is not None and len(out.name) == bot_module.MAX_SESSION_NAME_LEN
    assert out.branch == "wt/" + "x" * bot_module.MAX_SESSION_NAME_LEN


def test_name_prompt_is_a_force_reply() -> None:
    text, markup = _name_prompt()
    assert "ответом" in text
    assert markup.force_reply is True and markup.selective is True


def test_no_direct_kill_calls_bypass_watcher() -> None:
    """Прямой вызов kill_tmux/kill_all/kill_session мимо watcher — тихий баг.

    Такая сессия гаснет, но Watcher о ней не узнаёт и на следующем опросе
    доложит о ней как об упавшей — пользователь получит карточку «сессия
    завершилась» сразу после того, как сам её погасил.
    """
    source = Path(bot_module.__file__).read_text(encoding="utf-8")
    offenders = _DIRECT_KILL.findall(source)
    assert not offenders, f"нашёл гашение мимо watcher: {offenders}"


def _status(**kwargs: object) -> RepoStatus:
    base: dict[str, object] = {
        "path": Path("/repos/alpha"),
        "branch": "main",
        "dirty": False,
        "ahead": 0,
        "behind": 0,
        "upstream": "origin/main",
        "detached": False,
    }
    base.update(kwargs)
    return RepoStatus(**base)  # type: ignore[arg-type]


def test_sync_line_marks_selection() -> None:
    assert _sync_line(_status(), selected=True, label="alpha").startswith("☑")
    assert _sync_line(_status(), selected=False, label="alpha").startswith("☐")


def test_sync_line_shows_behind_and_ahead() -> None:
    assert "↓3" in _sync_line(_status(behind=3), selected=False, label="alpha")
    assert "↑2" in _sync_line(_status(ahead=2), selected=False, label="alpha")


def test_sync_line_marks_dirty_and_clean() -> None:
    assert "✎" in _sync_line(_status(dirty=True), selected=False, label="alpha")
    assert "✓" in _sync_line(_status(), selected=False, label="alpha")


def test_sync_line_marks_missing_upstream() -> None:
    assert "⚠" in _sync_line(_status(upstream=None), selected=False, label="alpha")


def test_sync_line_escapes_html() -> None:
    line = _sync_line(_status(branch="<x>"), selected=False, label="a&b")
    assert "&amp;" in line
    assert "<x>" not in line


def test_sync_line_uses_disambiguated_label_not_bare_name() -> None:
    # Одноимённые репозитории (два клона, ребёнок с тем же именем) неотличимы
    # по `path.name` — строка обязана показывать переданный label, а не имя.
    line = _sync_line(_status(path=Path("/repos/dirA/repo")), selected=False, label="dirA/repo")
    assert "dirA/repo" in line


def test_sync_report_line_covers_every_outcome() -> None:
    for outcome in Outcome:
        result = SyncResult(Path("/repos/alpha"), outcome, "причина", "main")
        assert "alpha" in _sync_report_line(result, label="alpha")


def test_has_repos_detects_repo_child(tmp_path: Path) -> None:
    (tmp_path / "alpha" / ".git").mkdir(parents=True)
    (tmp_path / "beta").mkdir()

    assert _has_repos(tmp_path) is True
    assert _has_repos(tmp_path / "beta") is False


def test_has_repos_sees_worktree_gitfile(tmp_path: Path) -> None:
    # У git worktree `.git` — файл, а не каталог; is_dir() его не увидит.
    child = tmp_path / "wt"
    child.mkdir()
    (child / ".git").write_text("gitdir: /elsewhere\n")

    assert _has_repos(tmp_path) is True


def test_has_repos_true_when_cwd_itself_is_a_repo(tmp_path: Path) -> None:
    # Карточка Sync показывает и сам каталог, если он репозиторий (см. list_repos) —
    # кнопка должна появляться и тогда, даже без единого репозитория-ребёнка.
    (tmp_path / ".git").mkdir()

    assert _has_repos(tmp_path) is True


def test_selected_targets_keeps_listing_order() -> None:
    listing = [Path("/repos/a"), Path("/repos/b"), Path("/repos/c")]
    chosen = {Path("/repos/c"), Path("/repos/a")}
    assert _selected_targets(listing, chosen) == [Path("/repos/a"), Path("/repos/c")]


def test_selected_targets_drops_paths_the_listing_no_longer_has() -> None:
    # Листинг мог измениться между отрисовкой карточки и тапом (другой
    # каталог, исчезнувший репозиторий) — путь, которого больше нет в
    # листинге, не должен попасть в цели.
    listing = [Path("/repos/a")]
    chosen = {Path("/repos/a"), Path("/repos/gone")}
    assert _selected_targets(listing, chosen) == [Path("/repos/a")]


def test_selected_targets_empty_selection_or_listing() -> None:
    assert _selected_targets([], {Path("/repos/a")}) == []
    assert _selected_targets([Path("/repos/a")], set()) == []


def test_selected_targets_survives_reordering_when_new_repo_appears() -> None:
    # Ровно тот сценарий, который чинили: рядом склонировали ещё один
    # репозиторий между отрисовками. Индекс сместился бы на соседа; путь — нет.
    chosen = {Path("/repos/beta")}
    grown_listing = [Path("/repos/gamma"), Path("/repos/alpha"), Path("/repos/beta")]

    assert _selected_targets(grown_listing, chosen) == [Path("/repos/beta")]


def test_sync_line_marks_live_session() -> None:
    line = _sync_line(_status(), selected=False, label="alpha", live_session=True)
    assert "🔒" in line
    assert "🔒" not in _sync_line(_status(), selected=False, label="alpha")


def test_chunk_report_keeps_short_report_in_one_chunk() -> None:
    lines = ["a" * 10, "b" * 10, "c" * 10]
    assert _chunk_report(lines) == ["\n".join(lines)]


def test_chunk_report_splits_by_char_limit() -> None:
    lines = ["x" * 30 for _ in range(5)]
    chunks = _chunk_report(lines, limit=70)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 70


def test_chunk_report_never_splits_a_single_line() -> None:
    # Строка длиннее лимита не имеет права разорваться пополам — целиком в
    # свой собственный кусок, даже если этот кусок сам переваливает лимит.
    long_line = "y" * 200
    chunks = _chunk_report(["short", long_line, "short2"], limit=50)
    assert long_line in chunks


def test_chunk_report_empty_input() -> None:
    assert _chunk_report([]) == []


def test_sync_unavailable_line_names_repo_and_reason() -> None:
    line = _sync_unavailable_line("alpha")
    assert "alpha" in line
    assert "❔" in line


def test_sync_unavailable_line_escapes_html() -> None:
    line = _sync_unavailable_line("a&b")
    assert "&amp;" in line


def _session(url: str = "https://claude.ai/code/session_01ABC") -> RemoteSession:
    return RemoteSession(
        name="oms",
        tmux_name="rc-oms",
        cwd="/Users/n/code/oms",
        url=url,
        created_at=int(time.time()) - 60,
        tmux_id="$1",
    )


def test_session_card_is_the_passport_html() -> None:
    session = RemoteSession(
        name="oms@x",
        tmux_name="session_01ABC",
        cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC",
        created_at=int(time.time()),
        label="oms@x",
    )
    p = passport.build(session, host="m1", tree=None)
    text = _session_card(p)
    assert "<b>oms@x</b>" in text
    assert "ssh m1 -t" in text
    assert "claude-rc --host m1" in text


def test_same_session_recognises_the_session_from_the_card() -> None:
    session = _session()
    assert _same_session(session, session.tmux_id, session.created_at) is session


def test_same_session_survives_a_rename() -> None:
    # `await_url` переименовывает сессию в её id — оба признака это сохраняют.
    session = _session()
    renamed = dataclasses.replace(session, tmux_name="session_01ABC", name="oms@x")
    assert _same_session(renamed, session.tmux_id, session.created_at) is renamed


def test_same_session_rejects_a_relaunch_in_the_same_directory() -> None:
    """Устаревшая кнопка Stop не имеет права погасить чужую работу.

    Каталог — ключ сессии, но не удостоверение: прежняя могла умереть, а в том
    же каталоге подняться новая.
    """
    session = _session()
    assert _same_session(session, "$2", session.created_at) is None


def test_same_session_rejects_a_relaunch_in_the_very_same_second() -> None:
    # Ровно случай `restart`: время создания то же, экземпляр другой.
    session = _session()
    fresh = dataclasses.replace(session, tmux_id="$7")
    assert fresh.created_at == session.created_at
    assert _same_session(fresh, session.tmux_id, session.created_at) is None


def test_same_session_rejects_a_repeated_tmux_id_after_a_server_restart() -> None:
    # `$N` уникален только на время жизни tmux-сервера: погасив последнюю
    # сессию, мы уносим сервер, и новый раздаёт `$0` заново. Разводит их
    # время создания — сессия поднята уже позже.
    session = _session()
    fresh = dataclasses.replace(session, created_at=session.created_at + 30)
    assert fresh.tmux_id == session.tmux_id
    assert _same_session(fresh, session.tmux_id, session.created_at) is None


def test_same_session_handles_a_directory_with_no_session() -> None:
    assert _same_session(None, "$1", 1000) is None


def test_pull_line_reports_what_the_pull_did() -> None:
    # Молча тянуть нельзя: человек должен видеть, на каком коде поднимается сессия.
    result = SyncResult(Path("/repos/oms"), Outcome.updated, "подтянуто 3", "main")
    assert _pull_line(result) == "⤵️ main: подтянуто 3"


def test_pull_line_names_a_directory_that_is_not_a_repo() -> None:
    result = SyncResult(Path("/services"), Outcome.skipped, "не рабочая копия git", "?")
    assert _pull_line(result) == "⤵️ не git-репозиторий, тянуть нечего"


def test_pull_line_escapes_html() -> None:
    result = SyncResult(Path("/repos/oms"), Outcome.failed, "<evil>", "<b>")
    line = _pull_line(result)
    assert "&lt;evil&gt;" in line and "&lt;b&gt;" in line


def test_session_keyboard_has_every_lever() -> None:
    markup = _session_keyboard("tok", "https://claude.ai/code/session_A")
    data = [b.callback_data or b.url for row in markup.inline_keyboard for b in row]
    assert data == [
        "https://claude.ai/code/session_A",
        "stop:tok",
        "byp:tok",
        "mcp:tok",
        "tail:tok",
        "ren:tok",
    ]
    assert all(
        len((b.callback_data or "").encode()) <= 64 for row in markup.inline_keyboard for b in row
    )


def test_browse_card_offers_bypass_start(tmp_path: Path) -> None:
    _, keyboard = _browse_card(tmp_path)
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "nav:here" in data and "nav:bypass" in data


def _reply_from(user_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=user_id, is_bot=user_id == 42, first_name="x"),
        text="Как назвать сессию?",
    )


def test_reply_to_our_own_prompt_is_recognised() -> None:
    # Заявки живут в памяти: после перезапуска бота ForceReply в чате остаётся,
    # а ждущего его нет — и молчание в ответ выглядит поломкой.
    assert _is_own_prompt(_reply_from(42), 42) is True


def test_reply_to_someone_else_is_not_our_business() -> None:
    assert _is_own_prompt(_reply_from(7), 42) is False


def test_reply_without_author_is_not_ours() -> None:
    orphan = _reply_from(42).model_copy(update={"from_user": None})
    assert _is_own_prompt(orphan, 42) is False
