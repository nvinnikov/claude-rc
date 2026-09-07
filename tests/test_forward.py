import socket
from pathlib import Path

import pytest
from clauderc import forward

# Настоящий `_is_ssh`: автофикстура ниже подменяет модульный атрибут, и тесты
# самой проверки должны звать оригинал, а не заглушку.
_real_is_ssh = forward._is_ssh


@pytest.fixture
def pid_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(forward.PID_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_settle_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start()` спит `_SETTLE_S` перед `poll()` — тестам ждать незачем."""
    monkeypatch.setattr(forward.time, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def _pid_is_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию pid из файла считаем настоящим ssh: `ps` в тестах не зовём."""
    monkeypatch.setattr(forward, "_is_ssh", lambda pid: True)


class _FakeProc:
    def __init__(self, argv: list[str], *, poll_result: int | None = None, **kw: object) -> None:
        self.argv = argv
        self.pid = 4242
        self._poll_result = poll_result

    def poll(self) -> int | None:
        return self._poll_result


def test_start_spawns_ssh_and_writes_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    spawned: list[list[str]] = []

    def fake_popen(argv: list[str], **kw: object) -> _FakeProc:
        spawned.append(argv)
        return _FakeProc(argv)

    monkeypatch.setattr(forward.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(forward, "port_busy", lambda port: False)
    (f,) = forward.start("m1", [3000])
    assert spawned == [["ssh", "-N", "-L", "3000:localhost:3000", "m1"]]
    assert f == forward.Forward(host="m1", port=3000, pid=4242)
    assert (pid_dir / "m1-3000.pid").read_text().strip() == "4242"


def test_start_refuses_busy_port(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    monkeypatch.setattr(forward, "port_busy", lambda port: True)
    with pytest.raises(forward.ForwardError, match="3000"):
        forward.start("m1", [3000])


def test_start_refuses_bad_host(pid_dir: Path) -> None:
    with pytest.raises(forward.ForwardError, match="хоста"):
        forward.start("../x", [3000])


def test_start_rejects_ssh_that_dies_immediately(
    monkeypatch: pytest.MonkeyPatch, pid_dir: Path
) -> None:
    def fake_popen(argv: list[str], **kw: object) -> _FakeProc:
        return _FakeProc(argv, poll_result=255)

    monkeypatch.setattr(forward.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(forward, "port_busy", lambda port: False)
    with pytest.raises(forward.ForwardError, match="255"):
        forward.start("m1", [3000])
    assert not (pid_dir / "m1-3000.pid").exists()


def test_port_busy_detects_a_listener() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert forward.port_busy(port) is True
    assert forward.port_busy(port) is False


def test_stop_kills_and_removes_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("4242")
    killed: list[int] = []
    monkeypatch.setattr(forward.os, "kill", lambda pid, sig: killed.append(pid))
    assert forward.stop("m1") == [forward.Forward(host="m1", port=3000, pid=4242)]
    assert killed == [4242]
    assert not (pid_dir / "m1-3000.pid").exists()


def test_stop_ignores_dead_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("4242")

    def kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(forward.os, "kill", kill)
    assert forward.stop("m1", [3000]) == []
    assert not (pid_dir / "m1-3000.pid").exists()


def test_stop_keeps_pid_file_on_permission_error(
    monkeypatch: pytest.MonkeyPatch, pid_dir: Path
) -> None:
    (pid_dir / "m1-3000.pid").write_text("4242")

    def kill(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(forward.os, "kill", kill)
    with pytest.raises(forward.ForwardError, match="3000"):
        forward.stop("m1", [3000])
    assert (pid_dir / "m1-3000.pid").exists()


def test_stop_refuses_bad_host(pid_dir: Path) -> None:
    with pytest.raises(forward.ForwardError, match="хоста"):
        forward.stop("a/b")


def test_stop_refuses_host_starting_with_dash(pid_dir: Path) -> None:
    with pytest.raises(forward.ForwardError, match="хоста"):
        forward.stop("-evil")


def test_active_lists_pid_files(pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("1")
    (pid_dir / "m3-80.pid").write_text("2")
    assert forward.active() == [
        forward.Forward(host="m1", port=3000, pid=1),
        forward.Forward(host="m3", port=80, pid=2),
    ]


def test_start_accepts_user_at_host(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    # В ~/.ssh/config запись есть не всегда; `@` в имени pid-файла безопасен.
    spawned: list[list[str]] = []

    def fake_popen(argv: list[str], **kw: object) -> _FakeProc:
        spawned.append(argv)
        return _FakeProc(argv)

    monkeypatch.setattr(forward.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(forward, "port_busy", lambda port: False)
    (f,) = forward.start("user@m1", [3000])

    assert spawned == [["ssh", "-N", "-L", "3000:localhost:3000", "user@m1"]]
    assert f.host == "user@m1"
    assert (pid_dir / "user@m1-3000.pid").is_file()
    # Обратный разбор pid-файла не должен терять часть до «@».
    assert forward.active() == [f]


def test_start_still_refuses_a_host_that_looks_like_an_ssh_option(pid_dir: Path) -> None:
    # Часть до «@» тоже обязана начинаться с буквы или цифры, иначе аргумент
    # уедет в argv ssh как опция.
    with pytest.raises(forward.ForwardError, match="хоста"):
        forward.start("-oProxyCommand=x@m1", [3000])


def test_stop_does_not_kill_a_reused_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    # ssh умер сам, а его pid занял чужой процесс: SIGTERM ушёл бы не туда.
    # Туннеля нет, значит и файл — протухшая запись, её убираем.
    (pid_dir / "m1-3000.pid").write_text("4242")
    killed: list[int] = []
    monkeypatch.setattr(forward, "_is_ssh", lambda pid: False)
    monkeypatch.setattr(forward.os, "kill", lambda pid, sig: killed.append(pid))

    assert forward.stop("m1") == []
    assert killed == []
    assert not (pid_dir / "m1-3000.pid").exists()


def test_is_ssh_reads_the_command_name(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kw: object) -> FakeCompleted:
        seen.append(argv)
        return FakeCompleted(reply["out"])

    reply = {"out": "/usr/bin/ssh\n"}
    monkeypatch.setattr(forward.subprocess, "run", fake_run)
    assert _real_is_ssh(4242) is True
    assert seen == [["ps", "-p", "4242", "-o", "comm="]]

    reply["out"] = "postgres\n"
    assert _real_is_ssh(4242) is False


def test_is_ssh_is_unknown_when_ps_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Без ответа ps ничего не известно — ни что это ssh, ни что это не он.
    def fake_run(argv: list[str], **kw: object) -> None:
        raise OSError("ps нет")

    monkeypatch.setattr(forward.subprocess, "run", fake_run)
    assert _real_is_ssh(4242) is None


def test_stop_keeps_the_pid_file_when_the_process_is_unknown(
    monkeypatch: pytest.MonkeyPatch, pid_dir: Path
) -> None:
    # ps не ответил: ssh мог быть жив и продолжать форвардить порт. Удалить
    # файл значило бы потерять единственную нить к нему — молчаливо и навсегда.
    (pid_dir / "m1-3000.pid").write_text("4242")
    killed: list[int] = []
    monkeypatch.setattr(forward, "_is_ssh", lambda pid: None)
    monkeypatch.setattr(forward.os, "kill", lambda pid, sig: killed.append(pid))

    with pytest.raises(forward.ForwardError, match="3000"):
        forward.stop("m1")
    assert killed == []
    assert (pid_dir / "m1-3000.pid").exists()
