from pathlib import Path

import pytest
from clauderc import remote, state_probe
from clauderc.state_probe import State

FIXTURES = Path(__file__).parent / "fixtures" / "panes"


def _pane(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("idle", State.IDLE),
        ("working", State.WORKING),
        ("needs_input", State.NEEDS_INPUT),
        ("trust", State.NEEDS_INPUT),
        ("garbage", State.UNKNOWN),
    ],
)
def test_classify_real_panes(fixture: str, expected: State) -> None:
    assert state_probe.classify(_pane(fixture)) is expected


def test_dialog_wins_over_prompt() -> None:
    # Диалог рисуется поверх рамки ввода — пустой ❯ ниже не делает сессию idle.
    pane = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n\n❯ \n"
    assert state_probe.classify(pane) is State.NEEDS_INPUT


async def test_probe_reports_dead_when_tmux_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 1, "can't find session"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "listening_ports", _no_ports)
    result = await state_probe.probe("session_X")
    assert result.state is State.DEAD
    assert result.last_lines == ()


async def test_probe_keeps_last_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 0, _pane("idle")

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "listening_ports", _no_ports)
    result = await state_probe.probe("session_X", lines=3)
    assert result.state is State.IDLE
    assert len(result.last_lines) == 3


async def _no_ports(tmux_name: str) -> tuple[int, ...]:
    return ()


def test_parse_lsof_extracts_ports() -> None:
    out = "p123\nn*:3000\np456\nn127.0.0.1:5173\nn[::1]:5173\n"
    assert state_probe._parse_lsof(out) == (3000, 5173)


async def test_listening_ports_walks_children(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        assert a == ("list-panes", "-t", "=session_X:", "-F", "#{pane_pid}")
        return 0, "100\n"

    calls: list[list[str]] = []

    async def exec_(argv: list[str], timeout_s: float = 5.0) -> tuple[int, str]:
        calls.append(argv)
        if argv[0] == "pgrep":
            parent = argv[-1]
            return (0, "101\n102\n") if parent == "100" else (1, "")
        assert argv[0] == "lsof"
        assert "100,101,102" in argv
        return 0, "n*:3000\n"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "_exec", exec_)
    assert await state_probe.listening_ports("session_X") == (3000,)


async def test_listening_ports_empty_when_lsof_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 0, "100\n"

    async def exec_(argv: list[str], timeout_s: float = 5.0) -> tuple[int, str]:
        return 127, "not found"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "_exec", exec_)
    assert await state_probe.listening_ports("session_X") == ()
