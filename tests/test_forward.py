import socket
from pathlib import Path

import pytest
from clauderc import forward


@pytest.fixture
def pid_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(forward.PID_DIR_ENV, str(tmp_path))
    return tmp_path


class _FakeProc:
    def __init__(self, argv: list[str], **kw: object) -> None:
        self.argv = argv
        self.pid = 4242


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


def test_active_lists_pid_files(pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("1")
    (pid_dir / "m3-80.pid").write_text("2")
    assert forward.active() == [
        forward.Forward(host="m1", port=3000, pid=1),
        forward.Forward(host="m3", port=80, pid=2),
    ]
