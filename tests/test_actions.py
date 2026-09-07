import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from clauderc import actions, remote
from clauderc.remote import RemoteSession

Handler = Callable[..., tuple[int, str]]


def _stub(handler: Handler) -> Callable[..., Awaitable[tuple[int, str]]]:
    async def run(*args: str, check: bool = True) -> tuple[int, str]:
        return handler(*args)

    return run


def _session(tmux_name: str = "session_01ABC", label: str = "oms@old") -> RemoteSession:
    return RemoteSession(
        name=label,
        tmux_name=tmux_name,
        cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC",
        created_at=0,
        label=label,
    )


async def test_send_types_literally_then_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))

    await actions.send(_session(), "/mcp")

    # -l: иначе tmux принял бы «Enter» или «C-c» в тексте за имя клавиши.
    # Target — `=имя:`, здесь ждут target-pane.
    assert calls == [
        ("send-keys", "-t", "=session_01ABC:", "-l", "/mcp"),
        ("send-keys", "-t", "=session_01ABC:", "Enter"),
    ]


async def test_send_without_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    await actions.send(_session(), "1", enter=False)
    assert calls == [("send-keys", "-t", "=session_01ABC:", "-l", "1")]


async def test_send_raises_when_enter_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        if a[-1] == "Enter":
            return 1, "can't find session"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    with pytest.raises(actions.ActionError, match="can't find session"):
        await actions.send(_session(), "/mcp")


async def test_tail_returns_last_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    pane = "\n".join(f"line{i}" for i in range(10)) + "\n\n\n"
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, pane)))
    assert await actions.tail(_session(), lines=3) == "line7\nline8\nline9"


async def test_rename_sets_label_and_reports_app_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        if a[0] == "capture-pane":
            return 0, "❯ \n"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    result = await actions.rename(_session(), "new", settle_s=0)

    assert result.label == "oms@new"
    assert result.app_renamed is True
    assert ("set-option", "-t", "=session_01ABC:", "@rc_label", "oms@new") in calls
    assert ("send-keys", "-t", "=session_01ABC:", "-l", "/rename new") in calls


async def test_rename_notices_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(*a: str) -> tuple[int, str]:
        if a[0] == "capture-pane":
            return 0, "Unknown slash command: /rename\n❯ \n"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    result = await actions.rename(_session(), "new", settle_s=0)
    assert result.app_renamed is False


async def test_rename_keeps_repo_from_old_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, "")))
    result = await actions.rename(_session(label="city-manager@wt/2026"), "fix", settle_s=0)
    assert result.label == "city-manager@fix"


async def test_rename_falls_back_to_directory_when_no_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, "")))
    result = await actions.rename(_session(label=""), "fix", settle_s=0)
    assert result.label == "oms@fix"


async def test_rename_raises_when_tmux_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (1, "no such session")))
    with pytest.raises(actions.ActionError, match="no such session"):
        await actions.rename(_session(), "new", settle_s=0)


async def test_send_and_tail_waits_then_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a[0])
        return (0, "a\nb\nc\n") if a[0] == "capture-pane" else (0, "")

    monkeypatch.setattr(remote, "_run", _stub(handler))
    out = await actions.send_and_tail(_session(), "/mcp", wait_s=0, lines=2)
    assert out == "b\nc"
    assert calls == ["send-keys", "send-keys", "capture-pane"]


async def test_restart_kills_then_relaunches_with_resume_and_old_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    killed: list[tuple[str, str]] = []

    async def kill(tmux_name: str, cwd: str) -> bool:
        order.append("kill")
        killed.append((tmux_name, cwd))
        return True

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        order.append("launch")
        assert label == "oms@old"
        assert kw["resume"] == "session_01ABC"
        assert kw["permission_mode"] == "plan"
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    session = dataclasses.replace(_session(), mode="plan")
    await actions.restart(session, kill=kill)
    assert order == ["kill", "launch"]
    assert killed == [("session_01ABC", "/repos/oms")]


async def test_restart_overrides_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return True

    seen: dict[str, Any] = {}

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        seen.update(kw)
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    await actions.restart(_session(), kill=kill, mode="bypassPermissions")
    assert seen["permission_mode"] == "bypassPermissions"


async def test_restart_without_session_id_launches_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return True

    seen: dict[str, Any] = {}

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        seen.update(kw)
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    await actions.restart(_session(tmux_name="rc-oms"), kill=kill)
    assert seen["resume"] is None


async def test_restart_refuses_when_kill_fails() -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return False

    with pytest.raises(actions.ActionError, match="не погас"):
        await actions.restart(_session(), kill=kill)


async def test_rename_cleans_whitespace_in_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Двухстрочный ответ в Telegram — обычное дело, а таб в @rc_label разорвал
    # бы строку `list-sessions` и сессия пропала бы из всех списков.
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    result = await actions.rename(_session(), "a\tb\nc", settle_s=0)

    assert result.label == "oms@a b c"
    assert ("set-option", "-t", "=session_01ABC:", "@rc_label", "oms@a b c") in calls
    assert ("send-keys", "-t", "=session_01ABC:", "-l", "/rename a b c") in calls


async def test_rename_refuses_a_blank_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, "")))
    with pytest.raises(actions.ActionError, match="пустое имя"):
        await actions.rename(_session(), "   ", settle_s=0)
