import pytest
from clauderc import proxy


def test_strip_host_handles_both_spellings() -> None:
    assert proxy.strip_host(["--host", "m1", "sessions"]) == ("m1", ["sessions"])
    assert proxy.strip_host(["--host=m1", "sessions", "--json"]) == ("m1", ["sessions", "--json"])
    assert proxy.strip_host(["sessions"]) == (None, ["sessions"])


@pytest.mark.parametrize(
    "argv",
    [
        ["sessions", "--host"],
        ["--host", "--json", "sessions"],
        ["--host=", "sessions"],
    ],
)
def test_strip_host_rejects_missing_value(argv: list[str]) -> None:
    with pytest.raises(proxy.HostError, match="--host"):
        proxy.strip_host(argv)


def test_command_of_skips_options() -> None:
    assert proxy.command_of(["--json", "sessions"]) == "sessions"
    assert proxy.command_of([]) is None


def test_relative_paths_only_for_path_commands() -> None:
    assert proxy.relative_paths(["start", "."]) == ["."]
    assert proxy.relative_paths(["start", "../x"]) == ["../x"]
    assert proxy.relative_paths(["start", "/abs"]) == []
    assert proxy.relative_paths(["start", "~/code"]) == []
    # start без пути — это «.» на той стороне, то есть домашний каталог сервера
    assert proxy.relative_paths(["start"]) == ["."]
    assert proxy.relative_paths(["start", "--name", "x", "/abs"]) == []
    # у send текст может содержать «/», это не путь
    assert proxy.relative_paths(["send", "oms", "run ./build.sh"]) == []


def test_remote_argv_quotes_and_prepends_path() -> None:
    argv = proxy.remote_argv("m1", ["send", "oms@x", "hi there; rm -rf /"], tty=False)
    # "--" перед host: хост, начинающийся с "-", не должен читаться как опция ssh.
    assert argv[:4] == ["ssh", "-T", "--", "m1"]
    command = argv[4]
    # PATH: неинтерактивный ssh не читает .zshrc, и ~/.local/bin (uv tool) там нет
    assert command.startswith('export PATH="$HOME/.local/bin:$PATH"; ')
    assert "claude-rc send oms@x 'hi there; rm -rf /'" in command


def test_remote_argv_tty_flag() -> None:
    assert proxy.remote_argv("m1", ["connect"], tty=True)[1] == "-t"


def test_exec_remote_uses_execvp(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(proxy.os, "execvp", lambda file, args: seen.append((file, args)))
    proxy.exec_remote("m1", ["connect", "oms"])
    assert seen[0][0] == "ssh"
    assert seen[0][1][1] == "-t"


def test_run_remote_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    seen: list[list[str]] = []

    def fake_run(
        argv: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool
    ) -> FakeCompleted:
        seen.append(argv)
        return FakeCompleted()

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)
    code, out = proxy.run_remote("m1", ["sessions", "--json"])
    assert code == 0
    assert out == "ok\n"
    assert seen[0][:4] == ["ssh", "-T", "--", "m1"]


def test_run_remote_returns_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "boom\n"

    monkeypatch.setattr(proxy.subprocess, "run", lambda *a, **k: FakeCompleted())
    code, out = proxy.run_remote("m1", ["sessions"])
    assert code == 1
    assert out == "boom\n"


def test_run_remote_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> None:
        raise proxy.subprocess.TimeoutExpired(cmd="ssh", timeout=5.0)

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)
    code, out = proxy.run_remote("m1", ["sessions"], timeout_s=5.0)
    assert code == 1
    assert "m1" in out
    assert "5" in out


def test_strip_host_stops_at_the_double_dash() -> None:
    # После «--» идут аргументы команды: `send oms -- --host` посылает в сессию
    # текст «--host», а не требует имя машины, которого там нет.
    assert proxy.strip_host(["send", "oms", "--", "--host"]) == (
        None,
        ["send", "oms", "--", "--host"],
    )
    assert proxy.strip_host(["--host", "m1", "send", "oms", "--", "--host=x"]) == (
        "m1",
        ["send", "oms", "--", "--host=x"],
    )


def test_relative_paths_covers_connect() -> None:
    # `connect --start` поднимает сессию в каталоге, и пустая цель на той стороне
    # означала бы домашний каталог удалённой машины, а не текущий здесь.
    assert proxy.relative_paths(["connect", "--start"]) == ["."]
    assert proxy.relative_paths(["connect", ".", "--start"]) == ["."]
    assert proxy.relative_paths(["connect", "../x", "--start"]) == ["../x"]
    assert proxy.relative_paths(["connect", "--start", "--branch", "feat/x", "/abs"]) == []


def test_relative_paths_lets_a_connect_label_through() -> None:
    # Цель `connect` — не обязательно путь: ярлык и session_… резолвит та сторона.
    assert proxy.relative_paths(["connect", "oms@x"]) == []
    assert proxy.relative_paths(["connect", "session_01A", "--read-only"]) == []
    # Без --start пустая цель — «единственная живая сессия», а не каталог.
    assert proxy.relative_paths(["connect"]) == []
