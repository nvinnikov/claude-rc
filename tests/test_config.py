from pathlib import Path

import pytest

from tgclaude.config import Config, load_config


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def test_load_config_reads_all_fields(tmp_path: Path):
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
        "launch_timeout_s = 30\n",
    )

    assert load_config(cfg) == Config(
        bot_token="abc",
        allowed_user_id=42,
        rc_roots=(code,),
        worktree_root=tmp_path / "wt",
        state_path=tmp_path / "state.json",
        scan_depth=2,
        launch_timeout_s=30.0,
    )


def test_worktree_root_defaults_and_need_not_exist(tmp_path: Path):
    cfg = _write(
        tmp_path, f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n'
    )

    # каталог создаётся при первом worktree, заранее его существования не требуем
    assert load_config(cfg).worktree_root == Path("~/.tg-claude/worktrees").expanduser()


def test_defaults_are_applied(tmp_path: Path):
    cfg = _write(
        tmp_path,
        f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["{tmp_path}"]\n',
    )

    loaded = load_config(cfg)

    assert loaded.scan_depth == 3
    assert loaded.launch_timeout_s == 90.0


def test_rc_roots_accepts_bare_string(tmp_path: Path):
    cfg = _write(
        tmp_path, f'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = "{tmp_path}"\n'
    )

    assert load_config(cfg).rc_roots == (tmp_path,)


def test_rc_roots_defaults_to_home(tmp_path: Path):
    cfg = _write(tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\n')

    assert load_config(cfg).rc_roots == (Path.home(),)


def test_leftover_keys_from_chat_version_are_ignored(tmp_path: Path):
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


def test_missing_required_field_raises(tmp_path: Path):
    cfg = _write(tmp_path, 'bot_token = "abc"\n')

    with pytest.raises(KeyError):
        load_config(cfg)


def test_nonexistent_root_raises(tmp_path: Path):
    cfg = _write(
        tmp_path, 'bot_token = "abc"\nallowed_user_id = 1\nrc_roots = ["/no/such/dir"]\n'
    )

    with pytest.raises(ValueError, match="rc_roots"):
        load_config(cfg)
