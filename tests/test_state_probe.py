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
