import tomllib
from pathlib import Path

import pytest
from clauderc import setup
from clauderc.config import load_config
from clauderc.setup import Answers, RootsError


def _answers(root: Path, token: str = "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890") -> Answers:
    return Answers(bot_token=token, allowed_user_id=42, rc_roots=(root,))


def test_render_config_round_trips_through_load_config(tmp_path: Path) -> None:
    # Самый ценный тест здесь: то, что визард пишет, обязано читаться боевым
    # загрузчиком. Иначе человек узнает о расхождении на первом запуске бота.
    root = tmp_path / "code"
    root.mkdir()
    target = tmp_path / "config.toml"
    target.write_text(setup.render_config(_answers(root)))

    config = load_config(target)
    assert config.bot_token == "123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
    assert config.allowed_user_id == 42
    assert config.rc_roots == (root,)


def test_render_config_escapes_token(tmp_path: Path) -> None:
    # В токене есть двоеточие, а в путях может быть что угодно — значения
    # обязаны быть корректными строками TOML, а не склейкой.
    root = tmp_path / "code"
    root.mkdir()
    text = setup.render_config(_answers(root, token='12:ab"cd\\ef'))
    parsed = tomllib.loads(text)
    assert parsed["bot_token"] == '12:ab"cd\\ef'


def test_render_config_keeps_several_roots(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    answers = Answers(bot_token="1:x", allowed_user_id=1, rc_roots=(first, second))
    parsed = tomllib.loads(setup.render_config(answers))
    assert parsed["rc_roots"] == [str(first), str(second)]


def test_parse_roots_splits_on_comma(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    got = setup.parse_roots(f"{first}, {second}", default=())
    assert got == (first, second)


def test_parse_roots_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "code").mkdir()
    assert setup.parse_roots("~/code", default=()) == (tmp_path / "code",)


def test_parse_roots_empty_input_gives_default(tmp_path: Path) -> None:
    root = tmp_path / "d"
    root.mkdir()
    assert setup.parse_roots("   ", default=(root,)) == (root,)


def test_parse_roots_rejects_missing_directory(tmp_path: Path) -> None:
    # Ошибка на месте, а не через полчаса при первом /repos: load_config
    # такой конфиг всё равно не примет.
    with pytest.raises(RootsError) as excinfo:
        setup.parse_roots(str(tmp_path / "nope"), default=())
    assert "nope" in str(excinfo.value)


def test_parse_roots_rejects_empty_input_without_default() -> None:
    with pytest.raises(RootsError):
        setup.parse_roots("", default=())


def test_mask_token_hides_the_middle() -> None:
    masked = setup.mask_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert masked.startswith("123456:")
    assert masked.endswith("7890")
    assert "ABCdefGHIjkl" not in masked


def test_mask_token_handles_short_and_odd_values() -> None:
    assert setup.mask_token("") == ""
    assert "x" not in setup.mask_token("x")


def test_looks_like_token_accepts_real_shape() -> None:
    assert setup.looks_like_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")


def test_looks_like_token_rejects_garbage() -> None:
    assert not setup.looks_like_token("")
    assert not setup.looks_like_token("простотекст")
    assert not setup.looks_like_token("123456")
    assert not setup.looks_like_token(":ABCdef")


def test_default_roots_prefers_documents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Documents").mkdir()
    assert setup.default_roots() == (tmp_path / "Documents",)


def test_default_roots_falls_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert setup.default_roots() == (tmp_path,)
