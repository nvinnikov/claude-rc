# CLI-тулза и резюм сессий — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать `claude-rc` устанавливаемой тулзой с CLI и научить бота поднимать упавшие сессии, продолжая прежний диалог Claude.

**Architecture:** Ядро (`remote`, `repos`, `worktrees`, `browse`) остаётся транспортно-независимым; к нему добавляются четыре новых модуля — `paths` (где лежат файлы), `history` (диалоги из `~/.claude/projects`), `watch` (опрос tmux на предмет исчезнувших сессий) и `cli` (argparse-фронтенд). `bot.py` становится вторым потребителем тех же модулей, а не единственным.

**Tech Stack:** Python 3.12, стандартная библиотека (`argparse`, `json`, `tomllib`, `asyncio`), aiogram 3 (только в `bot.py`), pytest + pytest-asyncio, mypy `--strict`, ruff.

**Spec:** `docs/superpowers/specs/2026-08-18-cli-and-resume-design.md`

## Global Constraints

- Ветка работы: `feat/cli-and-resume` (уже создана, на ней лежит спека).
- Новых внешних зависимостей не добавляем. CLI — только `argparse` из стандартной библиотеки.
- `mypy --strict` покрывает `clauderc` и `tests`. Аннотируем всё, включая тесты. `Any` не протаскиваем.
- `ruff`: `line-length = 100`, target `py312`. Каждая задача заканчивается зелёным `make check`.
- Комментарии, докстринги и сообщения коммитов — русские; идентификаторы, логи и подписи кнопок — английские.
- Комментарии только там, где решение неочевидно. Очевидное не комментируем.
- Коммиты атомарные, в стиле Conventional Commits; в теле — **почему**, а не что.
- Пользовательский ввод, попадающий в командную строку, экранируется через `shlex.quote`.
- `callback_data` в Telegram — не длиннее 64 байт. Пути и id туда не кладём: только короткий токен из словаря.
- Тесты быстрые и без сети.

---

### Task 0: Спайк — работает ли `--remote-control` вместе с `--resume`

Это единственная задача без TDD: её результат — знание, а не код. От него зависят задачи 3 и 5.

**Files:** ничего не коммитим, кроме отчёта в конце.

**Interfaces:**
- Consumes: ничего
- Produces: ответ «работает / не работает», который меняет объём задач 3 и 5

- [ ] **Step 1: Найти каталог с историей диалогов**

```bash
ls -t ~/.claude/projects/-Users-$(whoami)-Documents-tg-claude/*.jsonl | head -3
```

Ожидание: несколько `<uuid>.jsonl`. Возьми id (имя файла без расширения) второго по свежести — самый свежий может принадлежать текущей сессии.

- [ ] **Step 2: Поднять сессию с резюмом в tmux**

```bash
ID=<подставь uuid из шага 1>
tmux new-session -d -s spike-resume -x 120 -y 40 -c ~/Documents/tg-claude \
  "exec claude --remote-control spike --resume $ID"
sleep 20
tmux capture-pane -p -J -t '=spike-resume:' | tail -30
```

Ожидание одного из трёх:
- в панели есть `https://claude.ai/code/session_…` и видна прежняя переписка → **работает**;
- есть ссылка, но диалог пустой → флаг проглочен, резюма нет;
- ошибка разбора аргументов или сессия завершилась → **не работает**.

- [ ] **Step 3: Проверить `--continue`**

```bash
tmux kill-session -t '=spike-resume' 2>/dev/null
tmux new-session -d -s spike-continue -x 120 -y 40 -c ~/Documents/tg-claude \
  "exec claude --remote-control spike --continue"
sleep 20
tmux capture-pane -p -J -t '=spike-continue:' | tail -30
```

- [ ] **Step 4: Прибрать за собой**

```bash
tmux kill-session -t '=spike-continue' 2>/dev/null
tmux kill-session -t '=spike-resume' 2>/dev/null
tmux ls
```

- [ ] **Step 5: Записать результат и решить, меняется ли план**

Если оба флага работают — план идёт как написан.

Если **не работают**, останови выполнение и сообщи об этом человеку. Запасной вариант из спеки: `remote.launch` начинает генерировать `uuid4` и передавать его в `--session-id`, сохраняя в tmux-опцию `@rc_session_id`; резюм становится доступен только для сессий, поднятых через нас, а пункт `Pick…` из задачи 5 исчезает. Это меняет задачи 3 и 5 настолько, что переписывать план должен человек, а не исполнитель.

---

### Task 1: `paths.py` — где живут файлы установленной тулзы

**Files:**
- Create: `clauderc/paths.py`
- Create: `tests/test_paths.py`
- Modify: `clauderc/bot.py` (строки 261-262 — резолв конфига)
- Modify: `README.md` (секция про установку и `config.toml`)

**Interfaces:**
- Consumes: ничего
- Produces:
  - `paths.config_file() -> Path`
  - `paths.log_file() -> Path`
  - `paths.claude_projects() -> Path`

- [ ] **Step 1: Написать падающие тесты**

Создай `tests/test_paths.py`:

```python
from pathlib import Path

import pytest
from clauderc import paths


def test_env_var_wins_even_if_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clauderc.paths'`

- [ ] **Step 3: Написать модуль**

Создай `clauderc/paths.py`:

```python
"""Где лежат файлы установленной тулзы.

Единственное место, которое знает расположение конфига, лога и хранилища
диалогов Claude Code. Пока конфиг искали рядом с исходниками, пакет нельзя
было поставить: `uv tool install` кладёт код в чужой каталог, где `config.toml`
взяться неоткуда.
"""

from __future__ import annotations

import os
from pathlib import Path


def config_file() -> Path:
    """Первый существующий конфиг из цепочки; если ни одного — XDG-путь.

    Возврат несуществующего XDG-пути намеренный: сообщение об ошибке должно
    называть место, куда конфиг положить, а не то, где его случайно искали.
    """
    env = os.environ.get("CLAUDE_RC_CONFIG")
    if env:
        return Path(env).expanduser()

    candidates = [
        Path.home() / ".config/claude-rc/config.toml",
        Path.cwd() / "config.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def log_file() -> Path:
    return Path.home() / ".claude-rc/claude-rc.log"


def claude_projects() -> Path:
    """Хранилище диалогов Claude Code. CLAUDE_CONFIG_DIR уважаем: он есть у claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(env).expanduser() if env else Path.home() / ".claude"
    return base / "projects"
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS, 8 тестов

- [ ] **Step 5: Переключить бота на `paths.config_file()`**

В `clauderc/bot.py` в функции `main()` замени

```python
    root = Path(__file__).resolve().parent.parent
    config = load_config(root / "config.toml")
```

на

```python
    config = load_config(paths.config_file())
```

Добавь `paths` в импорт: строка `from clauderc import browse, worktrees` становится
`from clauderc import browse, paths, worktrees`.

`Path` из `bot.py` не убирай: он остаётся в сигнатурах `start_session` и словарей.
Если ruff сообщит о неиспользуемом импорте — это про что-то другое, проверь по выводу.

- [ ] **Step 6: Обновить README**

В секции про установку опиши, что конфиг ищется в трёх местах — `$CLAUDE_RC_CONFIG`,
`~/.config/claude-rc/config.toml`, `./config.toml` — и что для установленной тулзы
правильное место второе. Строку про «скопируй `config.example.toml` в корень репозитория»
поправь так, чтобы она называла оба сценария: работа в клоне и установленная тулза.

- [ ] **Step 7: Прогнать полный гейт**

Run: `make check`
Expected: lint, typecheck и все тесты зелёные

- [ ] **Step 8: Коммит**

```bash
git add clauderc/paths.py clauderc/bot.py tests/test_paths.py README.md
git commit -m "$(cat <<'MSG'
feat: конфиг ищется по XDG-пути, а не рядом с исходниками

Путь Path(__file__).parent.parent делал установку пакета невозможной: uv tool
install кладёт код в чужой каталог, где config.toml взяться неоткуда. Поиск в
текущем каталоге оставлен последним звеном, чтобы разработка в клоне работала
без переменных окружения.
MSG
)"
```

---

### Task 2: `history.py` — диалоги Claude Code для каталога

**Files:**
- Create: `clauderc/history.py`
- Create: `tests/test_history.py`

**Interfaces:**
- Consumes: `paths.claude_projects() -> Path` (Task 1)
- Produces:
  - `history.Conversation` — frozen dataclass с полями `session_id: str`, `cwd: str`, `updated_at: float`, `preview: str`
  - `history.slug(cwd: str) -> str`
  - `history.conversations(cwd: str, *, limit: int = 5) -> list[Conversation]`

- [ ] **Step 1: Написать падающие тесты**

Создай `tests/test_history.py`:

```python
import json
import os
from pathlib import Path

import pytest
from clauderc import history


def _write(
    directory: Path, session_id: str, cwd: str, first_user: str, mtime: float
) -> Path:
    """Кладёт правдоподобный jsonl: служебные записи, потом первое сообщение."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    records = [
        {"type": "last-prompt", "sessionId": session_id},
        {"type": "mode", "sessionId": session_id},
        {"type": "user", "cwd": cwd, "message": {"role": "user", "content": first_user}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setattr(history.paths, "claude_projects", lambda: root)
    return root


def test_slug_replaces_each_non_alnum_separately() -> None:
    # Проверено на реальном каталоге: `/` и `.` дают два дефиса подряд.
    # Это НЕ то же самое, что _UNSAFE в remote.py, который серии схлопывает.
    assert history.slug("/Users/n/.tg-claude-worktrees") == "-Users-n--tg-claude-worktrees"
    assert history.slug("/a/b") == "-a-b"


def test_missing_directory_gives_empty_list(projects: Path) -> None:
    assert history.conversations("/no/such/place") == []


def test_reads_conversation_with_preview(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(projects / history.slug(str(cwd)), "aaa", str(cwd), "Работает сейчас бот?", 1000.0)

    (found,) = history.conversations(str(cwd))
    assert found.session_id == "aaa"
    assert found.cwd == str(cwd)
    assert found.preview == "Работает сейчас бот?"
    assert found.updated_at == 1000.0


def test_sorted_by_mtime_desc_and_limited(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "old", str(cwd), "старый", 1000.0)
    _write(directory, "mid", str(cwd), "средний", 2000.0)
    _write(directory, "new", str(cwd), "новый", 3000.0)

    assert [c.session_id for c in history.conversations(str(cwd))] == ["new", "mid", "old"]
    assert [c.session_id for c in history.conversations(str(cwd), limit=2)] == ["new", "mid"]


def test_skips_file_from_another_cwd(projects: Path, tmp_path: Path) -> None:
    # Слаг неоднозначен: `/a/b` и `/a.b` дают одно имя каталога. Поле cwd внутри
    # файла — единственная надёжная проверка.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "mine", str(cwd), "моё", 2000.0)
    _write(directory, "alien", "/somewhere/else", "чужое", 3000.0)

    assert [c.session_id for c in history.conversations(str(cwd))] == ["mine"]


def test_skips_broken_json(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "good", str(cwd), "живой", 1000.0)
    (directory / "broken.jsonl").write_text("{не json\n")

    assert [c.session_id for c in history.conversations(str(cwd))] == ["good"]


def test_file_without_cwd_is_skipped(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    (directory / "empty.jsonl").write_text(json.dumps({"type": "mode"}) + "\n")

    assert history.conversations(str(cwd)) == []


def test_preview_from_content_blocks(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    record = {
        "type": "user",
        "cwd": str(cwd),
        "message": {"content": [{"type": "text", "text": "  собери\n  релиз  "}]},
    }
    (directory / "blocks.jsonl").write_text(json.dumps(record) + "\n")

    (found,) = history.conversations(str(cwd))
    assert found.preview == "собери релиз"


def test_preview_is_trimmed(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(projects / history.slug(str(cwd)), "long", str(cwd), "я" * 200, 1000.0)

    (found,) = history.conversations(str(cwd))
    assert len(found.preview) == history.PREVIEW_CHARS + 1
    assert found.preview.endswith("…")


def test_conversation_without_user_message_gets_placeholder(
    projects: Path, tmp_path: Path
) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    record = {"type": "assistant", "cwd": str(cwd)}
    (directory / "quiet.jsonl").write_text(json.dumps(record) + "\n")

    (found,) = history.conversations(str(cwd))
    assert found.preview == "без названия"


def test_subdirectories_are_ignored(projects: Path, tmp_path: Path) -> None:
    # Рядом с jsonl лежат каталоги, названные тем же uuid.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "real", str(cwd), "настоящий", 1000.0)
    (directory / "decoy.jsonl").mkdir()

    assert [c.session_id for c in history.conversations(str(cwd))] == ["real"]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clauderc.history'`

- [ ] **Step 3: Написать модуль**

Создай `clauderc/history.py`:

```python
"""Диалоги Claude Code, лежащие в ~/.claude/projects.

Своего реестра сессий не заводим: единственный источник правды — то же
хранилище, из которого `claude --resume` берёт историю. Формат хранилища
публичным API не является, поэтому модуль изолирован и при любой неожиданности
возвращает пустой список — бот тогда деградирует до запуска без развилки.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clauderc import paths

log = logging.getLogger("clauderc.history")

PREVIEW_CHARS = 80
# Служебные записи идут первыми, а cwd и первое сообщение лежат в начале файла.
# Диалог бывает на сотни килобайт — читать его целиком незачем.
_MAX_LINES = 200
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_SPACES = re.compile(r"\s+")
_UNTITLED = "без названия"


@dataclass(frozen=True)
class Conversation:
    session_id: str
    cwd: str
    updated_at: float
    preview: str


def slug(cwd: str) -> str:
    """Имя каталога внутри ~/.claude/projects.

    Каждый символ вне [A-Za-z0-9] заменяется по отдельности, серии не
    схлопываются: `/Users/n/.x` даёт `-Users-n--x`, два дефиса подряд.
    """
    return _NON_ALNUM.sub("-", cwd)


def conversations(cwd: str, *, limit: int = 5) -> list[Conversation]:
    """Диалоги Claude Code для каталога, свежие первыми."""
    directory = _directory(cwd)
    if directory is None:
        return []

    wanted = _real(cwd)
    found: list[Conversation] = []
    for path in directory.glob("*.jsonl"):
        if not path.is_file():
            continue  # рядом с диалогами лежат каталоги, названные тем же uuid
        conversation = _read(path)
        if conversation is None or _real(conversation.cwd) != wanted:
            continue
        found.append(conversation)

    found.sort(key=lambda c: c.updated_at, reverse=True)
    return found[:limit]


def _directory(cwd: str) -> Path | None:
    """Каталог хранилища для пути. Пробуем и как есть, и через realpath.

    claude называет каталог по тому пути, из которого его запустили; наш
    вызывающий мог отдать симлинк.
    """
    root = paths.claude_projects()
    for candidate in (cwd, _real(cwd)):
        directory = root / slug(candidate)
        if directory.is_dir():
            return directory
    return None


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _read(path: Path) -> Conversation | None:
    cwd: str | None = None
    preview = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index >= _MAX_LINES:
                    break
                record = _record(line)
                if record is None:
                    continue
                if cwd is None and isinstance(record.get("cwd"), str):
                    cwd = record["cwd"]
                if not preview and record.get("type") == "user":
                    preview = _preview(record)
                if cwd is not None and preview:
                    break
            mtime = path.stat().st_mtime
    except OSError as exc:
        log.debug("skip %s: %s", path, exc)
        return None

    if cwd is None:
        return None
    return Conversation(
        session_id=path.stem, cwd=cwd, updated_at=mtime, preview=preview or _UNTITLED
    )


def _record(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None  # файл пишется прямо сейчас или испорчен — одна строка не повод падать
    return parsed if isinstance(parsed, dict) else None


def _preview(record: dict[str, Any]) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    else:
        return ""

    text = _SPACES.sub(" ", text).strip()
    if len(text) > PREVIEW_CHARS:
        return text[:PREVIEW_CHARS] + "…"
    return text
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_history.py -v`
Expected: PASS, 11 тестов

- [ ] **Step 5: Прогнать полный гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 6: Коммит**

```bash
git add clauderc/history.py tests/test_history.py
git commit -m "$(cat <<'MSG'
feat: чтение диалогов Claude Code для каталога

Своего реестра сессий не заводим: он разъехался бы с тем, что видит
`claude --resume`. Слаг каталога вычисляем, но подтверждаем полем cwd внутри
файла — кодировка неоднозначна, `/a/b` и `/a.b` дают одно имя.
MSG
)"
```

---

### Task 3: резюм в `remote.launch`

**Files:**
- Modify: `clauderc/remote.py` (сигнатура `launch`, сборка команды)
- Modify: `tests/test_remote.py` (дописать тесты в конец файла)

**Interfaces:**
- Consumes: ничего нового
- Produces: `remote.launch(repo: str, cwd: str, *, timeout_s: float = 90.0, resume: str | None = None) -> RemoteSession`, где `resume` — `None` (новый диалог), `"last"` (`--continue`) или id диалога (`--resume <id>`)

- [ ] **Step 1: Написать падающие тесты**

Допиши в конец `tests/test_remote.py`:

```python
_FRESH = "rc-repo\t/repos/repo\t1700000000\thttps://claude.ai/code/session_A"


def _capture_new_session(commands: list[str]) -> Handler:
    """Заглушка tmux: до new-session сессий нет, после — есть.

    Порядок важен: launch первым делом зовёт find(cwd), и заглушка, отдающая
    готовую сессию сразу, вернула бы её вместо запуска — new-session не случился бы.
    """

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "new-session":
            commands.append(args[-1])
            return 0, ""
        if args[0] == "capture-pane":
            return 0, "https://claude.ai/code/session_A"
        if args[0] == "list-sessions":
            return (0, _FRESH) if commands else (0, "")
        return 0, ""

    return handler


def _fast(monkeypatch: pytest.MonkeyPatch, commands: list[str]) -> None:
    """Заглушка tmux плюс нулевая пауза опроса: четыре теста иначе спят три секунды."""
    monkeypatch.setattr(remote, "_run", _stub(_capture_new_session(commands)))
    monkeypatch.setattr(remote, "tmux_available", lambda: True)
    monkeypatch.setattr(remote, "_POLL_S", 0)


async def test_launch_without_resume_has_no_extra_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5)

    assert "--resume" not in commands[0]
    assert "--continue" not in commands[0]


async def test_launch_with_last_uses_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="last")

    assert commands[0].endswith("--continue")


async def test_launch_with_id_uses_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="abc-123")

    assert commands[0].endswith("--resume abc-123")


async def test_launch_quotes_resume_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # id приходит из имени файла на диске — в командную строку без кавычек нельзя.
    commands: list[str] = []
    _fast(monkeypatch, commands)

    await remote.launch("repo", "/repos/repo", timeout_s=5, resume="a b; rm -rf /")

    assert "'a b; rm -rf /'" in commands[0]


async def test_launch_with_resume_returns_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Инвариант «один каталог — одна сессия» резюм не отменяет.
    started: list[str] = []

    def handler(*args: str) -> tuple[int, str]:
        if args[0] == "new-session":
            started.append(args[-1])
            return 0, ""
        return 0, _ROW

    monkeypatch.setattr(remote, "_run", _stub(handler))
    monkeypatch.setattr(remote, "tmux_available", lambda: True)

    session = await remote.launch("oms", "/repos/oms", timeout_s=5, resume="last")

    assert session.tmux_name == "rc-oms"
    assert started == []
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_remote.py -k resume -v`
Expected: FAIL — `launch() got an unexpected keyword argument 'resume'`

- [ ] **Step 3: Реализовать флаг**

В `clauderc/remote.py` добавь рядом с `launch` вспомогательную функцию:

```python
def _resume_flag(resume: str | None) -> str:
    """Хвост командной строки для резюма диалога."""
    if resume is None:
        return ""
    if resume == "last":
        return " --continue"
    return f" --resume {shlex.quote(resume)}"
```

и поменяй сборку команды в `launch`:

```python
    name = await _unique_name(repo, cwd)
    command = (
        _SCRUB_ENV
        + f"exec {shlex.quote(CLAUDE_BIN)} --remote-control {shlex.quote(repo)}"
        + _resume_flag(resume)
    )
```

Сигнатура:

```python
async def launch(
    repo: str, cwd: str, *, timeout_s: float = 90.0, resume: str | None = None
) -> RemoteSession:
```

Обнови докстринг: одной строкой скажи, что `resume` продолжает прежний диалог, а
проверка на живую сессию в `cwd` его не отменяет.

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_remote.py -v`
Expected: PASS, все тесты файла включая пять новых

- [ ] **Step 5: Прогнать полный гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 6: Коммит**

```bash
git add clauderc/remote.py tests/test_remote.py
git commit -m "$(cat <<'MSG'
feat: launch умеет продолжать прежний диалог Claude

Флаг идёт после --remote-control <name>: у --remote-control необязательный
аргумент-имя, и резюм, вставленный раньше, съел бы его.
MSG
)"
```

---

### Task 4: `watch.py` — замечать упавшие сессии

**Files:**
- Create: `clauderc/watch.py`
- Create: `tests/test_watch.py`

**Interfaces:**
- Consumes: `remote.list_sessions()`, `remote.kill_tmux()`, `remote.kill_all()`, `remote.session_name()`, `remote.RemoteSession` (существуют)
- Produces:
  - `watch.Died` — frozen dataclass с полями `name: str`, `tmux_name: str`, `cwd: str`
  - `watch.Watcher(*, poll_s: float = 15.0)`
  - `Watcher.kill(tmux_name: str) -> bool`
  - `Watcher.kill_named(repo: str) -> bool`
  - `Watcher.kill_all() -> int`
  - `Watcher.poll(on_died: Callable[[Died], Awaitable[None]]) -> None`
  - `Watcher.run(on_died: Callable[[Died], Awaitable[None]]) -> None`

- [ ] **Step 1: Написать падающие тесты**

Создай `tests/test_watch.py`:

```python
import asyncio

import pytest
from clauderc import watch
from clauderc.remote import RemoteSession
from clauderc.watch import Died, Watcher


def _session(name: str, cwd: str = "/repos/x") -> RemoteSession:
    return RemoteSession(
        name=name, tmux_name=f"rc-{name}", cwd=cwd, url="https://x", created_at=0
    )


def _sessions(monkeypatch: pytest.MonkeyPatch, *batches: list[RemoteSession]) -> None:
    """Подменяет list_sessions последовательностью снимков, по одному на вызов."""
    queue = list(batches)

    async def fake() -> list[RemoteSession]:
        return queue.pop(0) if queue else []

    monkeypatch.setattr(watch, "list_sessions", fake)


async def _collect(watcher: Watcher, times: int) -> list[Died]:
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    for _ in range(times):
        await watcher.poll(on_died)
    return seen


async def test_first_snapshot_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Перезапуск бота при живых сессиях не должен отчитываться о падениях.
    _sessions(monkeypatch, [_session("a")])
    assert await _collect(Watcher(), 1) == []


async def test_disappeared_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _sessions(monkeypatch, [_session("a", "/repos/a")], [])
    (died,) = await _collect(Watcher(), 2)
    assert died == Died(name="a", tmux_name="rc-a", cwd="/repos/a")


async def test_surviving_session_is_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _sessions(monkeypatch, [_session("a")], [_session("a")])
    assert await _collect(Watcher(), 2) == []


async def test_expected_death_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(monkeypatch, [_session("a")], [_session("a")], [])

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)          # базовый снимок
    assert await watcher.kill("rc-a") is True
    await watcher.poll(on_died)          # ещё жива
    await watcher.poll(on_died)          # исчезла — но её гасили мы
    assert seen == []
    assert killed == ["rc-a"]


async def test_mark_does_not_leak_to_next_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # Одноразовость метки: сессия с тем же именем, упавшая позже, должна попасть в отчёт.
    async def fake_kill(tmux_name: str) -> bool:
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    _sessions(
        monkeypatch,
        [_session("a")],  # базовый
        [],               # погашена нами
        [_session("a")],  # поднялась заново
        [],               # упала сама
    )

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)
    await watcher.kill("rc-a")
    await watcher.poll(on_died)
    await watcher.poll(on_died)
    await watcher.poll(on_died)
    assert [d.tmux_name for d in seen] == ["rc-a"]


async def test_kill_all_marks_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_kill_all() -> int:
        return 2

    monkeypatch.setattr(watch, "kill_all", fake_kill_all)
    _sessions(
        monkeypatch,
        [_session("a"), _session("b")],  # базовый
        [_session("a"), _session("b")],  # снимок внутри kill_all
        [],
    )

    watcher = Watcher()
    seen: list[Died] = []

    async def on_died(died: Died) -> None:
        seen.append(died)

    await watcher.poll(on_died)
    assert await watcher.kill_all() == 2
    await watcher.poll(on_died)
    assert seen == []


async def test_kill_named_translates_repo_name(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def fake_kill(tmux_name: str) -> bool:
        killed.append(tmux_name)
        return True

    monkeypatch.setattr(watch, "kill_tmux", fake_kill)
    await Watcher().kill_named("my.repo")
    assert killed == ["rc-my-repo"]


async def test_poll_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # poll честно пробрасывает: глотать — обязанность run, и она проверяется отдельно.
    async def broken() -> list[RemoteSession]:
        raise RuntimeError("tmux ушёл")

    monkeypatch.setattr(watch, "list_sessions", broken)

    async def on_died(died: Died) -> None:
        raise AssertionError("не должно вызываться")

    with pytest.raises(RuntimeError):
        await Watcher().poll(on_died)


async def test_run_survives_a_failed_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # Watcher не имеет права уронить бота: упавший опрос переживается, цикл идёт дальше.
    calls = {"n": 0}

    async def flaky() -> list[RemoteSession]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tmux ушёл")
        return []

    monkeypatch.setattr(watch, "list_sessions", flaky)

    async def on_died(died: Died) -> None:
        raise AssertionError("не должно вызываться")

    task = asyncio.create_task(Watcher(poll_s=0).run(on_died))
    for _ in range(200):  # граница вместо while: упавшая задача не должна вешать тест
        if calls["n"] >= 3:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 3
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_watch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clauderc.watch'`

- [ ] **Step 3: Написать модуль**

Создай `clauderc/watch.py`:

```python
"""Слежение за исчезновением RC-сессий.

События от tmux нам недоступны, а `exec` в панели означает, что упавший claude
уносит tmux-сессию целиком. Значит единственный надёжный признак смерти —
исчезновение из `list_sessions()`, и замечать его приходится опросом.

Гашение проходит через Watcher, а не через remote напрямую: иначе намеренно
убитая сессия попала бы в отчёт как упавшая, и отметить её было бы негде.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from clauderc.remote import RemoteSession, kill_all, kill_tmux, list_sessions, session_name

log = logging.getLogger("clauderc.watch")

# Заметно больше _POLL_S из remote: сессии не пропадают каждую секунду,
# а лишний опрос tmux — лишний процесс.
POLL_S = 15.0

OnDied = Callable[["Died"], Awaitable[None]]


@dataclass(frozen=True)
class Died:
    name: str
    tmux_name: str
    cwd: str


class Watcher:
    """Сравнивает снимки живых сессий и сообщает о тех, что исчезли не по нашей воле."""

    def __init__(self, *, poll_s: float = POLL_S) -> None:
        self._poll_s = poll_s
        self._known: dict[str, RemoteSession] | None = None
        self._expected: set[str] = set()

    async def kill(self, tmux_name: str) -> bool:
        self._expected.add(tmux_name)
        return await kill_tmux(tmux_name)

    async def kill_named(self, repo: str) -> bool:
        return await self.kill(session_name(repo))

    async def kill_all(self) -> int:
        for session in await list_sessions():
            self._expected.add(session.tmux_name)
        return await kill_all()

    async def poll(self, on_died: OnDied) -> None:
        current = {s.tmux_name: s for s in await list_sessions()}
        previous, self._known = self._known, current

        # Метки живут только пока жива сессия: иначе неудавшееся гашение оставило бы
        # вечное «не сообщать», и настоящее падение прошло бы молча.
        expected_gone = self._expected - set(current)
        self._expected &= set(current)

        if previous is None:
            return  # первый снимок базовый: что бы в нём ни было, падений ещё не видели

        for tmux_name, session in previous.items():
            if tmux_name in current or tmux_name in expected_gone:
                continue
            await on_died(Died(name=session.name, tmux_name=tmux_name, cwd=session.cwd))

    async def run(self, on_died: OnDied) -> None:
        while True:
            try:
                await self.poll(on_died)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watch poll failed")
            await asyncio.sleep(self._poll_s)
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_watch.py -v`
Expected: PASS, 9 тестов

- [ ] **Step 5: Прогнать полный гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 6: Коммит**

```bash
git add clauderc/watch.py tests/test_watch.py
git commit -m "$(cat <<'MSG'
feat: watcher замечает исчезнувшие RC-сессии

Гашение проходит через Watcher, а не через remote напрямую: у нас четыре места,
где сессию убивают намеренно, и метку «это мы» негде было бы поставить, если бы
kill остался разбросан по хендлерам.
MSG
)"
```

---

### Task 5: бот — развилка резюма, watcher и список при старте

**Files:**
- Modify: `clauderc/bot.py` (импорты, `main()`, `start_session`, точки гашения, новый хендлер)
- Modify: `tests/test_auth.py` → оставить как есть
- Create: `tests/test_bot_cards.py`

**Interfaces:**
- Consumes: `history.conversations`, `history.Conversation`, `watch.Watcher`, `watch.Died`, `remote.launch(..., resume=)`, `paths.config_file` (Tasks 1-4)
- Produces: ничего для последующих задач

- [ ] **Step 1: Написать падающие тесты на чистые помощники**

Создай `tests/test_bot_cards.py`:

```python
from clauderc.bot import _died_text, _resume_keyboard
from clauderc.watch import Died


def test_resume_keyboard_lists_new_continue_and_conversations() -> None:
    markup = _resume_keyboard(
        [
            ("t0", "New session"),
            ("t1", "Continue last"),
            ("t2", "сделай релиз"),
        ]
    )
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["New session", "Continue last", "сделай релиз"]


def test_resume_keyboard_callback_data_fits_telegram_limit() -> None:
    # В callback_data влезает 64 байта; id диалога туда не кладём — только токен.
    markup = _resume_keyboard([("deadbeef", "и" * 200)])
    (button,) = markup.inline_keyboard[0]
    assert button.callback_data == "res:deadbeef"
    assert len((button.callback_data or "").encode()) <= 64


def test_died_text_names_the_directory() -> None:
    text = _died_text(Died(name="oms", tmux_name="rc-oms", cwd="/repos/oms"))
    assert "oms" in text
    assert "/repos/oms" in text


def test_died_text_escapes_html() -> None:
    text = _died_text(Died(name="a&b", tmux_name="rc-a-b", cwd="/repos/<x>"))
    assert "&amp;" in text
    assert "<x>" not in text
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_bot_cards.py -v`
Expected: FAIL — `ImportError: cannot import name '_resume_keyboard' from 'clauderc.bot'`

- [ ] **Step 3: Добавить чистые помощники в `bot.py`**

Рядом с существующими `_open_keyboard` и `_fresh_text` (модульный уровень, вне `main`):

```python
def _resume_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Кнопки выбора диалога: по строке на вариант, в callback_data — только токен."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"res:{token}")]
            for token, label in items
        ]
    )


def _died_text(died: Died) -> str:
    return (
        f"⚠️ Сессия <b>{html.escape(died.name)}</b> завершилась\n"
        f"<code>{html.escape(died.cwd)}</code>"
    )
```

Добавь в шапку файла ровно один импорт — тот, что нужен помощникам сейчас:

```python
from clauderc.watch import Died
```

`history` и `Watcher` появятся в шагах 7 и 10, когда их станет кому использовать:
импорт, добавленный заранее, уронил бы ruff на коммите этого шага.

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_bot_cards.py -v`
Expected: PASS, 4 теста

- [ ] **Step 5: Коммит помощников**

```bash
make check
git add clauderc/bot.py tests/test_bot_cards.py
git commit -m "feat: карточки резюма и упавшей сессии"
```

`make check` здесь обязателен: это первый коммит, трогающий `bot.py`.

- [ ] **Step 6: Пропустить `resume` через `start_session`**

В `main()` найди `async def start_session(message, target, branch)` и добавь параметр:

```python
    async def start_session(
        message: Message, target: Path, branch: str | None, resume: str | None = None
    ) -> None:
```

В теле функции вызов `launch` становится:

```python
            session = await launch(
                cwd.name, str(cwd), timeout_s=config.launch_timeout_s, resume=resume
            )
```

Больше ничего в `start_session` не меняется: ветка `TrustRequired`, ветка
`LaunchError` и успешный ответ остаются как были.

- [ ] **Step 7: Добавить развилку `offer_start`**

Рядом с `start_session`, в `main()`. Словарь `resume_pending` объяви рядом с
существующими `trust_pending` / `tree_pending`:

```python
    resume_pending: dict[str, tuple[Path, str | None, str | None]] = {}
```

Добавь `history` в импорт модулей ядра: строка `from clauderc import browse, paths, worktrees`
становится `from clauderc import browse, history, paths, worktrees`.

```python
    async def offer_start(message: Message, target: Path, branch: str | None) -> None:
        """Запуск с выбором диалога, если в каталоге уже есть история.

        Для новой ветки истории быть не может — там свежий worktree, и лишний
        шаг только мешал бы.
        """
        if branch is not None:
            await start_session(message, target, branch)
            return

        found = history.conversations(str(target))
        if not found:
            await start_session(message, target, None)
            return

        items: list[tuple[str, str]] = []
        for label, resume in [("New session", None), ("Continue last", "last")]:
            token = uuid.uuid4().hex[:8]
            resume_pending[token] = (target, None, resume)
            items.append((token, label))
        for conversation in found:
            token = uuid.uuid4().hex[:8]
            resume_pending[token] = (target, None, conversation.session_id)
            items.append((token, conversation.preview))

        await message.answer(
            f"В <code>{html.escape(str(target))}</code> уже есть диалоги. Что поднимаем?",
            parse_mode="HTML",
            reply_markup=_resume_keyboard(items),
        )
```

- [ ] **Step 8: Добавить хендлер выбора**

Рядом с остальными `@dp.callback_query`, по образцу `on_pick`:

```python
    @dp.callback_query(F.data.startswith("res:"))
    async def on_resume(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        choice = resume_pending.pop((query.data or "").removeprefix("res:"), None)
        await query.answer()
        message = _live_message(query)
        if message is None:
            return
        await message.edit_reply_markup(reply_markup=None)
        if choice is None:
            await message.answer("Выбор устарел, повтори запуск.")
            return
        target, branch, resume = choice
        await start_session(message, target, branch, resume)
```

- [ ] **Step 9: Перевести точки запуска на `offer_start`**

Замени `start_session` на `offer_start` в четырёх местах, где запуск инициирует
человек (номера строк — до правок, ищи по контексту):

| Место | Было |
|---|---|
| `cmd_rc`, однозначное совпадение (~451) | `await start_session(message, matches[0], branch)` |
| `on_tree_start` (~557) | `await start_session(message, path, None)` |
| `on_nav`, действие `here` (~670) | `await start_session(message, state.cwd, None)` |
| `on_pick` (~748) | `await start_session(message, target, branch)` |

Не трогай `on_nav`, действие `newwt` (~674): там всегда новая ветка и новый
worktree, истории в нём быть не может. Не трогай `on_resume` из шага 8: он уже
знает, что выбрал человек.

- [ ] **Step 10: Провести гашение через Watcher**

Добавь импорт: `from clauderc.watch import Died` становится
`from clauderc.watch import Died, Watcher`. Объяви watcher в начале `main()`,
рядом с `state`:

```python
    watcher = Watcher()
```

Замени пять вызовов гашения:

| Строка (до правок) | Было | Стало |
|---|---|---|
| ~475 | `killed = await kill_all()` | `killed = await watcher.kill_all()` |
| ~530 | `await kill_session(session.name)` | `await watcher.kill(session.tmux_name)` |
| ~477 (в `cmd_rckill`) | `if await kill_session(name):` | `if await watcher.kill_named(name):` |
| ~602 | `await kill_tmux(session.tmux_name)` | `await watcher.kill(session.tmux_name)` |
| ~628 | `killed = await kill_tmux(tmux_name)` | `killed = await watcher.kill(tmux_name)` |
| ~712 | `await kill_tmux(tmux_name)` | `await watcher.kill(tmux_name)` |

Строка ~530 заодно чинит мелкую кривизну: `kill_session(session.name)` гоняла уже
очищенное имя через `session_name` второй раз. `watcher.kill(session.tmux_name)`
берёт имя напрямую.

После замены `kill_all`, `kill_session` и `kill_tmux` в `bot.py` больше не нужны —
убери их из блока импорта `from clauderc.remote import (...)`. Ruff сообщит,
если что-то осталось.

- [ ] **Step 11: Поднять watcher и отправить список при старте**

В конце `main()`, перед `await dp.start_polling(bot)`:

```python
    async def on_died(died: Died) -> None:
        token = uuid.uuid4().hex[:8]
        resume_pending[token] = (Path(died.cwd), None, "last")
        await bot.send_message(
            config.allowed_user_id,
            _died_text(died),
            parse_mode="HTML",
            reply_markup=_resume_keyboard([(token, "↻ Resume")]),
        )

    for session in await list_sessions():
        await bot.send_message(
            config.allowed_user_id,
            _list_item(session),
            parse_mode="HTML",
            reply_markup=_open_keyboard(session.url),
        )

    watch_task = asyncio.create_task(watcher.run(on_died))
    try:
        await dp.start_polling(bot)
    finally:
        watch_task.cancel()
```

Список отправляется до создания задачи намеренно: иначе первый опрос watcher'а мог
бы разойтись со списком, который человек только что получил.

- [ ] **Step 12: Прогнать полный гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 13: Проверить руками**

```bash
uv run claude-rc bot   # если Task 6 ещё не сделана — uv run python -m clauderc.bot
```

Ожидание в Telegram:
1. при старте прилетают карточки живых сессий (или тишина, если их нет);
2. `▶️ Start Claude RC` в каталоге с историей показывает `New session`,
   `Continue last` и превью диалогов; тап поднимает сессию;
3. в каталоге без истории запуск идёт сразу, без лишнего шага;
4. `tmux kill-session -t '=rc-<имя>'` руками в терминале в течение 15 секунд
   даёт карточку «сессия завершилась» с кнопкой `Resume`;
5. кнопка `⏹ Stop` в боте карточку о падении **не** порождает.

- [ ] **Step 14: Коммит**

```bash
git add clauderc/bot.py
git commit -m "$(cat <<'MSG'
feat: выбор диалога при запуске, отчёт о падениях и список при старте

Сессия в tmux исчезает целиком вместе с упавшим claude, и до сих пор потеря
обнаруживалась только кликом по мёртвой ссылке. Гашение переведено на Watcher,
чтобы намеренно убитые сессии не попадали в отчёт.
MSG
)"
```

---

### Task 6: `cli.py` — команда `claude-rc`

**Files:**
- Create: `clauderc/cli.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml` (секция `[project.scripts]`)
- Modify: `Makefile` (цель `run`)
- Modify: `README.md` (установка и команды)
- Modify: `CLAUDE.md` (таблица модулей, раздел «Команды»)

**Interfaces:**
- Consumes: `paths`, `history`, `remote`, `repos`, `worktrees`, `bot.main` (Tasks 1-5)
- Produces: `clauderc.cli.main(argv: list[str] | None = None) -> int` и `clauderc.cli.run() -> None` (точка входа консольного скрипта)

- [ ] **Step 1: Написать падающие тесты**

Создай `tests/test_cli.py`:

```python
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
    config.write_text(
        f'bot_token = "x"\nallowed_user_id = 1\nrc_roots = ["{root}"]\n'
    )
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clauderc.cli'`

- [ ] **Step 3: Написать модуль**

Создай `clauderc/cli.py`:

```python
"""Команда `claude-rc` — тот же набор действий, что у бота, но из терминала.

Всё, кроме разбора аргументов и печати, живёт в модулях ядра: CLI и бот — две
равноправные морды над одним кодом, а не копия логики.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from clauderc import paths, worktrees
from clauderc.config import load_config
from clauderc.remote import (
    LaunchError,
    RemoteSession,
    TrustRequired,
    await_url,
    confirm_trust,
    kill_all,
    kill_tmux,
    launch,
    list_sessions,
)
from clauderc.worktrees import WorktreeError

# Коды возврата: 1 — не получилось сделать, 2 — не с чем работать.
EXIT_FAILED = 1
EXIT_ENVIRONMENT = 2


def run() -> None:
    """Точка входа консольного скрипта."""
    sys.exit(main())


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return EXIT_ENVIRONMENT

    handler: Any = getattr(_Commands, args.command)
    return int(handler(args))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-rc", description="RC-сессии Claude Code")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bot", help="запустить Telegram-бота на переднем плане")
    sub.add_parser("version", help="версия")

    sessions = sub.add_parser("sessions", help="живые RC-сессии")
    sessions.add_argument("--json", action="store_true", dest="as_json")

    start = sub.add_parser("start", help="поднять сессию")
    start.add_argument("path", nargs="?", default=".", help="каталог (по умолчанию текущий)")
    start.add_argument("--branch", help="создать worktree под ветку")
    start.add_argument("--resume", help="продолжить диалог: last или id")

    stop = sub.add_parser("stop", help="погасить сессию")
    stop.add_argument("target", nargs="?", help="имя сессии или каталог")
    stop.add_argument("--all", action="store_true", dest="every")

    doctor = sub.add_parser("doctor", help="проверить окружение")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    return parser


class _Commands:
    """Обработчики подкоманд. Имя метода совпадает с именем команды."""

    @staticmethod
    def bot(args: argparse.Namespace) -> int:
        from clauderc.bot import main as bot_main

        asyncio.run(bot_main())
        return 0

    @staticmethod
    def version(args: argparse.Namespace) -> int:
        try:
            print(package_version("claude-rc"))
        except PackageNotFoundError:
            print("unknown (пакет не установлен)")
        return 0

    @staticmethod
    def sessions(args: argparse.Namespace) -> int:
        found = asyncio.run(list_sessions())
        if args.as_json:
            print(json.dumps({"sessions": [_as_dict(s) for s in found]}, ensure_ascii=False))
            return 0
        if not found:
            print("Живых сессий нет.")
            return 0
        for session in found:
            print(f"{session.name}\t{session.cwd}\t{int(session.uptime_s())}s\t{session.url}")
        return 0

    @staticmethod
    def start(args: argparse.Namespace) -> int:
        target = Path(args.path).expanduser().resolve()
        if not target.is_dir():
            print(f"Каталог не найден: {target}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        try:
            session = asyncio.run(_start(target, args.branch, args.resume))
        except (LaunchError, WorktreeError) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        except _TrustDeclined as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_ENVIRONMENT
        print(f"{session.name}\t{session.cwd}\n{session.url}")
        return 0

    @staticmethod
    def stop(args: argparse.Namespace) -> int:
        if args.every:
            print(f"Погашено сессий: {asyncio.run(kill_all())}.")
            return 0
        if not args.target:
            print("Укажи имя сессии, каталог или --all.", file=sys.stderr)
            return EXIT_ENVIRONMENT
        killed = asyncio.run(_stop(args.target))
        if killed is None:
            print(f"Сессия не найдена: {args.target}", file=sys.stderr)
            return EXIT_FAILED
        print(f"Погашена: {killed}")
        return 0

    @staticmethod
    def doctor(args: argparse.Namespace) -> int:
        checks = _diagnose()
        if args.as_json:
            print(json.dumps({"checks": checks}, ensure_ascii=False))
        else:
            for check in checks:
                print(f"{'✓' if check['ok'] else '✗'} {check['name']}: {check['detail']}")
        return 0 if all(check["ok"] for check in checks) else EXIT_ENVIRONMENT


class _TrustDeclined(RuntimeError):
    """Каталог требует подтверждения доверия, а подтвердить некому."""


def _as_dict(session: RemoteSession) -> dict[str, Any]:
    return {
        "name": session.name,
        "tmux_name": session.tmux_name,
        "cwd": session.cwd,
        "url": session.url,
        "uptime_s": int(session.uptime_s()),
    }


async def _start(target: Path, branch: str | None, resume: str | None) -> RemoteSession:
    cwd = target
    if branch:
        config = load_config(paths.config_file())
        cwd = await worktrees.ensure(target, branch, config.worktree_root)
    try:
        return await launch(cwd.name, str(cwd), resume=resume)
    except TrustRequired as need:
        return await _ask_trust(need)


async def _ask_trust(need: TrustRequired) -> RemoteSession:
    """Диалог доверия каталогу. За терминалом человек — его «да» и есть решение."""
    if not sys.stdin.isatty():
        raise _TrustDeclined(
            f"Каталог {need.cwd} ждёт подтверждения доверия, а stdin не терминал.\n"
            f"Подтверди в панели: tmux attach -t {need.tmux_name}"
        )
    answer = input(f"Claude впервые видит {need.cwd}. Доверяешь каталогу? [y/N] ")
    if answer.strip().lower() not in {"y", "yes", "д", "да"}:
        await kill_tmux(need.tmux_name)
        raise _TrustDeclined("Отменено, сессия погашена.")
    await confirm_trust(need.tmux_name)
    # watch_trust=False: диалог ещё мгновение висит на экране и был бы принят
    # за неотвеченный (та же причина, что в боте).
    return await await_url(need.tmux_name, need.cwd, watch_trust=False)


async def _stop(target: str) -> str | None:
    """Гасит сессию по имени или каталогу.

    Существования каталога не требуем: worktree мог быть удалён, а сессия в нём
    остаться — именно её и нужно погасить.
    """
    wanted = os.path.realpath(Path(target).expanduser())
    for session in await list_sessions():
        if session.name == target or os.path.realpath(session.cwd) == wanted:
            if await kill_tmux(session.tmux_name):
                return session.name
    return None


def _diagnose() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for binary in ("tmux", "claude"):
        found = shutil.which(binary)
        checks.append(
            {
                "name": binary,
                "ok": found is not None,
                "detail": found or "не найден в PATH",
            }
        )

    config_path = paths.config_file()
    if not config_path.is_file():
        checks.append({"name": "config", "ok": False, "detail": f"нет файла {config_path}"})
        return checks

    try:
        config = load_config(config_path)
    except (ValueError, KeyError, OSError) as exc:
        checks.append({"name": "config", "ok": False, "detail": f"{config_path}: {exc}"})
        return checks

    checks.append({"name": "config", "ok": True, "detail": str(config_path)})
    # Значение токена не печатаем никогда — только факт, что он не пуст.
    checks.append(
        {
            "name": "bot_token",
            "ok": bool(config.bot_token),
            "detail": "задан" if config.bot_token else "пуст",
        }
    )
    checks.append(
        {
            "name": "allowed_user_id",
            "ok": config.allowed_user_id != 0,
            "detail": "задан" if config.allowed_user_id else "не задан",
        }
    )
    checks.append(
        {
            "name": "rc_roots",
            "ok": True,
            "detail": ", ".join(str(root) for root in config.rc_roots),
        }
    )
    return checks
```

- [ ] **Step 4: Проверить, что тесты проходят**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 16 тестов

- [ ] **Step 5: Объявить точку входа**

В `pyproject.toml` после блока `dependencies` добавь:

```toml
[project.scripts]
claude-rc = "clauderc.cli:run"
```

- [ ] **Step 6: Проверить, что команда ставится и работает**

```bash
uv sync --all-groups
uv run claude-rc version
uv run claude-rc doctor
uv run claude-rc sessions
```

Expected: `version` печатает `0.1.0`, `doctor` — список проверок, `sessions` — живые
сессии или «Живых сессий нет.»

- [ ] **Step 7: Перевести `make run` на CLI**

В `Makefile`:

```make
run: install
	uv run claude-rc bot
```

`python -m clauderc.bot` продолжает работать: на него ссылается launchd-плист,
и ломать его до третьей части незачем.

- [ ] **Step 8: Обновить README**

Добавь секцию про CLI: установку (`uv tool install .`), таблицу команд из этого
плана и строку про то, что `claude-rc bot` — то же самое, что `make run`. Про
диалог доверия скажи явно: в терминале он спрашивается на stdin, а без tty
команда падает с кодом 2 и подсказкой `tmux attach`.

- [ ] **Step 9: Обновить CLAUDE.md**

В таблицу «Структура» добавь четыре строки:

```
| `clauderc/paths.py` | где лежат конфиг, лог и хранилище диалогов Claude Code |
| `clauderc/history.py` | диалоги Claude Code для каталога (для резюма) |
| `clauderc/watch.py` | опрос tmux: замечает исчезнувшие сессии, метит намеренные гашения |
| `clauderc/cli.py` | команда `claude-rc`: те же действия из терминала |
```

В раздел «Грабли, на которые уже наступали» добавь два абзаца:

```
**Слаг каталога в `~/.claude/projects` — посимвольный.** Каждый символ вне
`[A-Za-z0-9]` заменяется на `-` по отдельности, серии не схлопываются:
`/Users/n/.x` даёт `-Users-n--x`, два дефиса подряд. Регексп `_UNSAFE` из
`remote.py` серии как раз схлопывает — скопировать его сюда нельзя. И сам слаг
неоднозначен, поэтому `history` подтверждает попадание полем `cwd` внутри файла.

**Гасить сессию мимо `Watcher` нельзя.** Watcher считает падением любое
исчезновение сессии, которое он не пометил как ожидаемое. `remote.kill_tmux`
напрямую из хендлера даст пользователю карточку «сессия упала» сразу после того,
как он сам нажал Stop.
```

В раздел «Команды» добавь строку про `claude-rc`.

- [ ] **Step 10: Прогнать полный гейт**

Run: `make check`
Expected: всё зелёное

- [ ] **Step 11: Коммит**

```bash
git add clauderc/cli.py tests/test_cli.py pyproject.toml Makefile README.md CLAUDE.md
git commit -m "$(cat <<'MSG'
feat: команда claude-rc

Вторая морда над тем же ядром: бот нужен с телефона, а с самой машины удобнее
из терминала. Диалог доверия в CLI спрашивается на stdin — за терминалом сидит
человек, и это его решение о правах на каталог, а не автоподтверждение.
MSG
)"
```

---

## Проверка целиком

- [ ] **Прогнать гейт на чистом окружении**

```bash
make check
```

- [ ] **Убедиться, что установленная тулза работает вне репозитория**

```bash
uv tool install --force .
cd /tmp && claude-rc doctor
```

Ожидание: `doctor` находит конфиг по XDG-пути (или честно говорит, что его нет и
куда положить), а не падает с трассировкой.

- [ ] **Прибрать за проверкой**

```bash
uv tool uninstall claude-rc
```

- [ ] **Открыть PR**

```bash
git push -u origin feat/cli-and-resume
gh pr create --base master --title "feat: CLI-тулза и резюм сессий" --body "$(cat <<'BODY'
Первая из трёх частей: установка, CLI и резюм. Приложение в меню-баре и доставка — отдельными PR.

- конфиг переехал на XDG-путь: без этого пакет нельзя было поставить
- `history.py` читает диалоги Claude Code, `remote.launch` умеет `--resume`/`--continue`
- `watch.py` замечает упавшие сессии; гашение проведено через него, чтобы намеренные не попадали в отчёт
- `claude-rc` — вторая морда над тем же ядром

Спека: `docs/superpowers/specs/2026-08-18-cli-and-resume-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```
