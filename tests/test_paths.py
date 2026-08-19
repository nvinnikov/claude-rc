from pathlib import Path

import pytest
from clauderc import paths


def test_env_var_wins_even_if_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Явно указанный путь возвращаем как есть: сообщение об ошибке должно
    # называть место, которое выбрал человек, а не то, куда мы свернули.
    target = tmp_path / "nowhere" / "config.toml"
    monkeypatch.setenv("CLAUDE_RC_CONFIG", str(target))
    assert paths.config_file() == target


def test_env_var_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_RC_CONFIG", "~/somewhere/config.toml")
    assert paths.config_file() == Path.home() / "somewhere/config.toml"


def test_xdg_path_when_it_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_RC_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    xdg = tmp_path / ".config/claude-rc/config.toml"
    xdg.parent.mkdir(parents=True)
    xdg.write_text("")
    monkeypatch.chdir(tmp_path)
    assert paths.config_file() == xdg


def test_cwd_config_when_xdg_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Разработка внутри клона должна работать без переменных окружения.
    monkeypatch.delenv("CLAUDE_RC_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    local = work / "config.toml"
    local.write_text("")
    monkeypatch.chdir(work)
    assert paths.config_file() == local


def test_falls_back_to_xdg_when_nothing_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_RC_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert paths.config_file() == tmp_path / ".config/claude-rc/config.toml"


def test_log_file_under_claude_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.log_file() == tmp_path / ".claude-rc/claude-rc.log"


def test_claude_projects_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.claude_projects() == tmp_path / ".claude/projects"


def test_claude_projects_honours_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude Code уважает CLAUDE_CONFIG_DIR; без этого мы читали бы чужой каталог.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert paths.claude_projects() == tmp_path / "elsewhere/projects"
