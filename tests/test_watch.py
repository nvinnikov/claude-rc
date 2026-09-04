import asyncio

import pytest
from clauderc import watch
from clauderc.remote import RemoteSession
from clauderc.watch import Died, Watcher


def _session(name: str, cwd: str = "/repos/x") -> RemoteSession:
    return RemoteSession(name=name, tmux_name=f"rc-{name}", cwd=cwd, url="https://x", created_at=0)


def _sessions(monkeypatch: pytest.MonkeyPatch, *batches: list[RemoteSession]) -> None:
    """Подменяет list_sessions последовательностью снимков, по одному на вызов."""
    queue = list(batches)

    async def fake() -> list[RemoteSession]:
        return queue.pop(0) if queue else []

    monkeypatch.setattr(watch, "list_sessions", fake)


async def _collect(watcher: Watcher, times: int) -> list[Died]:
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    for _ in range(times):
        await watcher.poll(on_died)
    return seen


async def test_first_snapshot_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Перезапуск бота при живых сессиях не должен отчитываться о падениях.
    _sessions(monkeypatch, [_session("a")])
    assert await _collect(Watcher(), 1) == []


async def test_disappeared_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _sessions(monkeypatch, [_session("a", "/repos/a")], [])
    (died,) = await _collect(Watcher(), 2)
    assert died == Died(name="a", tmux_name="rc-a", cwd="/repos/a")


async def test_surviving_session_is_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _sessions(monkeypatch, [_session("a")], [_session("a")])
    assert await _collect(Watcher(), 2) == []


async def test_expected_death_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(monkeypatch, [_session("a")], [_session("a")], [])

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)  # базовый снимок
    assert await watcher.kill("rc-a") is True
    await watcher.poll(on_died)  # ещё жива
    await watcher.poll(on_died)  # исчезла — но её гасили мы
    assert seen == []
    assert killed == ["rc-a"]


async def test_failed_kill_does_not_swallow_real_death(monkeypatch: pytest.MonkeyPatch) -> None:
    # kill_tmux вернул False — сессия жива, метка не должна пережить её и проглотить
    # настоящее падение позже.
    async def fake_kill(tmux_name: str) -> bool:
        return False

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(monkeypatch, [_session("a", "/repos/a")], [_session("a", "/repos/a")], [])

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)  # базовый снимок
    assert await watcher.kill("rc-a") is False
    await watcher.poll(on_died)  # гашение не удалось, сессия ещё жива
    await watcher.poll(on_died)  # исчезла по-настоящему
    assert seen == [Died(name="a", tmux_name="rc-a", cwd="/repos/a")]


async def test_mark_does_not_leak_to_next_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # Одноразовость метки: сессия с тем же именем, упавшая позже, должна попасть в отчёт.
    async def fake_kill(tmux_name: str) -> bool:
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(
        monkeypatch,
        [_session("a")],  # базовый
        [],  # погашена нами
        [_session("a")],  # поднялась заново
        [],  # упала сама
    )

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)
    await watcher.kill("rc-a")
    await watcher.poll(on_died)
    await watcher.poll(on_died)
    await watcher.poll(on_died)
    assert [d.tmux_name for d in seen] == ["rc-a"]


async def test_kill_all_marks_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_kill(tmux_name: str) -> bool:
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(
        monkeypatch,
        [_session("a"), _session("b")],  # базовый
        [_session("a"), _session("b")],  # снимок внутри kill_all
        [],
    )

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)
    assert await watcher.kill_all() == 2
    await watcher.poll(on_died)
    assert seen == []


async def test_kill_all_reports_session_that_did_not_die(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # kill_all делит работу на kill() по одной сессии — неудача одной не должна
    # погасить метку на другой и не должна помешать репорту о настоящем падении.
    async def fake_kill(tmux_name: str) -> bool:
        return tmux_name != "rc-b"

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(
        monkeypatch,
        [_session("a"), _session("b")],  # базовый
        [_session("a"), _session("b")],  # снимок внутри kill_all
        [],  # b не погасилась, но исчезла сама следующим опросом
    )

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)
    assert await watcher.kill_all() == 1
    await watcher.poll(on_died)
    assert [d.tmux_name for d in seen] == ["rc-b"]


async def test_poll_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # poll честно пробрасывает: глотать — обязанность run, и она проверяется отдельно.
    async def broken() -> list[RemoteSession]:
        raise RuntimeError("tmux ушёл")

    monkeypatch.setattr(watch, "list_sessions", broken)

    async def on_died(died: Died) -> None:
        raise AssertionError("не должно вызываться")

    with pytest.raises(RuntimeError):
        await Watcher().poll(on_died)


async def test_run_survives_a_failed_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # Watcher не имеет права уронить бота: упавший опрос переживается, цикл идёт дальше.
    calls = {"n": 0}

    async def flaky() -> list[RemoteSession]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tmux ушёл")
        return []

    monkeypatch.setattr(watch, "list_sessions", flaky)

    async def on_died(died: Died) -> None:
        raise AssertionError("не должно вызываться")

    task = asyncio.create_task(Watcher(poll_s=0).run(on_died))
    for _ in range(200):  # граница вместо while: упавшая задача не должна вешать тест
        if calls["n"] >= 3:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 3


async def test_rename_is_not_reported_as_death(monkeypatch: pytest.MonkeyPatch) -> None:
    """`await_url` переименовывает сессию в id — имя пропадает, сессия остаётся.

    Watcher замечает исчезновение по имени, поэтому без проверки каталога
    переименование доехало бы до человека карточкой «сессия завершилась».
    """
    renamed = RemoteSession(
        name="oms", tmux_name="session_01ABC", cwd="/repos/oms", url="https://x", created_at=0
    )
    _sessions(monkeypatch, [_session("oms", "/repos/oms")], [renamed])
    assert await _collect(Watcher(), 2) == []


async def test_death_after_rename_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # Проверка каталога не должна проглатывать настоящую смерть следом за ней.
    renamed = RemoteSession(
        name="oms", tmux_name="session_01ABC", cwd="/repos/oms", url="https://x", created_at=0
    )
    _sessions(monkeypatch, [_session("oms", "/repos/oms")], [renamed], [])
    (died,) = await _collect(Watcher(), 3)
    assert died == Died(name="oms", tmux_name="session_01ABC", cwd="/repos/oms")


async def test_relaunch_in_the_same_directory_still_reports_the_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Упала и тут же поднялась заново — это смерть, а не переименование.

    Одного совпадения каталога мало: перезапуск внутри одного опроса проглотил
    бы настоящее падение. `session_created` переименование сохраняет, а
    перезапуск — нет, поэтому сверяем и его.
    """
    old = RemoteSession(
        name="oms", tmux_name="session_01A", cwd="/repos/oms", url="https://x", created_at=1000
    )
    fresh = RemoteSession(name="oms", tmux_name="rc-oms", cwd="/repos/oms", url="", created_at=2000)
    _sessions(monkeypatch, [old], [fresh])
    (died,) = await _collect(Watcher(), 2)
    assert died.tmux_name == "session_01A"
