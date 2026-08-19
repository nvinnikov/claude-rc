# Настройка при первом запуске и публикация в Homebrew — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Человек, поставивший всё через Homebrew, заполняет свои данные визардом и получает работающего бота, ни разу не редактируя файлы руками.

**Architecture:** Новый модуль `clauderc/setup.py` — чистые функции сборки конфига плюс две изолированные сетевые. Подкоманда `claude-rc setup` собирает из них диалог. Приложение перестаёт крутить крэш-луп без конфига: спрашивает `doctor --json`, при отсутствии конфига показывает отдельное состояние и открывает Терминал с визардом. Дальше — настоящий релиз и tap.

**Tech Stack:** Python 3.12 (`argparse`, `tomllib`, aiogram для двух сетевых вызовов), Swift 6.2 + AppKit, Homebrew Formula/Cask, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-19-setup-and-tap-design.md`

## Global Constraints

- Ветка работы: `feat/setup-and-tap` (создана, на ней лежит спека).
- Новых внешних зависимостей не добавляем. `aiogram` уже есть.
- `mypy --strict` покрывает `clauderc` и `tests`. Аннотируем всё, включая тесты.
- `ruff`: `line-length = 100`, target `py312`. Каждая задача заканчивается зелёным `make check`.
- Swift-тесты гоняются так (голый `swift test` падает):
  ```bash
  FW="$(xcode-select -p)/Library/Developer/Frameworks"
  swift test --package-path app \
    -Xswiftc -F"$FW" -Xlinker -F"$FW" -Xlinker -rpath -Xlinker "$FW" \
    -Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays
  ```
  либо `make app-test`, который подставляет флаги сам.
- Комментарии, докстринги и сообщения коммитов — русские; идентификаторы, логи и подписи пунктов меню — английские.
- Комментарии только там, где решение неочевидно.
- Коммиты атомарные, Conventional Commits; в теле — **почему**, а не что.
- Никаких `git commit --amend` и `git rebase`.
- Тесты быстрые и **без сети**: сетевые функции подменяются заглушкой.
- **Токен бота не печатается целиком нигде** — ни в выводе, ни в логах, ни в сообщениях об ошибках.
- **Бота и приложение не запускать** без явного указания: на машине работает приложение с живым ботом внутри.

---

### Task 0: Спайк — сгенерирует ли Homebrew блоки `resource`

Результат — знание, от которого зависит объём задачи 5. Кода не коммитим.

**Files:** черновики в `/private/tmp/claude-501/-Users-nvinnikov-Documents-tg-claude/0856f588-9100-4a4f-8d02-150d4802d96c/scratchpad/spike-brew/`

**Interfaces:**
- Consumes: ничего
- Produces: ответ, работает ли `brew update-python-resources` для формулы, чей `url` ведёт на GitHub Release

- [ ] **Step 1: Собрать пробный tap локально**

```bash
SP=/private/tmp/claude-501/-Users-nvinnikov-Documents-tg-claude/0856f588-9100-4a4f-8d02-150d4802d96c/scratchpad/spike-brew
rm -rf "$SP" && mkdir -p "$SP/Formula"
cp /Users/nvinnikov/Documents/tg-claude/packaging/homebrew/claude-rc.rb "$SP/Formula/"
cd "$SP" && git init -q && git add -A && git commit -qm init
brew tap-new nvinnikov/spiketap --no-git 2>/dev/null || true
TAPDIR="$(brew --repository)/Library/Taps/nvinnikov/homebrew-spiketap"
echo "tap: $TAPDIR"; ls "$TAPDIR" 2>/dev/null
```

- [ ] **Step 2: Подставить рабочий url и попробовать сгенерировать ресурсы**

В формуле сейчас стоят заглушки `vVERSION` и `REPLACE_WITH_SHA256`. Для спайка нужен настоящий архив. Собери sdist локально и сошлись на него файловым url:

```bash
cd /Users/nvinnikov/Documents/tg-claude
uv build 2>&1 | tail -2
ls -la dist/*.tar.gz
SDIST="$(ls dist/*.tar.gz | head -1)"
SHA="$(shasum -a 256 "$SDIST" | cut -d' ' -f1)"
echo "sdist=$SDIST sha=$SHA"
```

Скопируй формулу в tap, заменив `url` на `file://$PWD/$SDIST` и `sha256` на посчитанный, затем:

```bash
TAPDIR="$(brew --repository)/Library/Taps/nvinnikov/homebrew-spiketap"
brew update-python-resources "$TAPDIR/Formula/claude-rc.rb" 2>&1 | tail -20
grep -c "^  resource" "$TAPDIR/Formula/claude-rc.rb"
```

Ожидание: команда завершилась и в формуле появились блоки `resource` (их должно быть больше нуля).

- [ ] **Step 3: Если не сработало — проверить запасной путь**

```bash
cd /Users/nvinnikov/Documents/tg-claude
uv export --no-dev --no-emit-project --format requirements-txt 2>/dev/null | grep -v '^#' | head -20
```

Ожидание: список зависимостей с версиями. Из него блоки `resource` собираются механически: для каждого пакета нужен url и sha256 sdist с PyPI (`https://pypi.org/pypi/<name>/<version>/json`, поле `urls[].filename` с `.tar.gz`).

- [ ] **Step 4: Прибрать**

```bash
brew untap nvinnikov/spiketap 2>/dev/null || true
rm -rf "/private/tmp/claude-501/-Users-nvinnikov-Documents-tg-claude/0856f588-9100-4a4f-8d02-150d4802d96c/scratchpad/spike-brew"
rm -rf /Users/nvinnikov/Documents/tg-claude/dist
brew tap | grep -c spiketap || echo "tap убран"
```

- [ ] **Step 5: Записать результат**

Ответь одной строкой: работает `brew update-python-resources` для нашей формулы или нет, и сколько блоков `resource` она сгенерировала. Если нет — сколько зависимостей в списке `uv export`, то есть сколько блоков придётся собрать вручную.

---

### Task 1: `clauderc/setup.py` — чистые функции

**Files:**
- Create: `clauderc/setup.py`
- Create: `tests/test_setup.py`

**Interfaces:**
- Consumes: `clauderc.config.load_config` (существует), `clauderc.paths.config_file` (существует)
- Produces:
  - `setup.Answers` — frozen dataclass с полями `bot_token: str`, `allowed_user_id: int`, `rc_roots: tuple[Path, ...]`
  - `setup.render_config(answers: Answers) -> str`
  - `setup.parse_roots(raw: str, *, default: tuple[Path, ...]) -> tuple[Path, ...]`
  - `setup.mask_token(token: str) -> str`
  - `setup.looks_like_token(value: str) -> bool`
  - `setup.default_roots() -> tuple[Path, ...]`
  - `setup.RootsError` — исключение с внятным сообщением о несуществующем каталоге

- [ ] **Step 1: Написать падающие тесты**

Создай `tests/test_setup.py`:

```python
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


def test_default_roots_prefers_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Documents").mkdir()
    assert setup.default_roots() == (tmp_path / "Documents",)


def test_default_roots_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert setup.default_roots() == (tmp_path,)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clauderc.setup'`

- [ ] **Step 3: Написать модуль**

Создай `clauderc/setup.py`:

```python
"""Сборка config.toml для визарда первого запуска.

Всё, что можно проверить тестом, живёт здесь чистыми функциями: сам диалог в
`cli.py` только спрашивает и печатает.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_VISIBLE_TAIL = 4


class RootsError(ValueError):
    """Ответ про каталоги нельзя принять."""


@dataclass(frozen=True)
class Answers:
    bot_token: str
    allowed_user_id: int
    rc_roots: tuple[Path, ...]


def render_config(answers: Answers) -> str:
    """Текст config.toml. Значения экранируются как строки TOML.

    В токене есть двоеточие, а в путях может быть что угодно — склейка через
    кавычки рано или поздно даст файл, который не читается.
    """
    roots = ", ".join(_toml_string(str(root)) for root in answers.rc_roots)
    return (
        "# Создан `claude-rc setup`. Правь руками, если нужно.\n\n"
        "# Токен бота от @BotFather.\n"
        f"bot_token = {_toml_string(answers.bot_token)}\n\n"
        "# Твой Telegram user_id. Только он может управлять ботом.\n"
        f"allowed_user_id = {answers.allowed_user_id}\n\n"
        "# Где искать репозитории. Сами корни тоже валидные цели.\n"
        f"rc_roots = [{roots}]\n"
    )


def parse_roots(raw: str, *, default: tuple[Path, ...]) -> tuple[Path, ...]:
    """Разбирает ответ про каталоги: несколько путей через запятую."""
    items = [chunk.strip() for chunk in raw.split(",")]
    items = [chunk for chunk in items if chunk]
    if not items:
        if default:
            return default
        raise RootsError("нужен хотя бы один каталог")

    roots = tuple(Path(item).expanduser() for item in items)
    missing = [str(root) for root in roots if not root.is_dir()]
    if missing:
        raise RootsError(f"каталог не найден: {', '.join(missing)}")
    return roots


def mask_token(token: str) -> str:
    """Скрывает середину токена: узнать свой можно, скопировать чужой — нет."""
    if not token:
        return ""
    head, _, tail = token.partition(":")
    if not tail:
        return "…"
    return f"{head}:…{tail[-_VISIBLE_TAIL:]}" if len(tail) > _VISIBLE_TAIL else f"{head}:…"


def looks_like_token(value: str) -> bool:
    """Грубая проверка формы. Настоящая проверка — вызовом getMe."""
    return bool(_TOKEN.match(value.strip()))


def default_roots() -> tuple[Path, ...]:
    """Что предложить по умолчанию.

    Домашний каталог целиком — плохой ответ: обход на глубину 3 по нему долгий
    и мусорный. Поэтому сначала пробуем ~/Documents.
    """
    documents = Path.home() / "Documents"
    return (documents,) if documents.is_dir() else (Path.home(),)


def _toml_string(value: str) -> str:
    """Строковый литерал TOML. Пользуемся тем, что tomllib умеет читать обратно."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    literal = f'"{escaped}"'
    # Дешёвая страховка от собственной ошибки экранирования: если получившийся
    # литерал не читается обратно, лучше упасть здесь, чем отдать битый конфиг.
    if tomllib.loads(f"x = {literal}")["x"] != value:
        raise ValueError(f"не удалось закодировать значение для TOML: {value!r}")
    return literal
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_setup.py -v`
Expected: PASS, 14 тестов

- [ ] **Step 5: Прогнать гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 6: Коммит**

```bash
git add clauderc/setup.py tests/test_setup.py
git commit -m "$(cat <<'MSG'
feat: сборка config.toml для визарда первого запуска

Круговой тест держит главное: то, что визард пишет, читается боевым загрузчиком.
Расхождение иначе всплыло бы только при первом запуске бота, уже после настройки.
MSG
)"
```

---

### Task 2: Сетевые функции визарда

**Files:**
- Modify: `clauderc/setup.py`
- Modify: `tests/test_setup.py`

**Interfaces:**
- Consumes: `Answers`, `looks_like_token` (Task 1)
- Produces:
  - `setup.verify_token(token: str) -> TokenCheck` (корутина)
  - `setup.TokenCheck` — frozen dataclass с полями `ok: bool`, `bot_name: str | None`, `offline: bool`, `detail: str`
  - `setup.catch_user_id(token: str, *, timeout_s: float = 120.0) -> int | None` (корутина)

- [ ] **Step 1: Написать падающие тесты**

Допиши в `tests/test_setup.py`:

```python
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
            raise OSError("нет сети")
        if self.behaviour == "bad":
            raise RuntimeError("Unauthorized")
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_setup.py -k verify -v`
Expected: FAIL — `AttributeError: module 'clauderc.setup' has no attribute 'verify_token'`

- [ ] **Step 3: Дописать модуль**

Добавь в `clauderc/setup.py`:

```python
@dataclass(frozen=True)
class TokenCheck:
    ok: bool
    bot_name: str | None
    offline: bool
    detail: str


def _make_bot(token: str) -> Any:
    """Обёртка ради тестируемости: заглушка подменяет именно её."""
    from aiogram import Bot

    return Bot(token=token)


async def verify_token(token: str) -> TokenCheck:
    """Спрашивает Telegram, живой ли токен.

    Опечатка в токене иначе превращается в крэш-луп бота, который разбирают
    полчаса. Отсутствие сети и отказ Telegram различаются: в первом случае
    настройку можно продолжать, во втором — бессмысленно.
    """
    if not looks_like_token(token):
        return TokenCheck(False, None, False, "не похоже на токен от @BotFather")

    bot = _make_bot(token)
    try:
        me = await bot.get_me()
    except OSError as exc:
        return TokenCheck(False, None, True, f"нет связи с Telegram: {exc}")
    except Exception:
        # Текст исключения от aiogram может содержать сам токен (он в URL) —
        # наружу отдаём только факт отказа.
        return TokenCheck(False, None, False, "Telegram отверг токен")
    finally:
        await _close(bot)

    return TokenCheck(True, getattr(me, "username", None), False, "токен принят")


async def catch_user_id(token: str, *, timeout_s: float = 120.0) -> int | None:
    """Ждёт первое сообщение боту и возвращает id отправителя.

    Иначе человеку пришлось бы искать @userinfobot и копировать число — самый
    ошибкоёмкий шаг настройки.

    Зовётся только пока бот не настроен, то есть заведомо не запущен: второй
    поллер того же токена ломает работающего бота.
    """
    from aiogram import Bot

    bot = Bot(token=token)
    deadline = asyncio.get_running_loop().time() + timeout_s
    offset: int | None = None
    try:
        while asyncio.get_running_loop().time() < deadline:
            updates = await bot.get_updates(offset=offset, timeout=5)
            for update in updates:
                offset = update.update_id + 1
                message = getattr(update, "message", None)
                sender = getattr(message, "from_user", None) if message else None
                if sender is not None:
                    return int(sender.id)
    except Exception:
        return None
    finally:
        await _close(bot)
    return None


async def _close(bot: Any) -> None:
    session = getattr(bot, "session", None)
    if session is not None:
        await session.close()
```

Не забудь про импорты в шапке файла: `asyncio` и `from typing import Any`.

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_setup.py -v`
Expected: PASS, 18 тестов

- [ ] **Step 5: Прогнать гейт**

Run: `make check`

- [ ] **Step 6: Коммит**

```bash
git add clauderc/setup.py tests/test_setup.py
git commit -m "$(cat <<'MSG'
feat: живая проверка токена и подхват user_id из первого сообщения

Текст исключения aiogram может содержать сам токен — он в URL запроса, — поэтому
наружу отдаётся только факт отказа. Различие «нет сети» и «токен отвергнут» вынесено
в отдельное поле: в первом случае настройку можно продолжать.
MSG
)"
```

---

### Task 3: Подкоманда `claude-rc setup`

**Files:**
- Modify: `clauderc/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: всё из `clauderc/setup.py` (Tasks 1-2), `paths.config_file` (существует)
- Produces: подкоманда `setup`, поведение по контракту кодов возврата `0/1/2`

- [ ] **Step 1: Написать падающие тесты**

Допиши в `tests/test_cli.py`:

```python
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
    answers = iter([token, "42", str(root)])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert target.is_file()
    assert oct(target.stat().st_mode & 0o777) == "0o600"

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
    answers = iter([secret, "42", str(root)])
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
    # Каталог не существует, и человек повторяет ответ — визард переспрашивает,
    # а не пишет заведомо нерабочий конфиг.
    answers = iter([token, "42", str(tmp_path / "nope"), str(tmp_path / "nope")])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 2
    assert not target.exists()
```

Тест `test_setup_rejects_missing_directory` требует, чтобы визард переспрашивал
ограниченное число раз (возьми 2 попытки) и затем сдавался с кодом 2.

Класс `_FakeStdin` уже есть в `tests/test_cli.py:20` — он появился вместе с тестами
на диалог доверия и подменяет только `isatty()`. Переиспользуй его, второй не заводи.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_cli.py -k setup -v`
Expected: FAIL — подкоманды `setup` нет, `main` возвращает 2 из-за неизвестного аргумента или падает

- [ ] **Step 3: Добавить подкоманду**

В `clauderc/cli.py`:

в шапку — `from clauderc import paths, setup, worktrees`;

в `_parser()` рядом с остальными:

```python
    sub.add_parser("setup", help="заполнить config.toml: токен, user_id, каталоги")
```

в класс `_Commands`:

```python
    @staticmethod
    def setup(args: argparse.Namespace) -> int:
        if not sys.stdin.isatty():
            print(
                "setup — интерактивная команда, а stdin не терминал.\n"
                "Запусти её в терминале: claude-rc setup",
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        return asyncio.run(_run_setup(paths.config_file()))
```

и сам диалог рядом с другими помощниками:

```python
# Две попытки на каждый вопрос: одна на опечатку, вторая на исправление.
_ATTEMPTS = 2


async def _run_setup(target: Path) -> int:
    """Спрашивает три значения и пишет config.toml."""
    current = _current_answers(target)

    token = await _ask_token(current.bot_token if current else None)
    if token is None:
        return EXIT_FAILED

    user_id = _ask_user_id(current.allowed_user_id if current else None)
    if user_id is None:
        return EXIT_ENVIRONMENT

    try:
        roots = _ask_roots(current.rc_roots if current else None)
    except setup.RootsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT

    answers = setup.Answers(bot_token=token, allowed_user_id=user_id, rc_roots=roots)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(setup.render_config(answers))
        # 0600: в файле боевой токен бота.
        target.chmod(0o600)
    except OSError as exc:
        print(f"не удалось записать {target}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"\nГотово: {target}")
    print("Запусти приложение ClaudeRC или `claude-rc bot`.")
    return 0


def _current_answers(target: Path) -> setup.Answers | None:
    """Что уже настроено. Битый конфиг — то же, что его отсутствие."""
    try:
        config = load_config(target)
    except (OSError, ValueError, KeyError):
        return None
    return setup.Answers(
        bot_token=config.bot_token,
        allowed_user_id=config.allowed_user_id,
        rc_roots=config.rc_roots,
    )


async def _ask_token(current: str | None) -> str | None:
    hint = f" [{setup.mask_token(current)}]" if current else ""
    for _ in range(_ATTEMPTS):
        raw = input(f"Токен бота от @BotFather{hint}: ").strip()
        token = raw or (current or "")
        if not token:
            print("Токен нужен: заведи бота у @BotFather и вставь сюда.")
            continue

        check = await setup.verify_token(token)
        if check.ok:
            print(f"  ✓ бот @{check.bot_name}" if check.bot_name else "  ✓ токен принят")
            return token
        if check.offline:
            print(f"  ! {check.detail} — продолжаю, проверить не смог.")
            return token
        print(f"  ✗ {check.detail}")
    print("Токен так и не принят.", file=sys.stderr)
    return None


def _ask_user_id(current: int | None) -> int | None:
    hint = f" [{current}]" if current else ""
    for _ in range(_ATTEMPTS):
        raw = input(f"Твой Telegram user_id{hint}: ").strip()
        if not raw and current:
            return current
        try:
            return int(raw)
        except ValueError:
            print("  ✗ нужно число. Узнать своё: напиши @userinfobot.")
    print("user_id не получен.", file=sys.stderr)
    return None


def _ask_roots(current: tuple[Path, ...] | None) -> tuple[Path, ...]:
    default = current or setup.default_roots()
    shown = ", ".join(str(item) for item in default)
    last: setup.RootsError | None = None
    for _ in range(_ATTEMPTS):
        raw = input(f"Каталоги с репозиториями, через запятую [{shown}]: ")
        try:
            return setup.parse_roots(raw, default=default)
        except setup.RootsError as exc:
            last = exc
            print(f"  ✗ {exc}")
    raise last if last else setup.RootsError("каталоги не получены")
```

Подхват `user_id` из первого сообщения в этой задаче не подключаем — он появится
отдельным шагом ниже, чтобы задача осталась проверяемой без сети.

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, все прежние плюс пять новых

- [ ] **Step 5: Подключить подхват user_id**

В `_ask_user_id` добавь ветку: если `current` нет, предложи поймать id автоматически.

Замени `_ask_user_id` целиком на корутину:

```python
async def _ask_user_id(current: int | None, token: str) -> int | None:
    if current is None and _agrees("Узнать твой user_id автоматически? Напишешь боту любое сообщение."):
        print("  Жду сообщение боту… до 2 минут, Ctrl+C — ввести вручную.")
        try:
            caught = await setup.catch_user_id(token)
        except KeyboardInterrupt:
            caught = None
        if caught is not None:
            print(f"  ✓ user_id: {caught}")
            return caught
        print("  Не дождался — введи вручную.")

    hint = f" [{current}]" if current else ""
    for _ in range(_ATTEMPTS):
        raw = input(f"Твой Telegram user_id{hint}: ").strip()
        if not raw and current:
            return current
        try:
            return int(raw)
        except ValueError:
            print("  ✗ нужно число. Узнать своё: напиши @userinfobot.")
    print("user_id не получен.", file=sys.stderr)
    return None


def _agrees(question: str) -> bool:
    return input(f"{question} [Y/n]: ").strip().lower() in {"", "y", "yes", "д", "да"}
```

В `_run_setup` вызов становится `await _ask_user_id(current.allowed_user_id if current else None, token)`.

Обрати внимание на порядок: ветка автоподхвата выполняется **только** при
`current is None`. Это не косметика — поллинг при уже настроенном боте поднял бы
второго поллера того же токена рядом с работающим.

Допиши тест ровно на это:

```python
async def test_setup_does_not_poll_when_user_id_already_known(
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
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 7
```

И тест на то, что при пустом конфиге отказ от автоподхвата ведёт к ручному вводу:

```python
async def test_setup_falls_back_to_manual_user_id(
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
    # токен, «n» на автоподхват, user_id, каталоги
    answers = iter([token, "n", "42", str(root)])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_verify(value: str) -> cli.setup.TokenCheck:
        return cli.setup.TokenCheck(True, "my_test_bot", False, "токен принят")

    monkeypatch.setattr(cli.setup, "verify_token", fake_verify)

    assert cli.main(["setup"]) == 0
    assert cli.load_config(target).allowed_user_id == 42
```

Прежние тесты из шага 1 отвечают на все вопросы заранее заготовленным списком —
после добавления вопроса про автоподхват их списки ответов надо дополнить. Там, где
конфиг уже существует, вопрос не задаётся и список не меняется.

- [ ] **Step 6: Прогнать гейт**

Run: `make check`

- [ ] **Step 7: Описать в README**

Секция про первый запуск: `claude-rc setup`, что он спрашивает, и что после него надо
открыть приложение. Скажи, что конфиг пишется в `~/.config/claude-rc/config.toml` с
правами `600`, и что повторный запуск позволяет поменять отдельные значения.

- [ ] **Step 8: Коммит**

```bash
git add clauderc/cli.py tests/test_cli.py README.md
git commit -m "$(cat <<'MSG'
feat: команда claude-rc setup

Установка через Homebrew заканчивалась тупиком: конфига нет, бот падает, а
Reveal config открывает пустой каталог. Визард спрашивает три значения и пишет
файл с правами 600 — токен в нём боевой.
MSG
)"
```

---

### Task 4: Приложение перестаёт крутить крэш-луп без конфига

**Files:**
- Modify: `app/Sources/ClaudeRCMenu/BotSupervisor.swift`
- Modify: `app/Sources/ClaudeRCMenu/AppDelegate.swift`
- Modify: `app/Sources/ClaudeRCMenu/Doctor.swift`
- Create: `app/Tests/ClaudeRCMenuTests/ConfiguredTests.swift`

**Interfaces:**
- Consumes: `Doctor.parse`, `Doctor.Check` (существуют), `BotState` (существует)
- Produces: `BotState.notConfigured`, `Doctor.isConfigured(in:) -> Bool`

- [ ] **Step 1: Написать падающие тесты**

Создай `app/Tests/ClaudeRCMenuTests/ConfiguredTests.swift`:

```swift
import Foundation
import Testing

@testable import ClaudeRCMenu

@Test func configuredWhenConfigCheckPassed() {
    let json = """
    {"checks": [{"name": "config", "ok": true, "detail": "/Users/x/.config/claude-rc/config.toml"}]}
    """
    #expect(Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenConfigCheckFailed() {
    let json = """
    {"checks": [{"name": "config", "ok": false, "detail": "нет файла /Users/x/.config/claude-rc/config.toml"}]}
    """
    #expect(!Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenTokenIsEmpty() {
    // Файл есть, но токен пуст — бот всё равно не поднимется.
    let json = """
    {"checks": [
      {"name": "config", "ok": true, "detail": "/x/config.toml"},
      {"name": "bot_token", "ok": false, "detail": "пуст"}
    ]}
    """
    #expect(!Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}

@Test func notConfiguredWhenDoctorSaidNothing() {
    // Пустой ответ — не повод считать, что всё хорошо.
    #expect(!Doctor.isConfigured(in: []))
}

@Test func unrelatedFailedChecksDoNotBlockStart() {
    // tmux или claude не найдены — это другая беда, и про неё скажет сам бот.
    let json = """
    {"checks": [
      {"name": "config", "ok": true, "detail": "/x/config.toml"},
      {"name": "bot_token", "ok": true, "detail": "задан"},
      {"name": "tmux", "ok": false, "detail": "не найден в PATH"}
    ]}
    """
    #expect(Doctor.isConfigured(in: Doctor.parse(Data(json.utf8))))
}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `make app-test`
Expected: ошибка компиляции — `Doctor.isConfigured` не найден

- [ ] **Step 3: Добавить `isConfigured`**

В `app/Sources/ClaudeRCMenu/Doctor.swift`:

```swift
    /// Хватает ли конфига, чтобы бот вообще поднялся.
    ///
    /// Смотрим только на проверки про конфиг: отсутствие `tmux` — другая беда,
    /// и про неё бот скажет сам, а вот без токена он просто упадёт.
    static func isConfigured(in checks: [Check]) -> Bool {
        let blocking = ["config", "bot_token", "allowed_user_id"]
        guard checks.contains(where: { $0.name == "config" }) else { return false }
        return !checks.contains { blocking.contains($0.name) && !$0.ok }
    }
```

- [ ] **Step 4: Добавить состояние и поведение**

В `BotSupervisor.swift` расширь `BotState`:

```swift
    /// Конфига нет или он неполон. Отдельный случай, а не `crashed`: крэш-луп
    /// из трёх попыток не сообщает причину и выглядит как поломка.
    case notConfigured
```

В `BotSupervisor` добавь метод, спрашивающий тулзу:

```swift
    /// Спрашивает `claude-rc doctor --json`. Таймаут — как у остальных внешних
    /// вызовов: подвешенная тулза не должна морозить меню-бар.
    func isConfigured() -> Bool {
        let task = Process()
        task.executableURL = cli
        task.arguments = ["doctor", "--json"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        task.environment = CLILocator.childEnvironment(base: ProcessInfo.processInfo.environment)

        let exited = DispatchSemaphore(value: 0)
        task.terminationHandler = { _ in exited.signal() }
        guard (try? task.run()) != nil else { return false }
        guard exited.wait(timeout: .now() + 5) == .success else {
            task.terminate()
            Log.app("isConfigured: doctor не ответил за 5с")
            return false
        }
        return Doctor.isConfigured(in: Doctor.parse(pipe.fileHandleForReading.readDataToEndOfFile()))
    }
```

В `start()` перед всем остальным:

```swift
        guard isConfigured() else {
            Log.app("start: конфига нет, бота не поднимаем")
            state = .notConfigured
            return
        }
```

- [ ] **Step 5: Показать это в меню**

В `AppDelegate.swift` добавь пункт рядом с остальными:

```swift
    private let setupRow = NSMenuItem(title: "Run setup…", action: nil, keyEquivalent: "")
```

зарегистрируй его в `buildMenu()` сразу после `toggleRow`, с `action: #selector(runSetup)`
и `target: self`, а в `render(_:)` добавь ветку:

```swift
        case .notConfigured:
            statusRow.title = "Bot: not configured"
            toggleRow.title = "Start bot"
            toggleRow.isEnabled = false
```

и в `menuNeedsUpdate` — видимость пункта:

```swift
        setupRow.isHidden = !isNotConfigured(supervisor?.state)
```

Помощник — чистой функцией на модульном уровне, чтобы её покрыл тест:

```swift
func isNotConfigured(_ state: BotState?) -> Bool {
    if case .notConfigured = state { return true }
    return false
}
```

Сам обработчик открывает Терминал с визардом:

```swift
    @objc private func runSetup() {
        guard let cli else { return }
        // Открываем Терминал, а не запускаем визард внутри: он интерактивный,
        // а у приложения нет ни stdin, ни места, где показать вопросы.
        let script = "clear; \(cli.path) setup"
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        let temp = FileManager.default.temporaryDirectory
            .appendingPathComponent("claude-rc-setup.command")
        try? "#!/bin/sh\n\(script)\n".write(to: temp, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: temp.path)
        NSWorkspace.shared.open(
            [temp], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration()
        )
    }
```

Допиши тест на помощника в `ConfiguredTests.swift`:

```swift
@Test func setupRowShowsOnlyWhenNotConfigured() {
    #expect(isNotConfigured(.notConfigured))
    #expect(!isNotConfigured(.stopped))
    #expect(!isNotConfigured(.running(since: Date(timeIntervalSince1970: 0))))
    #expect(!isNotConfigured(nil))
}
```

- [ ] **Step 6: Проверить сборку и тесты**

```bash
swift build --package-path app
make app-test
```
Expected: сборка чистая, все прежние тесты плюс шесть новых

- [ ] **Step 7: Коммит**

```bash
git add app/Sources app/Tests
git commit -m "$(cat <<'MSG'
feat: приложение не крутит крэш-луп без конфига

Три перезапуска не сообщали причину и выглядели как поломка: это первое, что видел
человек после установки через Homebrew. Теперь бот в таком состоянии не поднимается
вовсе, а меню предлагает открыть визард.
MSG
)"
```

---

### Task 5: Первый релиз и tap

**Files:**
- Modify: `pyproject.toml` (версия)
- Modify: `packaging/homebrew/claude-rc.rb`, `packaging/homebrew/claude-rc-app.rb`
- Modify: `packaging/homebrew/README.md`, `README.md`

**Interfaces:**
- Consumes: `release.yml` из части 3, результат спайка задачи 0
- Produces: релиз `v0.2.0` и репозиторий `nvinnikov/homebrew-tap`

- [ ] **Step 1: Поднять версию**

В `pyproject.toml`: `version = "0.2.0"`. Прогони `make check` и `make app`, убедись,
что `Info.plist` собранного бандла показывает `0.2.0`:

```bash
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" app/build/ClaudeRC.app/Contents/Info.plist
```

- [ ] **Step 2: Коммит и слияние**

Эта задача выполняется **после** того, как остальные слиты в master: тег должен
указывать на код, который уже прошёл ревью.

```bash
git add pyproject.toml
git commit -m "chore: версия 0.2.0

Первый релиз, в котором установка через Homebrew имеет смысл: появился визард
настройки, без которого свежая установка заканчивалась тупиком."
```

- [ ] **Step 3: Поставить тег и дождаться релиза**

```bash
git checkout master && git pull --ff-only
git tag v0.2.0 && git push origin v0.2.0
gh run list --workflow=release.yml --limit 1
```

Дождись завершения. Проверь, что в релизе три артефакта: `ClaudeRC.app.zip`,
sdist `.tar.gz` и wheel `.whl`.

- [ ] **Step 4: Забрать контрольные суммы**

```bash
gh release download v0.2.0 --dir /tmp/rel-v0.2.0
shasum -a 256 /tmp/rel-v0.2.0/*
```

- [ ] **Step 5: Собрать блоки `resource`**

Спайк задачи 0 показал: `brew update-python-resources` работает и для формулы, чей
`url` ведёт на GitHub Release — он резолвит зависимости по метаданным пакета, а не по
адресу формулы. Сгенерировалось 18 блоков, столько же пакетов даёт `uv export`.

Порядок такой (первый шаг обязателен на brew 6.x, иначе «Refusing to load formula
from untrusted tap»):

```bash
TAP="$(brew --repository)/Library/Taps/nvinnikov/homebrew-tap"
brew trust --formula "$TAP/Formula/claude-rc.rb"
brew update-python-resources "$TAP/Formula/claude-rc.rb"
grep -c "^  resource" "$TAP/Formula/claude-rc.rb"
```

Ожидание: 18 блоков. Если команда почему-то откажет, запасной путь — собрать блоки
из вывода `uv export`, беря для каждого пакета sdist с PyPI:

```bash
cd /Users/nvinnikov/Documents/tg-claude
uv export --no-dev --no-emit-project --format requirements-txt | grep -v '^#' | grep -v '^-e'
```

для каждой строки `name==version`:

```bash
curl -s "https://pypi.org/pypi/<name>/<version>/json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); u=[x for x in d['urls'] if x['packagetype']=='sdist'][0]; print(u['url']); print(u['digests']['sha256'])"
```

- [ ] **Step 6: Создать tap и залить файлы**

```bash
gh repo create nvinnikov/homebrew-tap --public \
  --description "Homebrew tap: claude-rc" --clone
cd homebrew-tap
mkdir -p Formula Casks
cp /Users/nvinnikov/Documents/tg-claude/packaging/homebrew/claude-rc.rb Formula/
cp /Users/nvinnikov/Documents/tg-claude/packaging/homebrew/claude-rc-app.rb Casks/
```

Подставь в оба файла настоящие `url`, `sha256` и версию вместо заглушек
`vVERSION` / `VERSION` / `REPLACE_WITH_SHA256`, добавь блоки `resource` в формулу,
затем:

```bash
git add -A && git commit -m "claude-rc 0.2.0" && git push
```

- [ ] **Step 7: Проверить установку на деле**

```bash
brew untap nvinnikov/tap 2>/dev/null || true
brew tap nvinnikov/tap
brew info nvinnikov/tap/claude-rc
brew install nvinnikov/tap/claude-rc
claude-rc version
```

Ожидание: `0.2.0`. Установку cask'а проверяй отдельно и только после того, как
приложение будет погашено — иначе он попытается заменить работающий бандл.

- [ ] **Step 8: Обновить документацию**

В `README.md` замени пометку «tap ещё надо создать» на настоящую команду установки.
В `packaging/homebrew/README.md` отметь, что файлы в этом каталоге — источник, а
живут они в `nvinnikov/homebrew-tap`, и что при новом релизе надо обновить версию,
`sha256` и, если менялись зависимости, блоки `resource`.

- [ ] **Step 9: Коммит**

```bash
git add README.md packaging/homebrew/README.md packaging/homebrew/*.rb
git commit -m "docs: настоящий адрес tap вместо заглушки"
```

---

## Проверка целиком

- [ ] **Гейты**

```bash
make check
make app-test
```

- [ ] **Сквозной сценарий с чистого листа**

```bash
mv ~/.config/claude-rc/config.toml ~/.config/claude-rc/config.toml.bak
cd /tmp && claude-rc doctor; echo "код=$?"
```

Ожидание: `doctor` говорит, что конфига нет, код 2. Затем верни файл:

```bash
mv ~/.config/claude-rc/config.toml.bak ~/.config/claude-rc/config.toml
```

Визард с настоящим токеном руками не прогоняй — на машине работает бот, и
подхват `user_id` поднял бы второй поллер.

- [ ] **Открыть PR**

```bash
git push -u origin feat/setup-and-tap
gh pr create --base master --title "feat: визард настройки и публикация в Homebrew"
```
