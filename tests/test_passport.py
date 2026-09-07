import dataclasses
import html
import json
from pathlib import Path

import pytest
from clauderc import passport, remote, state_probe
from clauderc.remote import RemoteSession
from clauderc.worktrees import Worktree


def _session(tmux_name: str = "session_01ABC", label: str = "oms@mcp-fix") -> RemoteSession:
    return RemoteSession(
        name=label or "oms",
        tmux_name=tmux_name,
        cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC" if tmux_name.startswith("session_") else "",
        created_at=0,
        label=label,
    )


def _tree() -> Worktree:
    return Worktree(path=Path("/repos/oms"), repo="oms", branch="mcp-fix", dirty=False, unpushed=0)


def test_attach_line_is_local_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    assert passport.attach_line("session_01ABC", "") == "tmux attach -d -t =session_01ABC"


def test_attach_line_wraps_in_ssh_with_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    # -t: без tty tmux ответит «open terminal failed». Внутренняя команда — одной
    # квотированной строкой, чтобы удалённый shell не разобрал её на куски.
    assert (
        passport.attach_line("session_01ABC", "m1")
        == "ssh m1 -t 'tmux attach -d -t =session_01ABC'"
    )


def test_cli_prefix() -> None:
    assert passport.cli_prefix("") == "claude-rc"
    assert passport.cli_prefix("m1") == "claude-rc --host m1"
    # Хост с пробелом невозможен в ssh, но квотинг не должен зависеть от удачи.
    assert passport.cli_prefix("a b") == "claude-rc --host 'a b'"


def test_build_takes_session_id_from_renamed_tmux_name() -> None:
    p = passport.build(_session(), host="m1", tree=_tree())
    assert p.session_id == "session_01ABC"
    assert p.branch == "mcp-fix"
    assert p.blockers == ()
    assert p.label == "oms@mcp-fix"


def test_build_has_no_session_id_before_rename() -> None:
    p = passport.build(_session(tmux_name="rc-oms"), host="", tree=None)
    assert p.session_id == ""
    assert p.branch == ""


def test_as_dict_keeps_the_old_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Старые читатели sessions --json опираются на name/label/tmux_name/cwd/url/uptime_s/attach.
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    d = passport.as_dict(passport.build(_session(), host="", tree=_tree()))
    assert {
        "name",
        "label",
        "tmux_name",
        "cwd",
        "url",
        "uptime_s",
        "attach",
        "state",
        "last_lines",
        "listening",
    } <= set(d)
    assert d["host"] == ""
    assert d["session_id"] == "session_01ABC"
    assert d["cli"] == "claude-rc"
    assert d["state"] == ""
    assert d["last_lines"] == []
    assert d["listening"] == []
    json.dumps(d)  # сериализуемо без кастомного энкодера


def test_as_text_lists_every_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    text = passport.as_text(passport.build(_session(), host="m1", tree=_tree()))
    assert "oms@mcp-fix" in text
    assert "https://claude.ai/code/session_01ABC" in text
    assert "ssh m1 -t 'tmux attach -d -t =session_01ABC'" in text
    assert "claude-rc --host m1" in text


def test_as_html_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    session = RemoteSession(
        name="a<b", tmux_name="rc-a", cwd="/r/<x>", url="", created_at=0, label="a<b"
    )
    html_text = passport.as_html(passport.build(session, host="", tree=None))
    assert "<x>" not in html_text and "&lt;x&gt;" in html_text
    assert "ссылка неизвестна" in html_text


def test_as_html_caps_the_pane_tail() -> None:
    # capture-pane -J склеивает перенесённые строки в длинные — без отреза
    # одна болтливая сессия сама по себе перевалит карточку за лимит Telegram.
    base = passport.build(_session(), host="", tree=None)
    html_text = passport.as_html(dataclasses.replace(base, last_lines=("x" * 5000,)))
    assert len(html_text) < 3000
    # Многоточие впереди: срезано начало, а новость в панели — последние строки.
    assert "<pre>…x" in html_text
    assert html_text.rstrip().endswith("</pre>")


def test_as_html_keeps_a_short_tail_intact() -> None:
    base = passport.build(_session(), host="", tree=None)
    html_text = passport.as_html(dataclasses.replace(base, last_lines=("short tail",)))
    assert "<pre>short tail</pre>" in html_text
    assert "…" not in html_text


async def test_collect_inspects_each_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return _tree() if str(path) == "/repos/oms" else None

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    found = await passport.collect([_session()], host="m1", probe=False)
    assert [p.branch for p in found] == ["mcp-fix"]
    assert found[0].host == "m1"


async def test_collect_probes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return None

    async def fake_probe(tmux_name: str, *, lines: int = 5) -> state_probe.SessionState:
        return state_probe.SessionState(
            state=state_probe.State.NEEDS_INPUT, last_lines=("❯ 1. Yes",), listening=(3000,)
        )

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    monkeypatch.setattr(passport.state_probe, "probe", fake_probe)
    (p,) = await passport.collect([_session()], host="")
    assert p.state == "needs_input"
    assert p.listening == (3000,)
    d = passport.as_dict(p)
    assert d["state"] == "needs_input" and d["listening"] == [3000]
    assert d["last_lines"] == ["❯ 1. Yes"]
    assert "ждёт ответа" in passport.as_text(p) and "3000" in passport.as_text(p)


async def test_collect_can_skip_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return None

    async def boom(tmux_name: str, *, lines: int = 5) -> state_probe.SessionState:
        raise AssertionError("probe must not be called")

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    monkeypatch.setattr(passport.state_probe, "probe", boom)
    (p,) = await passport.collect([_session()], host="", probe=False)
    assert p.state == ""


def test_state_line_words() -> None:
    base = passport.build(_session(), host="", tree=None)
    for state, word in [
        ("idle", "свободна"),
        ("working", "работает"),
        ("needs_input", "ждёт ответа"),
        ("unknown", "состояние неясно"),
    ]:
        assert word in passport.state_line(dataclasses.replace(base, state=state))
    assert passport.state_line(base) == ""


def test_pre_block_cuts_before_escaping() -> None:
    # Отрез после экранирования разрубает «&lt;» пополам, отрез готовой строки
    # уносит закрывающий тег — в обоих случаях Telegram отвечает 400.
    raw = ("x" * 9 + "<") * 500
    out = passport.pre_block(raw, 100)

    assert out.startswith("<pre>") and out.endswith("</pre>")
    inner = out.removeprefix("<pre>").removesuffix("</pre>")
    assert "<" not in inner
    assert "&lt" in inner and "&l;" not in inner
    assert html.unescape(inner).endswith(raw[-10:])


def test_pre_block_bounds_the_escaped_length() -> None:
    # `html.escape` раздувает кавычки и скобки впятеро: текст, влезавший в
    # лимит сырым, за него выходил, и карточку резали уже снаружи — ровно тем
    # срезом, от которого pre_block и заведён.
    raw = '"<' * 750
    out = passport.pre_block(raw, 1500)

    assert len(out) <= 1500 + len("<pre></pre>") + 1
    assert out.endswith("</pre>")
    inner = out.removeprefix("<pre>").removesuffix("</pre>")
    assert "<" not in inner
    assert inner.startswith("…")


def test_pre_block_keeps_short_text_whole() -> None:
    assert passport.pre_block("short tail", 100) == "<pre>short tail</pre>"


def test_as_html_stays_under_the_telegram_limit_on_a_quoted_tail() -> None:
    # Карточка уходит без внешнего отреза (`show_chats`), значит её длина
    # должна быть ограничена по построению, а не срезом на месте отправки.
    base = passport.build(_session(), host="m1", tree=None)
    html_text = passport.as_html(dataclasses.replace(base, last_lines=('"<' * 2000,)))

    assert len(html_text) < 3800
    assert html_text.endswith("</pre>")
