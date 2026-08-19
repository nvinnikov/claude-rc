import json
from pathlib import Path
from typing import Any

import pytest
from clauderc import cli
from clauderc.remote import LaunchError, RemoteSession, TrustRequired


def _session(name: str = "oms") -> RemoteSession:
    return RemoteSession(
        name=name,
        tmux_name=f"rc-{name}",
        cwd=f"/repos/{name}",
        url="https://claude.ai/code/session_A",
        created_at=0,
    )


class _FakeStdin:
    """Подменяет только `isatty()` — `_ask_trust` больше у stdin ничего не спрашивает."""

    def __init__(self, tty: bool) -> None:
        self._is_tty = tty

    def isatty(self) -> bool:
        return self._is_tty


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


def test_start_branch_without_config_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Без этой проверки _start зовёт load_config напрямую, и отсутствие
    # конфига долетает наружу как FileNotFoundError с трейсбеком.
    missing_config = tmp_path / "config.toml"
    monkeypatch.setattr(cli.paths, "config_file", lambda: missing_config)

    assert cli.main(["start", str(tmp_path), "--branch", "feature/x"]) == 2
    err = capsys.readouterr().err
    assert str(missing_config) in err
    assert "Traceback" not in err


def test_start_branch_with_config_creates_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    config.write_text(f'bot_token = "x"\nallowed_user_id = 1\nrc_roots = ["{root}"]\n')
    monkeypatch.setattr(cli.paths, "config_file", lambda: config)

    seen: dict[str, Any] = {}

    async def fake_ensure(target: Path, branch: str, root: Path) -> Path:
        seen["target"] = target
        seen["branch"] = branch
        return tmp_path / "worktree"

    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        seen["cwd"] = cwd
        return _session()

    monkeypatch.setattr(cli.worktrees, "ensure", fake_ensure)
    monkeypatch.setattr(cli, "launch", fake_launch)

    assert cli.main(["start", str(tmp_path), "--branch", "feature/x"]) == 0
    assert seen["branch"] == "feature/x"
    assert seen["cwd"] == str(tmp_path / "worktree")


def test_start_trust_without_tty_fails_without_confirming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Без tty спросить некого — автоподтверждение доверия каталогу запрещено.
    confirmed: list[str] = []

    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        raise TrustRequired("rc-oms", cwd)

    async def fake_confirm(tmux_name: str) -> None:
        confirmed.append(tmux_name)

    monkeypatch.setattr(cli, "launch", fake_launch)
    monkeypatch.setattr(cli, "confirm_trust", fake_confirm)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))

    assert cli.main(["start", str(tmp_path)]) == 2
    assert "tmux attach -t rc-oms" in capsys.readouterr().err
    assert confirmed == []


def test_start_trust_declined_kills_session_without_confirming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    confirmed: list[str] = []
    killed: list[str] = []

    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        raise TrustRequired("rc-oms", cwd)

    async def fake_confirm(tmux_name: str) -> None:
        confirmed.append(tmux_name)

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(cli, "launch", fake_launch)
    monkeypatch.setattr(cli, "confirm_trust", fake_confirm)
    monkeypatch.setattr(cli, "kill_tmux", fake_kill)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    assert cli.main(["start", str(tmp_path)]) == 1
    assert killed == ["rc-oms"]
    assert confirmed == []
    assert capsys.readouterr().err.strip()


def test_start_trust_confirmed_prints_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    confirmed: list[str] = []

    async def fake_launch(repo: str, cwd: str, **kwargs: Any) -> RemoteSession:
        raise TrustRequired("rc-oms", cwd)

    async def fake_confirm(tmux_name: str) -> None:
        confirmed.append(tmux_name)

    async def fake_await_url(name: str, cwd: str, **kwargs: Any) -> RemoteSession:
        assert kwargs["watch_trust"] is False
        return _session()

    monkeypatch.setattr(cli, "launch", fake_launch)
    monkeypatch.setattr(cli, "confirm_trust", fake_confirm)
    monkeypatch.setattr(cli, "await_url", fake_await_url)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert cli.main(["start", str(tmp_path)]) == 0
    assert confirmed == ["rc-oms"]
    assert "https://claude.ai/code/session_A" in capsys.readouterr().out


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


def test_stop_kill_failure_is_not_reported_as_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Сессия нашлась, но tmux не смог её погасить — сообщение не должно вводить
    # в заблуждение, будто цели вообще не было.
    async def fake_list() -> list[RemoteSession]:
        return [_session()]

    async def fake_kill(tmux_name: str) -> bool:
        return False

    monkeypatch.setattr(cli, "list_sessions", fake_list)
    monkeypatch.setattr(cli, "kill_tmux", fake_kill)
    assert cli.main(["stop", "oms"]) == 1
    err = capsys.readouterr().err
    assert "не найдена" not in err
    assert "oms" in err


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


def test_doctor_never_prints_the_token_on_broken_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Токен выше по файлу, синтаксическая ошибка ниже — `except` печатает `str(exc)`,
    # и это не должно ронять его в сообщение об ошибке.
    config = tmp_path / "config.toml"
    config.write_text('bot_token = "123456:SECRET_VALUE"\nallowed_user_id = 1\nbroken = [\n')
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli.paths, "config_file", lambda: config)

    assert cli.main(["doctor", "--json"]) == 2
    captured = capsys.readouterr()
    assert "SECRET_VALUE" not in captured.out
    assert "SECRET_VALUE" not in captured.err


def test_setup_without_tty_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Заполнять нечего, если некому отвечать. Та же логика, что у диалога доверия.
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
    assert cli.main(["setup"]) == 2
    assert capsys.readouterr().err.strip()


def test_setup_writes_config_with_tight_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    # Тесты идут вслепую относительно того, крутится ли на машине настоящий бот —
    # без этой заглушки «n» на автоподхват уехало бы не туда, если pgrep его найдёт.
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    answers = iter([token, "n", "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert target.is_file()
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    # Каталог конфига тоже не должен быть доступен на чтение чужим пользователям.
    assert oct(target.parent.stat().st_mode & 0o777) == "0o700"

    config = cli.load_config(target)
    assert config.allowed_user_id == 42
    assert config.rc_roots == (root,)


def test_setup_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    secret = "123456:SECRETVALUEabcdefghijklmnopqrstuvwxyz"

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    answers = iter([secret, "n", "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    cli.main(["setup"])
    captured = capsys.readouterr()
    assert "SECRETVALUE" not in captured.out
    assert "SECRETVALUE" not in captured.err


def test_setup_keeps_existing_values_on_empty_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    target.write_text(
        cli.setup.render_config(
            cli.setup.Answers(bot_token=token, allowed_user_id=7, rc_roots=(root,))
        )
    )

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    config = cli.load_config(target)
    assert config.bot_token == token
    assert config.allowed_user_id == 7


def test_setup_rejects_missing_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    # Каталог не существует, и человек повторяет ответ — визард переспрашивает,
    # а не пишет заведомо нерабочий конфиг.
    answers = iter([token, "n", "42", str(tmp_path / "nope"), str(tmp_path / "nope")])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 2
    assert not target.exists()


def test_setup_does_not_poll_when_user_id_already_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Поллинг рядом с уже настроенным (и, возможно, запущенным) ботом дал бы
    # второго поллера того же токена — Telegram отдаёт конфликт, оба работают
    # через раз.
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    target.write_text(
        cli.setup.render_config(
            cli.setup.Answers(bot_token=token, allowed_user_id=7, rc_roots=(root,))
        )
    )

    def explode(value: str, **kwargs: object) -> int:
        raise AssertionError("поллинг при известном user_id недопустим")

    monkeypatch.setattr(cli.setup, "catch_user_id", explode)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 7


def test_setup_falls_back_to_manual_user_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    def explode(value: str, **kwargs: object) -> int:
        raise AssertionError("отказались от автоподхвата — в сеть не ходим")

    monkeypatch.setattr(cli.setup, "catch_user_id", explode)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    # токен, «n» на автоподхват, user_id, каталоги
    answers = iter([token, "n", "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 42


def test_setup_rerun_keeps_hand_edited_scan_depth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # scan_depth визард не спрашивает — раньше повторный запуск с Enter на все
    # вопросы молча стирал его, откатывая правку из README на дефолт.
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    target.write_text(
        cli.setup.render_config(
            cli.setup.Answers(bot_token=token, allowed_user_id=7, rc_roots=(root,)),
            extra={"scan_depth": 5},
        )
    )

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).scan_depth == 5


def test_setup_ctrl_c_during_autopickup_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ctrl+C во время ожидания сообщения не должен ронять визард трейсбеком —
    # подсказка обещает только «прервать», а не гарантированный переход к
    # ручному вводу (asyncio.run Ctrl+C ловится не там, где кажется).
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    def interrupted(value: str, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.setup, "catch_user_id", interrupted)
    monkeypatch.setattr(cli, "_agrees", lambda question: True)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": token)

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == cli.EXIT_ENVIRONMENT
    assert "Traceback" not in capsys.readouterr().err
    assert not target.exists()


def test_setup_rejected_token_and_missing_user_id_share_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Обе причины отказаться — не приняли токен, не назвали user_id — одной
    # природы: человек не смог ответить. Код возврата должен быть одинаковым.
    target = tmp_path / "config.toml"

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "not-a-token")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(False, None, False, "Telegram отверг токен")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == cli.EXIT_ENVIRONMENT
    assert not target.exists()


def test_setup_does_not_poll_when_config_exists_but_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Самая частая причина перезапустить визард — конфиг перестал читаться (например,
    # каталог из rc_roots исчез), а не то, что его вообще не было. Это тоже сигнал
    # «бот, скорее всего, настроен и работает» — автоподхват не должен предлагаться,
    # даже если бы человек на него согласился, иначе рядом с работающим ботом
    # поднимется второй поллер того же токена.
    target = tmp_path / "config.toml"
    missing_root = tmp_path / "gone"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    target.write_text(
        f'bot_token = "{token}"\nallowed_user_id = 7\nrc_roots = ["{missing_root}"]\n'
    )

    def explode(value: str, **kwargs: object) -> int:
        raise AssertionError("поллинг при существующем, но битом конфиге недопустим")

    monkeypatch.setattr(cli.setup, "catch_user_id", explode)
    # Форсируем согласие: без него баг маскируется случайным несовпадением ответа
    # с «да», а фикс обязан пропускать вопрос вовсе — независимо от ответа.
    monkeypatch.setattr(cli, "_agrees", lambda question: True)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)

    root = tmp_path / "code"
    root.mkdir()
    answers = iter([token, "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 42


def test_setup_narrows_permissions_of_preexisting_loose_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # os.open(..., O_CREAT, 0o600) выставляет режим только при СОЗДАНИИ файла —
    # если config.toml уже лежал с более широкими правами (например, от версии
    # до этой правки), повторный setup обязан их сузить, а не оставить как есть.
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    target.write_text(
        cli.setup.render_config(
            cli.setup.Answers(bot_token=token, allowed_user_id=7, rc_roots=(root,))
        )
    )
    target.chmod(0o644)

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_setup_reports_permission_error_instead_of_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Каталог конфига без права на обход (`x`) роняет `Path.exists()` наружу
    # `PermissionError` — человек должен увидеть сообщение, а не трейсбек.
    target = tmp_path / "config.toml"

    def explode(path: object) -> bool:
        raise PermissionError("нет доступа к каталогу")

    monkeypatch.setattr(cli.os.path, "lexists", explode)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)

    assert cli.main(["setup"]) == cli.EXIT_ENVIRONMENT
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(target) in err


def test_setup_skips_autopickup_when_bot_process_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Прокси («файла нет») недостаточно: CLAUDE_RC_CONFIG может указывать на
    # несуществующий путь, пока бот работает с другим конфигом. Прямая проверка —
    # найден ли живой процесс — не должна поллить, даже если бы человек согласился.
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    def explode(value: str, **kwargs: object) -> int:
        raise AssertionError("бот уже работает — поллинг недопустим")

    monkeypatch.setattr(cli.setup, "catch_user_id", explode)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=4242, checked=True)
    )
    monkeypatch.setattr(cli, "_agrees", lambda question: True)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    answers = iter([token, "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 42


def test_setup_offers_autopickup_when_no_bot_process_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    called: list[str] = []

    async def fake_catch(value: str, **kwargs: object) -> int:
        called.append(value)
        return 99

    monkeypatch.setattr(cli.setup, "catch_user_id", fake_catch)
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    # токен, «y» на автоподхват, каталоги — user_id ловится автоподхватом
    answers = iter([token, "y", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert called == [token]
    assert cli.load_config(target).allowed_user_id == 99


def test_foreign_bot_pid_reports_unchecked_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(*args: object, **kwargs: object) -> object:
        raise cli.subprocess.TimeoutExpired(cmd="pgrep", timeout=2)

    monkeypatch.setattr(cli.subprocess, "run", timed_out)
    check = cli._foreign_bot_pid()
    assert check.pid is None
    assert check.checked is False


def test_foreign_bot_pid_reports_unchecked_when_pgrep_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("pgrep не найден")

    monkeypatch.setattr(cli.subprocess, "run", missing)
    check = cli._foreign_bot_pid()
    assert check.pid is None
    assert check.checked is False


def test_setup_warns_and_asks_when_bot_check_failed_but_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # pgrep не ответил вовремя — не «бота нет», а «не смогли проверить». Молчание
    # тут хуже отказа: человек согласился бы на автоподхват, не зная, что guard
    # не сработал. Он должен увидеть предупреждение и явно подтвердить риск.
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    def explode(value: str, **kwargs: object) -> int:
        raise AssertionError("отказ от риска — поллинг недопустим")

    monkeypatch.setattr(cli.setup, "catch_user_id", explode)
    monkeypatch.setattr(cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(None, False))
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    # токен, «n» на предупреждение-подтверждение, user_id, каталоги
    answers = iter([token, "n", "42", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    err = capsys.readouterr().err
    assert "не удалось проверить" in err.lower()
    assert cli.load_config(target).allowed_user_id == 42


def test_setup_proceeds_when_bot_check_failed_and_human_confirms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    root = tmp_path / "code"
    root.mkdir()
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    called: list[str] = []

    async def fake_catch(value: str, **kwargs: object) -> int:
        called.append(value)
        return 77

    monkeypatch.setattr(cli.setup, "catch_user_id", fake_catch)
    monkeypatch.setattr(cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(None, False))
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    # токен, «y» на предупреждение-подтверждение, каталоги
    answers = iter([token, "y", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert "не удалось проверить" in capsys.readouterr().err.lower()
    assert called == [token]
    assert cli.load_config(target).allowed_user_id == 77


def test_setup_warns_about_dropped_extra_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # scan_depth — известный ключ, но со значением, для которого render_config не
    # умеет закодировать TOML (список вместо числа — например, после ручной
    # опечатки). Молча отбрасывать его нельзя: человек должен узнать, какой
    # именно ключ пропал и почему, а не найти это полгода спустя.
    #
    # _current_extras подменяем напрямую, а не кладём битый TOML на диск: такое
    # значение не проходит и int(raw.get("scan_depth", ...)) в load_config, то
    # есть уронило бы весь _current_answers ещё до этой ветки — а тест целится
    # именно в предупреждение при записи, не в устойчивость парсинга.
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.paths, "config_file", lambda: target)
    monkeypatch.setattr(cli, "_current_extras", lambda t: {"scan_depth": ["a", "b"]})
    monkeypatch.setattr(
        cli, "_foreign_bot_pid", lambda: cli._ForeignBotCheck(pid=None, checked=True)
    )
    answers = iter([token, "y", str(root)])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_catch(value: str, **kwargs: object) -> int:
        return 42

    monkeypatch.setattr(cli.setup, "catch_user_id", fake_catch)

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    err = capsys.readouterr().err
    assert "scan_depth" in err
    assert "list" in err
    assert "scan_depth" not in target.read_text()
