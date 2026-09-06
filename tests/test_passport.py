import json
from pathlib import Path

import pytest
from clauderc import passport, remote
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
    assert {"name", "label", "tmux_name", "cwd", "url", "uptime_s", "attach"} <= set(d)
    assert d["host"] == ""
    assert d["session_id"] == "session_01ABC"
    assert d["cli"] == "claude-rc"
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


async def test_collect_inspects_each_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return _tree() if str(path) == "/repos/oms" else None

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    found = await passport.collect([_session()], host="m1")
    assert [p.branch for p in found] == ["mcp-fix"]
    assert found[0].host == "m1"
