import tomllib
from pathlib import Path

import pytest
from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetMe, GetUpdates
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


def test_render_config_appends_extra_fields_with_toml_types(tmp_path: Path) -> None:
    # extra — поля, которых визард не спрашивает (worktree_root, scan_depth и
    # т.п.), но которые визард обязан переносить из прежнего файла при
    # перезаписи. Строки и числа должны остаться строками и числами в TOML,
    # а не превратиться друг в друга.
    root = tmp_path / "code"
    root.mkdir()
    text = setup.render_config(
        _answers(root),
        extra={
            "worktree_root": "~/.claude-rc/worktrees",
            "scan_depth": 5,
            "launch_timeout_s": 45.0,
        },
    )
    parsed = tomllib.loads(text)
    assert parsed["worktree_root"] == "~/.claude-rc/worktrees"
    assert parsed["scan_depth"] == 5
    assert parsed["launch_timeout_s"] == 45.0


def test_render_config_without_extra_matches_plain_call(tmp_path: Path) -> None:
    # extra=None и отсутствие аргумента вовсе обязаны давать один и тот же файл —
    # старые вызовы (cli.py до этой правки, тесты) не должны почувствовать разницу.
    root = tmp_path / "code"
    root.mkdir()
    assert setup.render_config(_answers(root)) == setup.render_config(_answers(root), extra=None)
    assert setup.render_config(_answers(root)) == setup.render_config(_answers(root), extra={})


def test_render_config_encodes_bools_as_lowercase_toml(tmp_path: Path) -> None:
    # bool — подкласс int в Python: наивный str(value) дал бы "True"/"False",
    # а это не валидный TOML. Регрессия: раньше scan_depth=True ломал файл.
    root = tmp_path / "code"
    root.mkdir()
    text = setup.render_config(_answers(root), extra={"scan_depth": True})
    assert "scan_depth = true" in text
    assert tomllib.loads(text)["scan_depth"] is True


def test_render_config_drops_values_of_unknown_type(tmp_path: Path) -> None:
    # Значение неизвестного типа (список, таблица и т.п.) не переносим вовсе —
    # str(value) дал бы синтаксис, который tomllib не читает обратно, то есть
    # визард, запущенный поправить каталоги, сделал бы файл нечитаемым насмерть.
    root = tmp_path / "code"
    root.mkdir()
    text = setup.render_config(
        _answers(root), extra={"scan_depth": 5, "rc_roots_backup": ["a", "b"]}
    )
    parsed = tomllib.loads(text)  # не должно бросать — файл обязан разбираться
    assert parsed["scan_depth"] == 5
    assert "rc_roots_backup" not in parsed


def test_unsupported_extra_keys_names_dropped_key_and_its_type() -> None:
    # cli.py печатает предупреждение по этому списку — человек должен узнать,
    # какое поле пропало и почему, а не найти это через полгода.
    dropped = setup.unsupported_extra_keys({"scan_depth": 5, "rc_roots_backup": ["a", "b"]})
    assert dropped == (("rc_roots_backup", "list"),)


def test_unsupported_extra_keys_empty_for_supported_values() -> None:
    assert setup.unsupported_extra_keys({"scan_depth": 5, "worktree_root": "~/x"}) == ()
    assert setup.unsupported_extra_keys(None) == ()
    assert setup.unsupported_extra_keys({}) == ()


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


class _FakeMe:
    def __init__(self, username: str) -> None:
        self.username = username


class _FakeBot:
    """Заглушка aiogram.Bot: тесты не ходят в сеть."""

    def __init__(self, token: str, behaviour: str = "ok") -> None:
        self.token = token
        self.behaviour = behaviour

    async def get_me(self) -> _FakeMe:
        if self.behaviour == "offline":
            # То, что реально бросает aiogram при обрыве сети — заворачивает
            # asyncio.TimeoutError/aiohttp.ClientError сюда. Голый OSError он
            # никогда не пропускает наружу.
            raise TelegramNetworkError(method=GetMe(), message="нет сети")
        if self.behaviour == "bad":
            raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")
        if self.behaviour == "rate_limited":
            raise TelegramRetryAfter(method=GetMe(), message="Too Many Requests", retry_after=30)
        if self.behaviour == "server_error":
            raise TelegramServerError(method=GetMe(), message="Internal Server Error")
        return _FakeMe("my_test_bot")

    async def session_close(self) -> None:
        return None


async def test_verify_token_reports_bot_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token))
    check = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert check.ok is True
    assert check.bot_name == "my_test_bot"
    assert check.offline is False


async def test_verify_token_distinguishes_offline_from_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Различие важное: без сети продолжать можно, с неверным токеном — нет.
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "offline"))
    offline = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert offline.ok is False
    assert offline.offline is True

    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "bad"))
    rejected = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert rejected.ok is False
    assert rejected.offline is False


async def test_verify_token_rejects_only_on_real_unauthorized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Регрессия: раньше отказ проверялся общей TelegramAPIError, а не тем
    # классом, который реально означает "токен неверный" (401).
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "bad"))
    check = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert check.ok is False
    assert check.offline is False


async def test_verify_token_does_not_blame_the_token_for_rate_limiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 429 (TelegramRetryAfter) — сам Telegram ограничил частоту, токен ни при
    # чём. Раньше это тоже уходило в "Telegram отверг токен", и человек шёл
    # перевыпускать рабочий токен у @BotFather.
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "rate_limited"))
    check = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert check.ok is False
    assert check.offline is True
    assert "отверг" not in check.detail.lower()
    assert "ни при чём" in check.detail.lower()


async def test_verify_token_does_not_blame_the_token_for_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 5xx (TelegramServerError, включая RestartingTelegram) — тоже не про
    # токен, а про временную неполадку на стороне Telegram.
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "server_error"))
    check = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert check.ok is False
    assert check.offline is True
    assert "отверг" not in check.detail.lower()


async def test_verify_token_recognizes_real_aiogram_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Регрессия: `except OSError` не ловит обрыв сети — aiogram заворачивает его
    # в TelegramNetworkError, который не наследует OSError. На коде с одним
    # `except OSError` этот тест краснеет: offline остаётся False, а человек без
    # интернета слышит «неверный токен» и идёт перевыпускать рабочий у @BotFather.
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "offline"))
    check = await setup.verify_token("123456:ABCdefGHIjklMNOpqrsTUVwxyz1234567890")
    assert check.offline is True
    assert check.ok is False


async def test_verify_token_rejects_bad_shape_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(token: str) -> _FakeBot:
        raise AssertionError("в сеть ходить не должны")

    monkeypatch.setattr(setup, "_make_bot", explode)
    check = await setup.verify_token("мусор")
    assert check.ok is False
    assert check.offline is False


async def test_verify_token_never_echoes_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "123456:SECRETVALUEabcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeBot(token, "bad"))
    check = await setup.verify_token(secret)
    assert "SECRETVALUE" not in check.detail


class _FakeSender:
    def __init__(
        self, user_id: int, full_name: str = "Test User", username: str | None = "testuser"
    ) -> None:
        self.id = user_id
        self.full_name = full_name
        self.username = username


class _FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = _FakeSender(user_id)


class _FakeUpdate:
    def __init__(self, update_id: int, message: _FakeMessage | None = None) -> None:
        self.update_id = update_id
        self.message = message


class _FakeUpdatesBot:
    """Заглушка для catch_user_id: отдаёт заранее заготовленные партии обновлений
    и запоминает offset, с которым её вызвали — второй поллер того же токена
    без сдвига offset вечно перечитывал бы одно и то же обновление.

    `offset=-1` (сброс бэклога перед ожиданием) отвечает пустым списком — этим
    тестам бэклог не важен, а без этой ветки самый первый вызов из
    `_first_fresh_offset` съедал бы первую партию как обычный опрос.
    """

    def __init__(self, batches: list[list[_FakeUpdate]], behaviour: str = "ok") -> None:
        self._batches = list(batches)
        self.behaviour = behaviour
        self.seen_offsets: list[int | None] = []

    async def get_updates(self, offset: int | None = None, timeout: int = 5) -> list[_FakeUpdate]:
        self.seen_offsets.append(offset)
        if self.behaviour == "offline":
            raise OSError("нет сети")
        if self.behaviour == "conflict":
            raise TelegramConflictError(
                method=GetUpdates(), message="terminated by other getUpdates"
            )
        if self.behaviour == "unauthorized":
            raise TelegramUnauthorizedError(method=GetUpdates(), message="Unauthorized")
        if offset == -1:
            return []
        if self._batches:
            return self._batches.pop(0)
        return []


class _FakeBacklogBot:
    """Очередь Telegram, как её видит getUpdates: `offset=-1` подглядывает
    последнее сообщение, не подтверждая его; обычный вызов возвращает всё
    начиная с offset (включая бэклог, если offset его не отсёк) — так же, как
    настоящий Telegram. Нужна для проверки самого сброса бэклога: `_FakeUpdatesBot`
    его нарочно не моделирует.
    """

    def __init__(self, backlog: list[_FakeUpdate], arriving: list[_FakeUpdate]) -> None:
        self._backlog = list(backlog)
        self._arriving = list(arriving)
        self.seen_offsets: list[int | None] = []

    async def get_updates(self, offset: int | None = None, timeout: int = 5) -> list[_FakeUpdate]:
        self.seen_offsets.append(offset)
        if offset == -1:
            return [self._backlog[-1]] if self._backlog else []
        due = [u for u in self._backlog if offset is None or u.update_id >= offset]
        self._backlog = [u for u in self._backlog if u not in due]
        if due:
            return due
        if self._arriving:
            return [self._arriving.pop(0)]
        return []


async def test_catch_user_id_returns_sender_of_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches = [[_FakeUpdate(1, _FakeMessage(777))]]
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot(batches))
    got = await setup.catch_user_id("123456:x", timeout_s=1.0)
    assert got is not None
    assert got.user_id == 777
    assert got.username == "testuser"


async def test_catch_user_id_ignores_updates_without_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Например edited_message: update есть, а message — нет, за ответ не считается.
    batches = [[_FakeUpdate(1)], [_FakeUpdate(2, _FakeMessage(42))]]
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot(batches))
    got = await setup.catch_user_id("123456:x", timeout_s=1.0)
    assert got is not None
    assert got.user_id == 42


async def test_catch_user_id_advances_offset_past_seen_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches = [[_FakeUpdate(9)], [_FakeUpdate(10, _FakeMessage(1))]]
    bot = _FakeUpdatesBot(batches)
    monkeypatch.setattr(setup, "_make_bot", lambda token: bot)
    await setup.catch_user_id("123456:x", timeout_s=1.0)
    # -1 — сброс бэклога перед ожиданием, идёт первым.
    assert bot.seen_offsets == [-1, None, 10]


async def test_catch_user_id_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot([]))
    got = await setup.catch_user_id("123456:x", timeout_s=0.02)
    assert got is None


async def test_catch_user_id_returns_none_when_network_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot([], "offline"))
    got = await setup.catch_user_id("123456:x", timeout_s=1.0)
    assert got is None


async def test_catch_user_id_raises_polling_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Кто-то ещё опрашивает getUpdates этим же токеном — снаружи выглядело бы
    # как истёкший таймаут, а причина другая (второй бот уже работает).
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot([], "conflict"))
    with pytest.raises(setup.PollingConflict):
        await setup.catch_user_id("123456:x", timeout_s=1.0)


async def test_catch_user_id_raises_token_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup, "_make_bot", lambda token: _FakeUpdatesBot([], "unauthorized"))
    with pytest.raises(setup.TokenRejected):
        await setup.catch_user_id("123456:x", timeout_s=1.0)


async def test_catch_user_id_skips_backlog_from_before_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Critical: Telegram хранит неподтверждённые обновления до 24 часов. Без
    # сброса бэклога первым «пойманным» может оказаться тот, кто писал боту
    # вчера, а не тот, кто сейчас сидит в визарде — allowed_user_id единственная
    # защита машины, ошибиться здесь значит отдать её чужому.
    stale = _FakeUpdate(5, _FakeMessage(999))  # посторонний, уже лежит в очереди
    fresh = _FakeUpdate(6, _FakeMessage(777))  # тот, кто пишет боту прямо сейчас
    bot = _FakeBacklogBot(backlog=[stale], arriving=[fresh])
    monkeypatch.setattr(setup, "_make_bot", lambda token: bot)

    caught = await setup.catch_user_id("123456:x", timeout_s=1.0)
    assert caught is not None
    assert caught.user_id == 777
