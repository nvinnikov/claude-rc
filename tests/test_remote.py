import subprocess
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path

import pytest
from clauderc import remote
from clauderc.remote import LaunchError, RemoteSession, attach_command, session_name

# Обработчик подменённого tmux: (команда, аргументы…) -> (код возврата, вывод)
Handler = Callable[..., tuple[int, str]]

_ROW = "rc-oms\t/repos/oms\t1700000000\thttps://claude.ai/code/session_A"


def _stub(handler: Handler) -> Callable[..., Awaitable[tuple[int, str]]]:
    """Подменяет remote._run переданным обработчиком (cmd, *args) -> (code, text)."""

    async def run(*args: str, check: bool = True) -> tuple[int, str]:
        return handler(*args)

    return run


def test_session_name_prefixes_and_sanitizes() -> None:
    assert session_name("my-services-v2") == "rc-my-services-v2"
    # tmux не принимает `.` и `:` в имени сессии
    assert session_name("my.repo:1") == "rc-my-repo-1"


def test_session_name_survives_garbage_input() -> None:
    assert session_name("...") == "rc-session"


def test_attach_command_uses_default_server_when_no_socket_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    assert attach_command("rc-oms") == "tmux attach -d -t =rc-oms"


def test_attach_command_targets_isolated_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    # Без -L подсадка пришла бы на сервер по умолчанию, где сессии из
    # изолированной песочницы (CLAUDE_RC_TMUX_SOCKET) попросту нет.
    monkeypatch.setenv(remote.TMUX_SOCKET_ENV, "claude-rc-pytest")
    assert attach_command("rc-oms") == "tmux -L claude-rc-pytest attach -d -t =rc-oms"


def test_attach_argv_detaches_others_and_matches_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    # `-d`: второй клиент с узким окном ужал бы панель тому, кто работает
    # прямо сейчас. `=`: без него `rc-oms` рискует поймать `rc-oms-2`.
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    assert remote.attach_argv("rc-oms") == ["tmux", "attach", "-d", "-t", "=rc-oms"]


async def test_list_sessions_parses_rows_and_ignores_foreign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


async def test_list_sessions_empty_when_server_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (1, "no server running")))

    assert await remote.list_sessions() == []


async def test_launch_returns_existing_instead_of_second_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(*args: str) -> tuple[int, str]:
        assert args[0] != "new-session", "вторую сессию поднимать нельзя"
        return (0, _ROW) if args[0] == "list-sessions" else (0, "")

    monkeypatch.setattr(remote, "_run", _stub(handler))

    session = await remote.launch("oms", "/repos/oms")

    assert session.url.endswith("session_A")


async def test_launch_stores_url_in_tmux_option(monkeypatch: pytest.MonkeyPatch) -> None:
    created = False
    options: list[tuple[str, ...]] = []

    def handler(*args: str) -> tuple[int, str]:
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


async def test_launch_timeout_kills_session_and_shows_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(*args: str) -> tuple[int, str]:
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


async def test_launch_reports_dead_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "capture-pane":
            return 1, "can't find pane"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(LaunchError, match="не отдав ссылку"):
        await remote.launch("oms", "/repos/oms", timeout_s=1.0)


async def test_await_url_fills_tmux_name_when_session_ended_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Сессия уже мертва в этой ветке (capture-pane её не нашёл), watcher её видел —
    # tmux_name должен доехать до вызывающего, иначе о смерти доложат дважды.
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (1, "can't find pane")))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(LaunchError) as exc:
        await remote.await_url("rc-oms", "/repos/oms", timeout_s=1.0)

    assert exc.value.tmux_name == "rc-oms"


def _default_server_sessions() -> str:
    """Список сессий на tmux-сервере по умолчанию — том, где живут рабочие rc-*."""
    result = subprocess.run(["tmux", "list-sessions"], capture_output=True, text=True, check=False)
    return result.stdout


@pytest.mark.skipif(not remote.tmux_available(), reason="нет tmux")
async def test_launch_against_real_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сквозная проверка: настоящий tmux на отдельном сокете, claude — заглушка с tty.

    Отдельный сокет (`CLAUDE_RC_TMUX_SOCKET`) — не для скорости, а для изоляции:
    сессия теста не должна попасться на глаза боту, который следит за сервером
    по умолчанию и честно репортит появление/исчезновение rc-* как чужую сессию.
    """
    socket_name = f"claude-rc-pytest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(remote.TMUX_SOCKET_ENV, socket_name)

    url = "https://claude.ai/code/session_" + uuid.uuid4().hex[:16]
    stub = tmp_path / "fake-claude"
    stub.write_text(f'#!/bin/sh\necho "remote control active at"\necho "{url}"\nsleep 30\n')
    stub.chmod(0o755)
    monkeypatch.setattr(remote, "CLAUDE_BIN", str(stub))
    repo = f"pytest-{uuid.uuid4().hex[:8]}"

    before = _default_server_sessions()
    try:
        session = await remote.launch(repo, str(tmp_path), timeout_s=20)

        assert session.url == url
        # Сессия переименована в id из ссылки — один идентификатор и для
        # приложения, и для `tmux attach`. Настоящий tmux принял его как имя.
        assert session.tmux_name == url.rsplit("/", 1)[-1]
        assert session.name == tmp_path.name
        # tmux отдаёт разрешённый путь: на macOS /var — симлинк на /private/var
        assert Path(session.cwd).resolve() == tmp_path.resolve()
        assert session.tmux_name in {s.tmux_name for s in await remote.list_sessions()}

        # Главное доказательство изоляции: на сервере по умолчанию сессии нет —
        # именно её отсутствие/появление там видит watcher бота.
        assert session.tmux_name not in _default_server_sessions()
        assert _default_server_sessions() == before

        assert await remote.kill_tmux(session.tmux_name) is True
        assert not await remote.list_sessions()
    finally:
        await remote._run("kill-session", "-t", f"=rc-{repo}", check=False)
        await remote._run("kill-session", "-t", f"={url.rsplit('/', 1)[-1]}", check=False)
        # Гасим и сам изолированный сервер, иначе процесс tmux -L останется висеть.
        await remote._run("kill-server", check=False)

    assert _default_server_sessions() == before


async def test_find_matches_by_directory_not_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Два клона с одинаковым basename — сессия ищется по каталогу."""
    rows = "\n".join(
        [
            "rc-claude-rules\t/a/claude-rules\t1700000000\thttps://claude.ai/code/session_A",
            "rc-claude-rules-9f1c2d\t/b/claude-rules\t1700000001\thttps://claude.ai/code/session_B",
        ]
    )
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, rows)))

    first = await remote.find("/a/claude-rules")
    second = await remote.find("/b/claude-rules")
    assert first is not None and first.url.endswith("session_A")
    assert second is not None and second.url.endswith("session_B")
    assert await remote.find("/c/claude-rules") is None


async def test_launch_does_not_reuse_session_from_another_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Раньше второй клон молча получал ссылку на сессию первого."""
    created: list[tuple[str, ...]] = []

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "list-sessions":
            return 0, _ROW  # живая сессия rc-oms, но в /repos/oms
        if args[0] == "new-session":
            created.append(args)
            return 0, ""
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(LaunchError):  # ссылку так и не дождались, но сессию завели
        await remote.launch("oms", "/other/oms", timeout_s=0.05)

    assert created, "для другого каталога нужна новая сессия"
    # имя занято сессией из /repos/oms → различаем родительским каталогом
    name = created[0][created[0].index("-s") + 1]
    assert name == "rc-other-oms"


async def test_await_url_raises_trust_required_and_keeps_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Диалог доверия — не ошибка: сессия жива и ждёт ответа человека."""
    calls: list[str] = []

    def handler(*args: str) -> tuple[int, str]:
        calls.append(args[0])
        if args[0] == "capture-pane":
            return 0, "Quick safety check\n ❯ 1. Yes, I trust this folder\n   2. No, exit"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    with pytest.raises(remote.TrustRequired) as exc:
        await remote.await_url("rc-oms", "/repos/oms", timeout_s=5.0)

    assert exc.value.tmux_name == "rc-oms"
    assert exc.value.cwd == "/repos/oms"
    assert "kill-session" not in calls, "сессию гасить нельзя — она держит вопрос"


async def test_await_url_ignores_trust_prompt_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После Enter диалог ещё мгновение на экране — принимать его за новый нельзя."""
    pane = "Quick safety check\n ❯ 1. Yes, I trust this folder\nhttps://claude.ai/code/session_A\n"
    monkeypatch.setattr(
        remote, "_run", _stub(lambda *a: (0, pane if a[0] == "capture-pane" else _ROW))
    )
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    session = await remote.await_url("rc-oms", "/repos/oms", timeout_s=5.0, watch_trust=False)

    assert session.url.endswith("session_A")


_FRESH = "rc-repo\t/repos/repo\t1700000000\thttps://claude.ai/code/session_A"


def _capture_new_session(commands: list[str]) -> Handler:
    """Заглушка tmux: до new-session сессий нет, после — есть.

    Порядок важен: launch первым делом зовёт find(cwd), и заглушка, отдающая
    готовую сессию сразу, вернула бы её вместо запуска — new-session не случился бы.
    """

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "new-session":
            commands.append(args[-1])
            return 0, ""
        if args[0] == "capture-pane":
            return 0, "https://claude.ai/code/session_A"
        if args[0] == "list-sessions":
            return (0, _FRESH) if commands else (0, "")
        return 0, ""

    return handler


def _fast(monkeypatch: pytest.MonkeyPatch, commands: list[str]) -> None:
    """Заглушка tmux плюс нулевая пауза опроса: четыре теста иначе спят три секунды."""
    monkeypatch.setattr(remote, "_run", _stub(_capture_new_session(commands)))
    monkeypatch.setattr(remote, "tmux_available", lambda: True)
    monkeypatch.setattr(remote, "_POLL_S", 0)


async def test_launch_without_resume_has_no_extra_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5)

    assert "--resume" not in commands[0]
    assert "--continue" not in commands[0]


async def test_launch_with_last_uses_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="last")

    assert commands[0].endswith("--continue")


async def test_launch_with_id_uses_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="abc-123")

    assert commands[0].endswith("--resume abc-123")


async def test_launch_quotes_resume_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # id приходит из имени файла на диске — в командную строку без кавычек нельзя.
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="a b; rm -rf /")

    assert "'a b; rm -rf /'" in commands[0]


async def test_launch_with_resume_returns_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Инвариант «один каталог — одна сессия» резюм не отменяет.
    started: list[str] = []

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "new-session":
            started.append(args[-1])
            return 0, ""
        return 0, _ROW

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "tmux_available", lambda: True)

    session = await remote.launch("oms", "/repos/oms", timeout_s=5, resume="last")

    assert session.tmux_name == "rc-oms"
    assert started == []


async def test_unique_name_takes_the_base_when_it_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "list_sessions", _sessions())
    assert await remote._unique_name("oms", "/Users/n/code/oms") == "rc-oms"


async def test_unique_name_disambiguates_by_parent_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Хеш пути уникальность давал, но человеку не говорил ничего: имя сессии
    # он видит в карточке и набирает в /rckill.
    monkeypatch.setattr(remote, "list_sessions", _sessions("rc-oms"))
    assert await remote._unique_name("oms", "/Users/n/forks/oms") == "rc-forks-oms"


async def test_unique_name_adds_levels_until_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "list_sessions", _sessions("rc-oms", "rc-forks-oms"))
    assert await remote._unique_name("oms", "/Users/n/forks/oms") == "rc-n-forks-oms"


async def test_unique_name_falls_back_to_a_hash_when_paths_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Путь совпал целиком — так бывает у симлинка на уже занятый каталог.
    taken = ("rc-oms", "rc-code-oms", "rc-n-code-oms", "rc-Users-n-code-oms")
    monkeypatch.setattr(remote, "list_sessions", _sessions(*taken))
    name = await remote._unique_name("oms", "/Users/n/code/oms")
    assert name.startswith("rc-oms-") and name not in taken


def _sessions(*names: str) -> Callable[[], Coroutine[None, None, list[RemoteSession]]]:
    async def listing() -> list[RemoteSession]:
        return [
            RemoteSession(name=n.removeprefix("rc-"), tmux_name=n, cwd="/x", url="", created_at=0)
            for n in names
        ]

    return listing


async def test_await_url_renames_the_session_to_the_claude_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ссылка появилась — имя сессии становится id из неё."""
    url = "https://claude.ai/code/session_01ABCdef"
    renamed: list[tuple[str, ...]] = []

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "capture-pane":
            return 0, f"remote control active at\n{url}"
        if args[0] == "rename-session":
            renamed.append(args)
            return 0, ""
        if args[0] == "list-sessions":
            return 0, "session_01ABCdef\t/repos/oms\t1000\t" + url
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    session = await remote.await_url("rc-oms", "/repos/oms", timeout_s=1.0)

    assert renamed == [("rename-session", "-t", "=rc-oms", "session_01ABCdef")]
    assert session.tmux_name == "session_01ABCdef"
    assert session.name == "oms"  # в карточке — каталог, id человеку ничего не говорит


async def test_await_url_survives_a_failed_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переименование не удалось — сессия жива под прежним именем, ссылка на месте."""
    url = "https://claude.ai/code/session_01ABCdef"

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "capture-pane":
            return 0, url
        if args[0] == "rename-session":
            return 1, "duplicate session"
        if args[0] == "list-sessions":
            return 0, f"rc-oms\t/repos/oms\t1000\t{url}"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    session = await remote.await_url("rc-oms", "/repos/oms", timeout_s=1.0)
    assert session.tmux_name == "rc-oms"
    assert session.url == url


async def test_list_sessions_keeps_renamed_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """После переименования префикса нет — узнаём своих по `@rc_url`."""
    rows = (
        "session_01ABCdef\t/repos/oms\t1000\thttps://claude.ai/code/session_01ABCdef\n"
        "rc-fresh\t/repos/fresh\t1001\t\n"  # ещё не дождалась ссылки
        "work\t/repos/work\t1002\t\n"  # чужая
    )
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, rows)))

    assert [s.tmux_name for s in await remote.list_sessions()] == ["rc-fresh", "session_01ABCdef"]


async def test_resolve_matches_id_name_and_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        "session_01A\t/repos/oms\t1000\thttps://claude.ai/code/session_01A\n"
        "session_01B\t/forks/oms\t1001\thttps://claude.ai/code/session_01B\n"
    )
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, rows)))

    assert [s.tmux_name for s in await remote.resolve("session_01B")] == ["session_01B"]
    assert [s.tmux_name for s in await remote.resolve("/forks/oms")] == ["session_01B"]
    # Имя каталога не уникально — обе, выбирать за человека нечего.
    assert [s.tmux_name for s in await remote.resolve("oms")] == ["session_01A", "session_01B"]
    assert await remote.resolve("nope") == []


async def test_await_url_keeps_the_name_when_the_url_was_not_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`@rc_url` не записался — переименовывать нельзя.

    Своих `list_sessions` узнаёт по префиксу `rc-` или по `@rc_url`. Сессия без
    обоих признаков исчезает из выдачи насовсем: живой процесс, до которого не
    дотянуться ни `/rckill`, ни watcher'у.
    """
    url = "https://claude.ai/code/session_01ABCdef"
    renamed: list[tuple[str, ...]] = []

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "capture-pane":
            return 0, url
        if args[0] == "set-option":
            return 1, "no such session"
        if args[0] == "rename-session":
            renamed.append(args)
            return 0, ""
        if args[0] == "list-sessions":
            return 0, f"rc-oms\t/repos/oms\t1000\t{url}"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "_POLL_S", 0.0)

    session = await remote.await_url("rc-oms", "/repos/oms", timeout_s=1.0)
    assert renamed == []
    assert session.tmux_name == "rc-oms"


async def test_resolve_expands_a_tilde_in_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/rckill ~/code/oms` — realpath тильду не разворачивает, resolve обязан."""
    home = Path.home()
    rows = f"session_01A\t{home}/code/oms\t1000\thttps://claude.ai/code/session_01A\n"
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, rows)))

    assert [s.tmux_name for s in await remote.resolve("~/code/oms")] == ["session_01A"]
