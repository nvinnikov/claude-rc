import uuid
from pathlib import Path

import pytest

from tgclaude import remote
from tgclaude.remote import LaunchError, session_name

_ROW = "rc-oms\t/repos/oms\t1700000000\thttps://claude.ai/code/session_A"


def _stub(handler):
    """Подменяет remote._run переданным обработчиком (cmd, *args) -> (code, text)."""

    async def run(*args, check=True):
        return handler(*args)

    return run


def test_session_name_prefixes_and_sanitizes():
    assert session_name("flybo-arch-v2") == "rc-flybo-arch-v2"
    # tmux не принимает `.` и `:` в имени сессии
    assert session_name("my.repo:1") == "rc-my-repo-1"


def test_session_name_survives_garbage_input():
    assert session_name("...") == "rc-session"


async def test_list_sessions_parses_rows_and_ignores_foreign(monkeypatch):
    out = "\n".join(
        [
            _ROW,
            "work\t/elsewhere\t1700000001\t",  # чужая tmux-сессия
            "rc-geo\t/repos/geo\t1700000002\thttps://claude.ai/code/session_B",
        ]
    )
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, out)))

    sessions = await remote.list_sessions()

    assert [s.name for s in sessions] == ["geo", "oms"]
    assert sessions[0].cwd == "/repos/geo"
    assert sessions[1].url.endswith("session_A")
    assert sessions[1].tmux_name == "rc-oms"


async def test_list_sessions_empty_when_server_down(monkeypatch):
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (1, "no server running")))

    assert await remote.list_sessions() == []


async def test_launch_returns_existing_instead_of_second_session(monkeypatch):
    def handler(*args):
        assert args[0] != "new-session", "вторую сессию поднимать нельзя"
        return (0, _ROW) if args[0] == "list-sessions" else (0, "")

    monkeypatch.setattr(remote, "_run", _stub(handler))

    session = await remote.launch("oms", "/repos/oms")

    assert session.url.endswith("session_A")


async def test_launch_stores_url_in_tmux_option(monkeypatch):
    created = False
    options: list[tuple] = []

    def handler(*args):
        nonlocal created
        if args[0] == "list-sessions":
            return (0, _ROW) if created else (0, "")
        if args[0] == "new-session":
            created = True
            return 0, ""
        if args[0] == "capture-pane":
            return 0, "шум\nhttps://claude.ai/code/session_A\nещё шум"
        if args[0] == "set-option":
            options.append(args)
            return 0, ""
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    session = await remote.launch("oms", "/repos/oms")

    assert session.url.endswith("session_A")
    # Ссылку кладём в user-опцию: TUI перерисует панель и вытрет её из буфера
    assert any("@rc_url" in args for args in options)


async def test_launch_timeout_kills_session_and_shows_tail(monkeypatch):
    calls: list[str] = []

    def handler(*args):
        calls.append(args[0])
        if args[0] == "capture-pane":
            return 0, "claude: command not found"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(LaunchError) as exc:
        await remote.launch("oms", "/repos/oms", timeout_s=0.05)

    assert "command not found" in str(exc.value)
    assert "kill-session" in calls


async def test_launch_reports_dead_session(monkeypatch):
    def handler(*args):
        if args[0] == "capture-pane":
            return 1, "can't find pane"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(LaunchError, match="не отдав ссылку"):
        await remote.launch("oms", "/repos/oms", timeout_s=1.0)


@pytest.mark.skipif(not remote.tmux_available(), reason="нет tmux")
async def test_launch_against_real_tmux(tmp_path: Path, monkeypatch):
    """Сквозная проверка: настоящий tmux, вместо claude — заглушка с tty."""
    url = "https://claude.ai/code/session_" + uuid.uuid4().hex[:16]
    stub = tmp_path / "fake-claude"
    stub.write_text(f'#!/bin/sh\necho "remote control active at"\necho "{url}"\nsleep 30\n')
    stub.chmod(0o755)
    monkeypatch.setattr(remote, "CLAUDE_BIN", str(stub))
    repo = f"pytest-{uuid.uuid4().hex[:8]}"

    try:
        session = await remote.launch(repo, str(tmp_path), timeout_s=20)

        assert session.url == url
        assert session.tmux_name == f"rc-{repo}"
        # tmux отдаёт разрешённый путь: на macOS /var — симлинк на /private/var
        assert Path(session.cwd).resolve() == tmp_path.resolve()
        assert repo in {s.name for s in await remote.list_sessions()}

        assert await remote.kill_session(repo) is True
        assert repo not in {s.name for s in await remote.list_sessions()}
    finally:
        await remote._run("kill-session", "-t", f"=rc-{repo}", check=False)
