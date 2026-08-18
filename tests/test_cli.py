import json
from pathlib import Path
from typing import Any

import pytest
from clauderc import cli
from clauderc.remote import LaunchError, RemoteSession


def _session(name: str = "oms") -> RemoteSession:
    return RemoteSession(
        name=name,
        tmux_name=f"rc-{name}",
        cwd=f"/repos/{name}",
        url="https://claude.ai/code/session_A",
        created_at=0,
    )


def test_no_command_prints_usage_and_fails() -> None:
    assert cli.main([]) == 2


def test_version_prints_something(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip()


def test_sessions_json_has_stable_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake() -> list[RemoteSession]:
        return [_session()]

    monkeypatch.setattr(cli, "list_sessions", fake)
    assert cli.main(["sessions", "--json"]) == 0

    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    # Объект, а не голый массив: поле можно будет добавить, не ломая читателей.
    assert list(payload) == ["sessions"]
    assert payload["sessions"][0]["name"] == "oms"
    assert payload["sessions"][0]["url"] == "https://claude.ai/code/session_A"


def test_sessions_plain_lists_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake() -> list[RemoteSession]:
        return [_session()]

    monkeypatch.setattr(cli, "list_sessions", fake)
    assert cli.main(["sessions"]) == 0
    assert "oms" in capsys.readouterr().out


def test_sessions_empty_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake() -> list[RemoteSession]:
        return []

    monkeypatch.setattr(cli, "list_sessions", fake)
    assert cli.main(["sessions"]) == 0
    assert capsys.readouterr().out.strip()


def test_start_prints_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        assert kwargs["resume"] is None
        return _session()

    monkeypatch.setattr(cli, "launch", fake_launch)
    assert cli.main(["start", str(tmp_path)]) == 0
    assert "https://claude.ai/code/session_A" in capsys.readouterr().out


def test_start_passes_resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        seen.update(kwargs)
        return _session()

    monkeypatch.setattr(cli, "launch", fake_launch)
    assert cli.main(["start", str(tmp_path), "--resume", "abc"]) == 0
    assert seen["resume"] == "abc"


def test_start_reports_launch_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        raise LaunchError("tmux умер")

    monkeypatch.setattr(cli, "launch", fake_launch)
    assert cli.main(["start", str(tmp_path)]) == 1
    assert "tmux умер" in capsys.readouterr().err


def test_start_rejects_missing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["start", str(tmp_path / "nope")]) == 2
    assert capsys.readouterr().err.strip()


def test_start_rejects_empty_resume(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # remote._resume_flag("") даёт `--resume ''` в командной строке tmux — ловим
    # пустую строку раньше, чем она туда доедет.
    assert cli.main(["start", str(tmp_path), "--resume", ""]) == 2
    assert capsys.readouterr().err.strip()


def test_stop_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def fake_list() -> list[RemoteSession]:
        return [_session()]

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(cli, "list_sessions", fake_list)
    monkeypatch.setattr(cli, "kill_tmux", fake_kill)
    assert cli.main(["stop", "oms"]) == 0
    assert killed == ["rc-oms"]


def test_stop_by_path(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def fake_list() -> list[RemoteSession]:
        return [_session()]

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(cli, "list_sessions", fake_list)
    monkeypatch.setattr(cli, "kill_tmux", fake_kill)
    assert cli.main(["stop", "/repos/oms"]) == 0
    assert killed == ["rc-oms"]


def test_stop_unknown_target_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_list() -> list[RemoteSession]:
        return []

    monkeypatch.setattr(cli, "list_sessions", fake_list)
    assert cli.main(["stop", "ghost"]) == 1
    assert capsys.readouterr().err.strip()


def test_stop_all(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def fake_kill_all() -> int:
        return 3

    monkeypatch.setattr(cli, "kill_all", fake_kill_all)
    assert cli.main(["stop", "--all"]) == 0
    assert "3" in capsys.readouterr().out


def test_doctor_json_envelope_and_failure_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.paths, "config_file", lambda: tmp_path / "config.toml")

    assert cli.main(["doctor", "--json"]) == 2
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert list(payload) == ["checks"]
    names = {check["name"] for check in payload["checks"]}
    assert {"tmux", "claude", "config"} <= names
    assert all(check["ok"] is False for check in payload["checks"] if check["name"] == "tmux")


def test_doctor_all_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    config.write_text(f'bot_token = "x"\nallowed_user_id = 1\nrc_roots = ["{root}"]\n')
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli.paths, "config_file", lambda: config)

    assert cli.main(["doctor", "--json"]) == 0
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert all(check["ok"] for check in payload["checks"])


def test_doctor_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # doctor читает config.toml — секрет не должен уехать ни в stdout, ни в JSON.
    config = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    config.write_text(
        f'bot_token = "123456:SECRET_VALUE"\nallowed_user_id = 1\nrc_roots = ["{root}"]\n'
    )
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli.paths, "config_file", lambda: config)

    cli.main(["doctor", "--json"])
    captured = capsys.readouterr()
    assert "SECRET_VALUE" not in captured.out
    assert "SECRET_VALUE" not in captured.err
