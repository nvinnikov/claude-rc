from pathlib import Path

import pytest
from clauderc.config import Config, load_config


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def test_load_config_reads_all_fields(tmp_path: Path) -> None:
    code = tmp_path / "code"
    code.mkdir()
    cfg = _write(
        tmp_path,
        'bot_token = "abc"\n'
        "allowed_user_id = 42\n"
        f'rc_roots = ["{code}"]\n'
        f'worktree_root = "{tmp_path / "wt"}"\n'
        f'state_path = "{tmp_path / "state.json"}"\n'
        "scan_depth = 2\n"
        "launch_timeout_s = 30\n"
        'permission_mode = "acceptEdits"\n'
        "pull_before_start = true\n",
    )

    assert load_config(cfg) == Config(
        bot_token="abc",
        allowed_user_id=42,
        rc_roots=(code,),
        worktree_root=tmp_path / "wt",
        state_path=tmp_path / "state.json",
        scan_depth=2,
        launch_timeout_s=30.0,
        permission_mode="acceptEdits",
        pull_before_start=True,
    )


def test_worktree_root_defaults_and_need_not_exist(tmp_path: Path) -> None:
    cfg = _write(tmp_path, f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n')

    # каталог создаётся при первом worktree, заранее его существования не требуем
    assert load_config(cfg).worktree_root == Path("~/.claude-rc/worktrees").expanduser()


def test_defaults_are_applied(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n',
    )

    loaded = load_config(cfg)

    assert loaded.scan_depth == 3
    assert loaded.launch_timeout_s == 90.0


def test_rc_roots_accepts_bare_string(tmp_path: Path) -> None:
    cfg = _write(tmp_path, f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = "{tmp_path}"\n')

    assert load_config(cfg).rc_roots == (tmp_path,)


def test_rc_roots_defaults_to_home(tmp_path: Path) -> None:
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\n')

    assert load_config(cfg).rc_roots == (Path.home(),)


def test_leftover_keys_from_chat_version_are_ignored(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        'bot_token = "abc"\n'
        "allowed_user_id = 1\n"
        "chat_id = -100500\n"
        'default_cwd = "~"\n'
        'db_path = "sessions.db"\n'
        f'rc_roots = ["{tmp_path}"]\n',
    )

    assert load_config(cfg).bot_token == "abc"


def test_missing_required_field_raises(tmp_path: Path) -> None:
    cfg = _write(tmp_path, 'bot_token = "abc"\n')

    with pytest.raises(KeyError):
        load_config(cfg)


def test_nonexistent_root_raises(tmp_path: Path) -> None:
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["/no/such/dir"]\n')

    with pytest.raises(ValueError, match="rc_roots"):
        load_config(cfg)


def test_wrong_typed_bot_token_raises_value_error_without_leaking_it(tmp_path: Path) -> None:
    # bot_token = 123456 (без кавычек) — TOML принял бы как целое число.
    cfg = _write(tmp_path, "bot_token = 123456\nallowed_user_id = 1\n")

    with pytest.raises(ValueError, match="bot_token") as excinfo:
        load_config(cfg)
    assert "123456" not in str(excinfo.value)


def test_wrong_typed_allowed_user_id_raises_value_error(tmp_path: Path) -> None:
    # Бытовой сценарий: человек пишет число в кавычках, как принято во многих
    # форматах — allowed_user_id = "123" вместо allowed_user_id = 123.
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = "123"\n')

    with pytest.raises(ValueError, match="allowed_user_id"):
        load_config(cfg)


def test_bool_allowed_user_id_is_rejected(tmp_path: Path) -> None:
    # bool — подкласс int в Python: без явной проверки true тихо стало бы 1.
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = true\n')

    with pytest.raises(ValueError, match="allowed_user_id"):
        load_config(cfg)


def test_wrong_typed_rc_roots_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = 5\n')

    with pytest.raises(ValueError, match="rc_roots"):
        load_config(cfg)


def test_rc_roots_with_non_string_item_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = [5]\n')

    with pytest.raises(ValueError, match="rc_roots"):
        load_config(cfg)


def test_wrong_typed_scan_depth_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\nscan_depth = "3"\n',
    )

    with pytest.raises(ValueError, match="scan_depth"):
        load_config(cfg)


def test_wrong_typed_launch_timeout_s_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n'
        'launch_timeout_s = "90"\n',
    )

    with pytest.raises(ValueError, match="launch_timeout_s"):
        load_config(cfg)


def test_wrong_typed_worktree_root_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\nworktree_root = 5\n',
    )

    with pytest.raises(ValueError, match="worktree_root"):
        load_config(cfg)


def test_wrong_typed_state_path_raises_value_error(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\nstate_path = 5\n',
    )

    with pytest.raises(ValueError, match="state_path"):
        load_config(cfg)


def test_permission_mode_defaults_to_none(tmp_path: Path) -> None:
    cfg = _write(tmp_path, f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n')
    assert load_config(cfg).permission_mode is None
    assert load_config(cfg).pull_before_start is False


def test_permission_mode_rejects_an_unknown_mode(tmp_path: Path) -> None:
    # claude на неизвестном режиме не стартует, и человек увидел бы мёртвую
    # сессию вместо «в конфиге не тот режим».
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n'
        'permission_mode = "yolo"\n',
    )
    with pytest.raises(ValueError, match="permission_mode"):
        load_config(cfg)


def test_pull_before_start_rejects_a_non_boolean(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n'
        'pull_before_start = "yes"\n',
    )
    with pytest.raises(ValueError, match="pull_before_start"):
        load_config(cfg)
