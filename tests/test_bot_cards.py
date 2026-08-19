import re
from pathlib import Path

from clauderc import bot as bot_module
from clauderc.bot import (
    ResumeChoice,
    _died_text,
    _has_repos,
    _pop_resume_group,
    _resume_keyboard,
    _selected_targets,
    _sync_line,
    _sync_report_line,
)
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


def test_pop_resume_group_clears_sibling_tokens() -> None:
    # Два быстрых тапа по разным кнопкам одной карточки не должны поднять две
    # сессии: выбор одного варианта гасит соседние токены той же карточки.
    choice_a: ResumeChoice = (Path("/repos/oms"), None, None)
    choice_b: ResumeChoice = (Path("/repos/oms"), None, "last")
    pending: dict[str, tuple[str, ResumeChoice]] = {
        "t0": ("g1", choice_a),
        "t1": ("g1", choice_b),
        "t2": ("g2", (Path("/repos/geo"), None, None)),
    }

    picked = _pop_resume_group(pending, "t0")

    assert picked == choice_a
    assert "t1" not in pending, "сосед по карточке должен исчезнуть"
    assert "t2" in pending, "токен другой карточки трогать нельзя"


def test_pop_resume_group_unknown_token_returns_none() -> None:
    pending: dict[str, tuple[str, ResumeChoice]] = {"t0": ("g1", (Path("/repos/oms"), None, None))}

    assert _pop_resume_group(pending, "stale") is None
    assert "t0" in pending, "неизвестный токен не должен трогать чужую карточку"


def test_no_direct_kill_calls_bypass_watcher() -> None:
    """Прямой вызов kill_tmux/kill_all/kill_session мимо watcher — тихий баг.

    Такая сессия гаснет, но Watcher о ней не узнаёт и на следующем опросе
    доложит о ней как об упавшей — пользователь получит карточку «сессия
    завершилась» сразу после того, как сам её погасил.
    """
    source = Path(bot_module.__file__).read_text(encoding="utf-8")
    offenders = _DIRECT_KILL.findall(source)
    assert not offenders, offenders


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


def test_selected_targets_maps_indices_in_order() -> None:
    listing = [Path("/repos/a"), Path("/repos/b"), Path("/repos/c")]
    assert _selected_targets(listing, {2, 0}) == [Path("/repos/a"), Path("/repos/c")]


def test_selected_targets_drops_indices_the_listing_no_longer_has() -> None:
    # Листинг мог измениться между отрисовкой карточки и тапом (другой каталог,
    # исчезнувший репозиторий) — индексы вне диапазона не должны попасть в цели.
    listing = [Path("/repos/a")]
    assert _selected_targets(listing, {0, 1, 5}) == [Path("/repos/a")]


def test_selected_targets_empty_selection_or_listing() -> None:
    assert _selected_targets([], {0, 1}) == []
    assert _selected_targets([Path("/repos/a")], set()) == []
