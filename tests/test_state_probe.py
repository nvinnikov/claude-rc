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


def test_prose_about_wanting_something_is_not_a_dialog() -> None:
    """«Do you want to» посреди ответа claude — проза, а не вопрос.

    Диалог печатает вопрос своей строкой, поэтому шаблон привязан к её началу.
    Без привязки свободная сессия показывалась бы как ждущая ответа — и кнопки,
    печатающие в панель, отказывали бы на пустом месте.
    """
    rows = _pane("idle").rstrip("\n").split("\n")
    # Строка внутри окна классификации (последние 12), иначе проверять нечего.
    rows[-10] = "  \u23bf  Found 3 more files. Do you want to see them?"
    pane = "\n".join(rows)
    assert "Do you want to" in "\n".join(rows[-state_probe._TAIL_FOR_CLASSIFY :])
    assert state_probe.classify(pane) is State.IDLE


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


async def test_listening_ports_survives_lsof_exit_1_with_dead_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # lsof выходит с кодом 1, если один из перечисленных pid уже умер (гонка с pgrep),
    # но живые сокеты в stdout всё равно печатает — их не теряем.
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 0, "100\n"

    async def exec_(argv: list[str], timeout_s: float = 5.0) -> tuple[int, str]:
        if argv[0] == "pgrep":
            return 1, ""
        assert argv[0] == "lsof"
        return 1, "n*:3000\n"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "_exec", exec_)
    assert await state_probe.listening_ports("session_X") == (3000,)


def test_numbered_list_without_a_caret_is_not_a_dialog() -> None:
    # Ответ claude с нумерованным списком — не диалог: подсвеченный пункт
    # диалог рисует с кареткой, обычный текст — нет.
    assert state_probe.classify(_pane("idle") + "1. первый пункт\n") is State.IDLE


def test_numbered_list_does_not_interrupt_working() -> None:
    assert state_probe.classify(_pane("working") + "1) шаг\n") is State.WORKING


async def test_classify_pane_reads_only_the_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    # Один capture-pane и разбор: ни list-panes, ни pgrep, ни lsof.
    seen: list[tuple[str, ...]] = []

    async def run(*argv: str, check: bool = True) -> tuple[int, str]:
        seen.append(argv)
        return 0, _pane("needs_input")

    async def boom(tmux_name: str) -> tuple[int, ...]:
        raise AssertionError("порты тут не спрашивают")

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "listening_ports", boom)
    assert await state_probe.classify_pane("session_X") is State.NEEDS_INPUT
    assert seen == [("capture-pane", "-p", "-J", "-t", "=session_X:")]


async def test_classify_pane_reports_dead_when_tmux_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*argv: str, check: bool = True) -> tuple[int, str]:
        return 1, "can't find session"

    monkeypatch.setattr(remote, "_run", run)
    assert await state_probe.classify_pane("session_X") is State.DEAD
