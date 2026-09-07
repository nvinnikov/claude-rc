# Пульт для сессий — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** У каждой RC-сессии появляется паспорт (все входы разом), наблюдаемое состояние и рычаги (`send`, `restart --mode`, `rename`, `connect`, `forward`), одинаково доступные из бота, локального CLI и CLI с другой машины через `--host`.

**Architecture:** Вся механика — в модулях `clauderc/` (`passport.py`, `state_probe.py`, `actions.py`, `proxy.py`, `forward.py`); `cli.py` и `bot.py` только разбирают аргументы и рисуют. Сессия по-прежнему живёт в tmux, ключ — рабочий каталог, новые факты о ней (`@rc_mode`) кладутся в user-опции tmux рядом с `@rc_url` и `@rc_label`. Удалённость — `ssh` через `os.execvp`, без демона.

**Tech Stack:** Python 3.12, asyncio, aiogram 3, tmux 3.x, ssh/lsof/pgrep; pytest + pytest-asyncio (`asyncio_mode = auto`), ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-09-06-control-plane-design.md`

## Global Constraints

- Гейт `make check` (ruff format --check, ruff check, mypy --strict на код и тесты, pytest) зелёный перед каждым коммитом.
- Коммиты — Conventional Commits, тело по-русски, объясняет «почему». Каждая задача — свой коммит.
- Код, идентификаторы, логи — английские; докстринги, комментарии, сообщения пользователю и тексты бота — русские; подписи кнопок — английские.
- Новый ключ конфига обязан попасть в `_EXTRA_KEYS` в `clauderc/cli.py` и в `config.example.toml`.
- Никакого пользовательского ввода в shell без `shlex.quote`. Target tmux: `=имя` для `kill-session`/`has-session`/`rename-session`/`attach`, `=имя:` там, где ждут target-pane (`capture-pane`, `send-keys`, `set-option`, `list-panes`).
- Внешние вызовы (`lsof`, `pgrep`, `ssh` в `run_remote`) — с таймаутом.
- Гашение сессии в боте — только через `Watcher.kill(tmux_name, cwd)`.
- Изменение поведения — с тестом. Тесты без сети; tmux-тесты помечены `skipif(not remote.tmux_available())` и идут на своём сокете `CLAUDE_RC_TMUX_SOCKET`.
- README.md и README.ru.md правятся вместе (задача 17).
- Стиль тестов: `remote._run` подменяется через `monkeypatch.setattr(remote, "_run", _stub(handler))` из `tests/test_remote.py`; CLI-тесты зовут `cli.main([...])` и подменяют `cli.<имя>`.

## Карта файлов

| Файл | Ответственность | Задачи |
|---|---|---|
| `clauderc/config.py` | новое поле `host` | 1 |
| `clauderc/passport.py` (новый) | `Passport`: сбор из `RemoteSession`+git+конфиг, рендер в text/html/dict | 2, 3, 10 |
| `clauderc/remote.py` | `@rc_mode`, `RemoteSession.mode`, режим `auto` по умолчанию, `attach_argv(read_only)` | 11, 15 |
| `clauderc/worktrees.py` | `branch_for(name)`, `label(path, name=)` | 4 |
| `clauderc/state_probe.py` (новый) | состояние по панели, хвост, порты | 8, 9 |
| `clauderc/actions.py` (новый) | `send`, `tail`, `rename`, `restart` | 6, 12, 13 |
| `clauderc/proxy.py` (новый) | `strip_host`, `remote_argv`, `exec_remote`, `run_remote` | 7 |
| `clauderc/forward.py` (новый) | ssh-туннели с pid-файлами | 16 |
| `clauderc/cli.py` | подкоманды `rename`, `send`, `restart`, `connect`, `forward`; флаги `--host`, `--name`; вывод через паспорт | 2, 4, 6, 7, 12, 13, 15, 16 |
| `clauderc/bot.py` | карточка = паспорт; шаг имени; кнопки `Bypass`/`/mcp`/`Tail`/`Rename`; `Start (bypass)` | 3, 5, 14 |
| `tests/test_passport.py`, `tests/test_state_probe.py`, `tests/test_actions.py`, `tests/test_proxy.py`, `tests/test_forward.py` (новые), `tests/fixtures/panes/*.txt` | тесты | по задачам |
| `README.md`, `README.ru.md`, `CLAUDE.md`, `config.example.toml` | документация | 1, 17 |

---

### Task 0: Закоммитить хвост (`whoami`, `@rc_label`)

**Files:**
- Modify: ничего — коммит уже сделанных правок в `CLAUDE.md`, `README.md`, `README.ru.md`, `clauderc/bot.py`, `clauderc/cli.py`, `clauderc/remote.py`, `clauderc/worktrees.py`, `tests/test_cli.py`, `tests/test_remote.py`, `tests/test_worktrees.py`

**Interfaces:**
- Produces: `RemoteSession.label: str`, `remote.find_enclosing(cwd)`, `worktrees.label(path) -> str`, CLI `whoami`, user-опция `@rc_label`. Всё последующее считает это данностью.

- [ ] **Step 1: Убедиться, что гейт зелёный**

Run: `make check`
Expected: `ruff` без замечаний, `mypy` `Success`, pytest `392 passed, 1 skipped` (число может отличаться на единицы).

- [ ] **Step 2: Посмотреть, что коммитим**

Run: `git status --short && git diff --stat`
Expected: 10 изменённых файлов, никаких новых неотслеживаемых кроме `docs/`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md README.ru.md clauderc/bot.py clauderc/cli.py clauderc/remote.py clauderc/worktrees.py tests/test_cli.py tests/test_remote.py tests/test_worktrees.py
git commit -m "feat: whoami и ярлык repo@branch на всех поверхностях

Агент внутри сессии узнаёт себя через claude-rc whoami по каталогу и его
родителям. Ярлык repo@branch хранится в @rc_label и уходит разом в
--remote-control, -n и карточку бота: без него приложение подставляло
авто-название, а каталог worktree говорил меньше, чем ветка."
```

---

### Task 1: Ключ конфига `host`

**Files:**
- Modify: `clauderc/config.py` (dataclass `Config`, `load_config`)
- Modify: `clauderc/cli.py:682-689` (`_EXTRA_KEYS`)
- Modify: `config.example.toml`
- Test: `tests/test_config.py`, `tests/test_setup.py`

**Interfaces:**
- Produces: `Config.host: str` (пустая строка — не задан).

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_config.py` (посмотри, как там строится минимальный конфиг — есть хелпер, пишущий `bot_token`/`allowed_user_id`/`rc_roots` во временный файл; используй его):

```python
def test_host_defaults_to_empty(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, ""))
    assert config.host == ""


def test_host_is_read_and_stripped(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, 'host = " m1 "\n'))
    assert config.host == "m1"


def test_host_must_be_a_string(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="host"):
        load_config(_write(tmp_path, "host = 1\n"))
```

В `tests/test_setup.py` найди тест про перенос `_EXTRA_KEYS` при перезаписи (grep `worktree_root` в этом файле) и добавь `host = "m1"` во входной файл и `assert 'host = "m1"' in written` в проверку.

- [ ] **Step 2: Запустить, убедиться, что падают**

Run: `uv run pytest tests/test_config.py -k host tests/test_setup.py -q`
Expected: FAIL — `Config` не имеет атрибута `host`; setup-тест не находит `host` в результате.

- [ ] **Step 3: Реализовать**

`clauderc/config.py`:

```python
@dataclass(frozen=True)
class Config:
    ...
    pull_before_start: bool
    # Как эта машина зовётся по ssh с других машин (алиас из ~/.ssh/config).
    # Пусто — паспорт сессии печатает только локальные формы подсадки.
    host: str
```

в `load_config`:

```python
    host = _optional_str(raw, "host")
    ...
    return Config(..., pull_before_start=pull_before_start, host=host)


def _optional_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key}: ожидалась строка")
    return value.strip()
```

`clauderc/cli.py`: добавить `"host",` в `_EXTRA_KEYS`.

`config.example.toml`, в конец:

```toml
# Как эта машина зовётся по ssh с других твоих машин — алиас из ~/.ssh/config.
# Нужен только при второй машине: с ним паспорт сессии печатает готовую команду
# `ssh <host> -t 'tmux attach …'` и префикс `claude-rc --host <host>`.
# Одна машина — полный продукт и без него.
# host = "m1"
```

Проверь `grep -rn "Config(" clauderc tests` — везде, где `Config(...)` собирается руками (тесты бота, `_diagnose`), добавь `host=""`.

- [ ] **Step 4: Прогнать гейт**

Run: `make check`
Expected: зелёный.

- [ ] **Step 5: Commit**

```bash
git add clauderc/config.py clauderc/cli.py config.example.toml tests/test_config.py tests/test_setup.py
git commit -m "feat(config): ключ host — как машина зовётся по ssh снаружи

Паспорт сессии должен печатать команду подсадки со второй машины, а бот
не знает, как его хост зовут снаружи. Опционален: одна машина — полный
продукт без него. В _EXTRA_KEYS, иначе визард его сотрёт."
```

---

### Task 2: Модуль `passport.py` и `sessions`/`whoami` через него

**Files:**
- Create: `clauderc/passport.py`
- Modify: `clauderc/cli.py` (`_Commands.sessions`, `_Commands.whoami`, `_as_dict`)
- Test: `tests/test_passport.py` (новый), `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Passport:
      label: str; name: str; host: str; cwd: str; branch: str; blockers: tuple[str, ...]
      url: str; session_id: str; tmux_name: str; created_at: int; uptime_s: int
      mode: str; attach: str; cli: str
      state: str = ""; last_lines: tuple[str, ...] = (); listening: tuple[int, ...] = ()
  def attach_line(tmux_name: str, host: str) -> str
  def cli_prefix(host: str) -> str
  def build(session: RemoteSession, *, host: str, tree: Worktree | None) -> Passport
  async def collect(sessions: list[RemoteSession], *, host: str) -> list[Passport]
  def as_dict(p: Passport) -> dict[str, Any]
  def as_text(p: Passport) -> str
  def as_html(p: Passport) -> str
  ```
  `state`/`last_lines`/`listening` заполняются в задаче 10, до неё остаются пустыми значениями по умолчанию и **не попадают** в `as_dict` (ключи добавляются в задаче 10).
- Consumes: `RemoteSession` (поля `name, tmux_name, cwd, url, created_at, label`), `worktrees.inspect(Path) -> Worktree | None` (`branch`, `blockers`), `remote.attach_command`.

- [ ] **Step 1: Написать падающие тесты `tests/test_passport.py`**

```python
import json
from pathlib import Path

import pytest
from clauderc import passport, remote
from clauderc.remote import RemoteSession
from clauderc.worktrees import Worktree


def _session(tmux_name: str = "session_01ABC", label: str = "oms@mcp-fix") -> RemoteSession:
    return RemoteSession(
        name=label or "oms",
        tmux_name=tmux_name,
        cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC" if tmux_name.startswith("session_") else "",
        created_at=0,
        label=label,
    )


def _tree() -> Worktree:
    return Worktree(path=Path("/repos/oms"), repo="oms", branch="mcp-fix", dirty=False, unpushed=0)


def test_attach_line_is_local_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    assert passport.attach_line("session_01ABC", "") == "tmux attach -d -t =session_01ABC"


def test_attach_line_wraps_in_ssh_with_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    # -t: без tty tmux ответит «open terminal failed». Внутренняя команда — одной
    # квотированной строкой, чтобы удалённый shell не разобрал её на куски.
    assert (
        passport.attach_line("session_01ABC", "m1")
        == "ssh m1 -t 'tmux attach -d -t =session_01ABC'"
    )


def test_cli_prefix() -> None:
    assert passport.cli_prefix("") == "claude-rc"
    assert passport.cli_prefix("m1") == "claude-rc --host m1"
    # Хост с пробелом невозможен в ssh, но квотинг не должен зависеть от удачи.
    assert passport.cli_prefix("a b") == "claude-rc --host 'a b'"


def test_build_takes_session_id_from_renamed_tmux_name() -> None:
    p = passport.build(_session(), host="m1", tree=_tree())
    assert p.session_id == "session_01ABC"
    assert p.branch == "mcp-fix"
    assert p.blockers == ()
    assert p.label == "oms@mcp-fix"


def test_build_has_no_session_id_before_rename() -> None:
    p = passport.build(_session(tmux_name="rc-oms"), host="", tree=None)
    assert p.session_id == ""
    assert p.branch == ""


def test_as_dict_keeps_the_old_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Старые читатели sessions --json опираются на name/label/tmux_name/cwd/url/uptime_s/attach.
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    d = passport.as_dict(passport.build(_session(), host="", tree=_tree()))
    assert {"name", "label", "tmux_name", "cwd", "url", "uptime_s", "attach"} <= set(d)
    assert d["host"] == ""
    assert d["session_id"] == "session_01ABC"
    assert d["cli"] == "claude-rc"
    json.dumps(d)  # сериализуемо без кастомного энкодера


def test_as_text_lists_every_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    text = passport.as_text(passport.build(_session(), host="m1", tree=_tree()))
    assert "oms@mcp-fix" in text
    assert "https://claude.ai/code/session_01ABC" in text
    assert "ssh m1 -t 'tmux attach -d -t =session_01ABC'" in text
    assert "claude-rc --host m1" in text


def test_as_html_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    session = RemoteSession(
        name="a<b", tmux_name="rc-a", cwd="/r/<x>", url="", created_at=0, label="a<b"
    )
    html_text = passport.as_html(passport.build(session, host="", tree=None))
    assert "<x>" not in html_text and "&lt;x&gt;" in html_text
    assert "ссылка неизвестна" in html_text


async def test_collect_inspects_each_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return _tree() if str(path) == "/repos/oms" else None

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    found = await passport.collect([_session()], host="m1")
    assert [p.branch for p in found] == ["mcp-fix"]
    assert found[0].host == "m1"
```

- [ ] **Step 2: Запустить, убедиться, что падают**

Run: `uv run pytest tests/test_passport.py -q`
Expected: FAIL — `ModuleNotFoundError: clauderc.passport`.

- [ ] **Step 3: Реализовать `clauderc/passport.py`**

```python
"""Паспорт сессии: все входы в неё разом, одинаковые на всех поверхностях.

Одна структура и три рендера (текст, HTML, dict), чтобы карточка бота, вывод
CLI и `--json` для агента не расходились: раньше карточка носила только имя
tmux, CLI — команду подсадки для этой же машины, а агенту не доставалось
ничего.
"""

from __future__ import annotations

import html
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clauderc import worktrees
from clauderc.remote import RemoteSession, attach_command
from clauderc.worktrees import Worktree

_SESSION_PREFIX = "session_"


@dataclass(frozen=True)
class Passport:
    label: str
    name: str
    host: str
    cwd: str
    branch: str
    blockers: tuple[str, ...]
    url: str
    session_id: str
    tmux_name: str
    created_at: int
    uptime_s: int
    mode: str
    attach: str
    cli: str
    state: str = ""
    last_lines: tuple[str, ...] = ()
    listening: tuple[int, ...] = field(default_factory=tuple)


def attach_line(tmux_name: str, host: str) -> str:
    """Команда подсадки: локальная, а при `host` — обёрнутая в ssh.

    `-t` обязателен: без tty tmux отвечает «open terminal failed». Внутренняя
    команда уходит одной квотированной строкой — удалённый shell разобрал бы
    её на слова заново.
    """
    local = attach_command(tmux_name)
    if not host:
        return local
    return shlex.join(["ssh", host, "-t", local])


def cli_prefix(host: str) -> str:
    return shlex.join(["claude-rc", "--host", host]) if host else "claude-rc"


def build(session: RemoteSession, *, host: str, tree: Worktree | None) -> Passport:
    tmux_name = session.tmux_name
    session_id = tmux_name if tmux_name.startswith(_SESSION_PREFIX) else ""
    return Passport(
        label=session.label or session.name,
        name=session.name,
        host=host,
        cwd=session.cwd,
        branch=tree.branch if tree is not None else "",
        blockers=tuple(tree.blockers) if tree is not None else (),
        url=session.url,
        session_id=session_id,
        tmux_name=tmux_name,
        created_at=session.created_at,
        uptime_s=int(session.uptime_s()),
        mode=getattr(session, "mode", ""),  # поле появляется в remote в задаче 11
        attach=attach_line(tmux_name, host),
        cli=cli_prefix(host),
    )


async def collect(sessions: list[RemoteSession], *, host: str) -> list[Passport]:
    """Паспорта для списка сессий; ветка — из git по каталогу каждой."""
    result: list[Passport] = []
    for session in sessions:
        tree = await worktrees.inspect(Path(session.cwd))
        result.append(build(session, host=host, tree=tree))
    return result


def as_dict(p: Passport) -> dict[str, Any]:
    return {
        "name": p.name,
        "label": p.label,
        "host": p.host,
        "tmux_name": p.tmux_name,
        "session_id": p.session_id,
        "cwd": p.cwd,
        "branch": p.branch,
        "blockers": list(p.blockers),
        "url": p.url,
        "uptime_s": p.uptime_s,
        "mode": p.mode,
        "attach": p.attach,
        "cli": p.cli,
    }


def _uptime(seconds: int) -> str:
    minutes = seconds // 60
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


def _tree_line(p: Passport) -> str:
    if not p.branch:
        return ""
    state = "; ".join(p.blockers) or "чисто"
    mode = f" · {p.mode}" if p.mode else ""
    return f"🌿 {p.branch} · {state}{mode}"


def as_text(p: Passport) -> str:
    head = f"{p.label} · {_uptime(p.uptime_s)}" + (f" · {p.host}" if p.host else "")
    lines = [head, p.cwd, _tree_line(p), f"🔗 {p.url or 'ссылка неизвестна'}", f"🖥 {p.attach}"]
    lines.append(f"🤖 {p.cli} send {shlex.quote(p.label)} '/mcp' --tail")
    return "\n".join(line for line in lines if line)


def as_html(p: Passport) -> str:
    e = html.escape
    head = f"<b>{e(p.label)}</b> · {_uptime(p.uptime_s)}" + (f" · {e(p.host)}" if p.host else "")
    lines = [
        head,
        f"<code>{e(p.cwd)}</code>",
        e(_tree_line(p)),
        e(p.url) if p.url else "ссылка неизвестна",
        f"🖥 <code>{e(p.attach)}</code>",
        f"🤖 <code>{e(p.cli)} send {e(shlex.quote(p.label))} '/mcp' --tail</code>",
    ]
    return "\n".join(line for line in lines if line)
```

`_uptime` дублирует `bot._uptime` — в задаче 3 бот перейдёт на паспортный, а свой удалит.

- [ ] **Step 4: Перевести CLI на паспорт**

В `clauderc/cli.py`: импорт `from clauderc import passport as passport`; `_Commands.sessions` и `_Commands.whoami`:

```python
    @staticmethod
    def sessions(args: argparse.Namespace) -> int:
        found = asyncio.run(list_sessions())
        host = _host_name()
        passports = asyncio.run(passport.collect(found, host=host))
        if args.as_json:
            print(
                json.dumps(
                    {"sessions": [passport.as_dict(p) for p in passports]}, ensure_ascii=False
                )
            )
            return 0
        if not passports:
            print("Живых сессий нет.")
            return 0
        print("\n\n".join(passport.as_text(p) for p in passports))
        return 0
```

`whoami`: то же через `passport.build`/`collect` для одной сессии, `--json` → `{"session": as_dict(p)}`, текст → `as_text(p)`.

```python
def _host_name() -> str:
    """`host` из конфига, если он есть и читается; иначе пусто — локальные формы."""
    config_path = paths.config_file()
    if not config_path.is_file():
        return ""
    try:
        return load_config(config_path).host
    except (ValueError, KeyError, OSError):
        return ""
```

Удалить `_as_dict` из `cli.py` (grep — других вызовов нет после правки).

Обновить `tests/test_cli.py`: тесты `sessions`/`whoami` подменяют `cli.list_sessions`/`cli.find_enclosing` — оставить; добавить `monkeypatch.setattr(cli.passport.worktrees, "inspect", fake_none)` там, где `collect` полез бы в git по несуществующему `/repos/oms` (или убедиться, что `inspect` на несуществующем пути возвращает `None` без исключения — он возвращает `None` при ненулевом коде git, так и есть, но это внешний вызов в юнит-тесте; подменяй).

- [ ] **Step 5: Гейт и коммит**

Run: `make check`
Expected: зелёный.

```bash
git add clauderc/passport.py clauderc/cli.py tests/test_passport.py tests/test_cli.py
git commit -m "feat: паспорт сессии — все входы разом, один рендер на CLI и JSON

Карточка носила только имя tmux, CLI — подсадку для своей машины, агенту
не доставалось ничего. Паспорт собирает ярлык, каталог, ветку, ссылку,
подсадку с учётом host и префикс claude-rc для агента из одной структуры."
```

---

### Task 3: Карточка бота через паспорт

**Files:**
- Modify: `clauderc/bot.py` (`_fresh_text`, `_list_item`, `_attach_line`, `_link_line`, `_uptime`, `show_chats`, `start_session`, стартовый список в `main`)
- Test: `tests/test_bot_cards.py`

**Interfaces:**
- Consumes: `passport.build`, `passport.as_html`.
- Produces: `bot._session_card(session, host, tree) -> str` — единственный рендер карточки сессии в боте.

- [ ] **Step 1: Тест**

В `tests/test_bot_cards.py` найди тесты `_fresh_text`/`_list_item`/`_attach_line` (grep). Замени их на:

```python
def test_session_card_is_the_passport_html() -> None:
    session = RemoteSession(
        name="oms@x", tmux_name="session_01ABC", cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC", created_at=int(time.time()), label="oms@x",
    )
    text = _session_card(session, host="m1", tree=None)
    assert "<b>oms@x</b>" in text
    assert "ssh m1 -t" in text
    assert "claude-rc --host m1" in text
```

- [ ] **Step 2: Убедиться, что падает**

Run: `uv run pytest tests/test_bot_cards.py -q`
Expected: FAIL — `_session_card` нет.

- [ ] **Step 3: Реализовать**

В `bot.py`:

```python
def _session_card(session: RemoteSession, *, host: str, tree: Worktree | None) -> str:
    return passport.as_html(passport.build(session, host=host, tree=tree))
```

Удалить `_fresh_text`, `_list_item`, `_attach_line`, `_link_line`, `_uptime` (проверь grep: `_uptime` может использоваться в `_tree_text` — тогда оставь `_uptime`). Все места, где они звались (`start_session` — «Уже поднята» и финальная карточка; `show_chats`; стартовый список в `main`; `told(_fresh_text(session))`), переводятся на `_session_card(session, host=config.host, tree=trees.get(real))`. В `start_session` финальная карточка: `f"✅ Сессия поднята\n{_session_card(session, host=config.host, tree=None)}"`.

- [ ] **Step 4: Гейт и коммит**

Run: `make check` → зелёный.

```bash
git add clauderc/bot.py tests/test_bot_cards.py
git commit -m "refactor(bot): карточка сессии — паспорт в HTML

Три отдельных рендера в боте расходились с CLI; теперь источник один."
```

---

### Task 4: `start --name`: ярлык и ветка из имени

**Files:**
- Modify: `clauderc/worktrees.py` (`branch_for`, `label`)
- Modify: `clauderc/cli.py` (`start` парсер, `_start`)
- Test: `tests/test_worktrees.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `worktrees.branch_for(name: str) -> str` (`wt/<slug>`), `worktrees.label(path: Path, *, name: str | None = None) -> str`, CLI `start --name`, `cli._start(..., name: str | None = None, new_worktree: bool = False)`.

- [ ] **Step 1: Тесты**

`tests/test_worktrees.py`:

```python
def test_branch_for_slugs_the_name() -> None:
    assert worktrees.branch_for("MCP fix!") == "wt/mcp-fix"
    assert worktrees.branch_for("...") == "wt/wt"


async def test_label_prefers_the_given_name(repo: Path) -> None:
    # `repo` — фикстура настоящего временного git-репозитория из этого файла
    assert (await worktrees.label(repo, name="mcp-fix")).endswith("@mcp-fix")
    assert "@" in await worktrees.label(repo)


async def test_label_uses_name_for_non_git_dir(tmp_path: Path) -> None:
    assert await worktrees.label(tmp_path, name="x") == f"{tmp_path.name}@x"
    assert await worktrees.label(tmp_path) == tmp_path.name
```

`tests/test_cli.py`:

```python
def test_start_name_becomes_label_and_new_worktree_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    async def fake_ensure(repo: Path, branch: str, root: Path) -> Path:
        seen["branch"] = branch
        return tmp_path

    async def fake_label(path: Path, *, name: str | None = None) -> str:
        return f"oms@{name}"

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        seen["label"] = label
        return _session()

    monkeypatch.setattr(cli.worktrees, "ensure", fake_ensure)
    monkeypatch.setattr(cli.worktrees, "label", fake_label)
    monkeypatch.setattr(cli, "launch", fake_launch)
    monkeypatch.setattr(cli.paths, "config_file", lambda: tmp_path / "missing.toml")
    # --new-worktree без --branch — ветка выводится из имени
    _write_config(tmp_path)  # хелпер этого файла, пишущий валидный config.toml; см. существующие тесты --branch
    assert cli.main(["start", str(tmp_path), "--new-worktree", "--name", "MCP fix"]) == 0
    assert seen["branch"] == "wt/mcp-fix"
    assert seen["label"] == "oms@MCP fix"
```

Если хелпера `_write_config` в файле нет — посмотри, как существующие тесты `start --branch` подсовывают конфиг, и сделай так же.

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_worktrees.py -k "branch_for or label" tests/test_cli.py -k start_name -q`
Expected: FAIL.

- [ ] **Step 3: Реализовать**

`clauderc/worktrees.py`:

```python
def branch_for(name: str) -> str:
    """Ветка для параллельной сессии, названной человеком: `wt/<slug>`."""
    return "wt/" + slug(name)


async def label(path: Path, *, name: str | None = None) -> str:
    """Ярлык сессии: репозиторий и имя (если дали) или ветка одной строкой. ..."""
    info = await inspect(path)
    repo = info.repo if info is not None else path.name
    if name:
        return f"{repo}@{name}"
    return f"{repo}@{info.branch}" if info is not None else path.name
```

`clauderc/cli.py`, парсер `start`:

```python
    start.add_argument("--name", help="имя сессии: ярлык repo@name и ветка wt/<name> для --new-worktree")
    start.add_argument(
        "--new-worktree", action="store_true", dest="new_worktree",
        help="отдельный worktree; ветка — из --name, иначе по времени",
    )
```

`_Commands.start`: проверка конфига, которая сейчас под `if args.branch`, становится `if args.branch or args.new_worktree`. `_start` получает `name=args.name, new_worktree=args.new_worktree`:

```python
async def _start(target, branch, resume, *, pull=False, permission_mode=None,
                 name: str | None = None, new_worktree: bool = False) -> RemoteSession:
    ...
    if new_worktree and not branch:
        branch = worktrees.branch_for(name) if name else worktrees.generate_branch()
    cwd = target
    if branch:
        ...
    label = await worktrees.label(cwd, name=name)
```

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/worktrees.py clauderc/cli.py tests/test_worktrees.py tests/test_cli.py
git commit -m "feat(cli): start --name — имя сессии вместо слага с датой

Сессия рождалась без имени, и приложение показывало слаг каталога с
меткой времени. Имя уходит в ярлык, в -n и, для нового worktree, в ветку
wt/<slug>."
```

---

### Task 5: Шаг «имя сессии» в боте

**Files:**
- Modify: `clauderc/bot.py` (`LaunchRequest`, `ask_name`, `on_text_reply` вместо `on_branch_reply`, `offer_start`, `on_nav`, `on_tree_start`, `_pop_resume_group`, `on_resume`)
- Test: `tests/test_bot_cards.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class LaunchRequest:
      target: Path
      branch: str | None = None
      resume: str | None = None
      name: str | None = None
      new_worktree: bool = False
      mode: str | None = None   # задача 14
  ```
  Заменяет `ResumeChoice` (кортеж). `resume_pending: dict[str, tuple[str, LaunchRequest]]`.
  `_name_prompt() -> tuple[str, ForceReply]` — текст и разметка запроса имени.
  `_apply_name(request: LaunchRequest, text: str) -> LaunchRequest` — чистая: `-` или пусто → без имени; для `new_worktree` без `branch` подставляет `worktrees.branch_for(name)` либо `generate_branch()`.

- [ ] **Step 1: Тесты**

В `tests/test_bot_cards.py` заменить тест `_pop_resume_group` на `LaunchRequest` и добавить:

```python
def test_apply_name_skips_on_dash() -> None:
    req = LaunchRequest(target=Path("/r"), new_worktree=True)
    out = _apply_name(req, "-")
    assert out.name is None
    assert out.branch is not None and out.branch.startswith("wt/")


def test_apply_name_derives_branch_for_new_worktree() -> None:
    out = _apply_name(LaunchRequest(target=Path("/r"), new_worktree=True), "MCP fix")
    assert out.name == "MCP fix"
    assert out.branch == "wt/mcp-fix"


def test_apply_name_keeps_explicit_branch() -> None:
    out = _apply_name(LaunchRequest(target=Path("/r"), branch="feat/x"), "x")
    assert out.branch == "feat/x"


def test_name_prompt_is_a_force_reply() -> None:
    text, markup = _name_prompt()
    assert "ответом" in text
    assert markup.force_reply is True and markup.selective is True
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_bot_cards.py -q` → FAIL (нет `LaunchRequest`, `_apply_name`, `_name_prompt`).

- [ ] **Step 3: Реализовать**

Модульный уровень `bot.py`:

```python
@dataclass(frozen=True)
class LaunchRequest:
    target: Path
    branch: str | None = None
    resume: str | None = None
    name: str | None = None
    new_worktree: bool = False


MAX_SESSION_NAME_LEN = 40


def _name_prompt() -> tuple[str, ForceReply]:
    return (
        "Как назвать сессию? Пришли имя <b>ответом на это сообщение</b> "
        "или <code>-</code>, чтобы обойтись веткой.",
        ForceReply(force_reply=True, selective=True, input_field_placeholder="имя сессии или -"),
    )


def _apply_name(request: LaunchRequest, text: str) -> LaunchRequest:
    name = text.strip()[:MAX_SESSION_NAME_LEN]
    if name == "-":
        name = ""
    branch = request.branch
    if request.new_worktree and not branch:
        branch = worktrees.branch_for(name) if name else worktrees.generate_branch()
    return dataclasses.replace(request, name=name or None, branch=branch)
```

`_pop_resume_group` меняет тип значения на `tuple[str, LaunchRequest]`; удалить алиас `ResumeChoice`.

Внутри `main`:

```python
    # Ключ — id сообщения с запросом имени сессии (ForceReply), значение — что
    # поднимать. Как у ветки для Sync: ответ привязан через reply_to_message,
    # и чужой текст в имя не попадёт.
    name_pending: dict[int, LaunchRequest] = {}

    async def ask_name(message: Message, request: LaunchRequest) -> None:
        text, markup = _name_prompt()
        prompt = await message.answer(text, parse_mode="HTML", reply_markup=markup)
        name_pending[prompt.message_id] = request
```

`start_session(message, request: LaunchRequest)` — сигнатура меняется на объект; внутри `branch = request.branch`, `resume = request.resume`, и `launch(await worktrees.label(cwd, name=request.name), ...)`.

Куда встаёт шаг имени:
- `offer_start`: ветка `branch is not None` → `ask_name(message, LaunchRequest(target, branch=branch))`; пустая история → `ask_name(message, LaunchRequest(target))`; при истории кнопка «New session» кладёт `LaunchRequest(target)` и `on_resume` для запроса без `resume` зовёт `ask_name`, с `resume` — сразу `start_session`.
- `on_nav` `newwt`: `ask_name(message, LaunchRequest(state.cwd, new_worktree=True))`.
- `on_tree_start`: `ask_name(message, LaunchRequest(path))`.
- `on_died` Resume: `LaunchRequest(Path(died.cwd), resume="last")` → `start_session` без вопроса.

Обработчик ответа: переименовать `on_branch_reply` в `on_text_reply`, первым делом:

```python
        request = name_pending.pop(reply.message_id, None)
        if request is not None:
            await start_session(message, _apply_name(request, message.text or ""))
            return
```

и дальше прежняя логика ветки для Sync.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/bot.py tests/test_bot_cards.py
git commit -m "feat(bot): шаг «имя сессии» перед запуском

Имя спрашивается ответом на ForceReply, как ветка у Sync: чужой текст
в него не попадёт. «-» пропускает шаг — тогда ярлык и ветка как раньше."
```

---

### Task 6: `actions.rename` + CLI `rename`

**Files:**
- Create: `clauderc/actions.py`
- Modify: `clauderc/cli.py` (парсер `rename`, `_Commands.rename`, общий `_one(target)` для команд с целью)
- Test: `tests/test_actions.py` (новый), `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  # actions.py
  LABEL_OPTION = "@rc_label"
  @dataclass(frozen=True)
  class RenameResult:
      label: str          # новый ярлык repo@name
      app_renamed: bool   # приложение приняло /rename
  async def send(session: RemoteSession, text: str, *, enter: bool = True) -> None
  async def tail(session: RemoteSession, lines: int = 5) -> str
  async def rename(session: RemoteSession, name: str, *, settle_s: float = 1.5) -> RenameResult
  # cli.py
  class _Ambiguous(Exception): sessions: list[RemoteSession]
  async def _one(target: str) -> RemoteSession | None   # resolve → одна или _Ambiguous
  ```
  `send`/`tail` определяются здесь (они нужны `rename`), CLI `send` — задача 12.

- [ ] **Step 1: Тесты `tests/test_actions.py`**

```python
from collections.abc import Awaitable, Callable

import pytest
from clauderc import actions, remote
from clauderc.remote import RemoteSession

Handler = Callable[..., tuple[int, str]]


def _stub(handler: Handler) -> Callable[..., Awaitable[tuple[int, str]]]:
    async def run(*args: str, check: bool = True) -> tuple[int, str]:
        return handler(*args)

    return run


def _session(tmux_name: str = "session_01ABC", label: str = "oms@old") -> RemoteSession:
    return RemoteSession(
        name=label, tmux_name=tmux_name, cwd="/repos/oms",
        url="https://claude.ai/code/session_01ABC", created_at=0, label=label,
    )


async def test_send_types_literally_then_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (calls.append(a), (0, ""))[1]))

    await actions.send(_session(), "/mcp")

    # -l: иначе tmux принял бы «Enter» или «C-c» в тексте за имя клавиши.
    # Target — `=имя:`, здесь ждут target-pane.
    assert calls == [
        ("send-keys", "-t", "=session_01ABC:", "-l", "/mcp"),
        ("send-keys", "-t", "=session_01ABC:", "Enter"),
    ]


async def test_send_without_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (calls.append(a), (0, ""))[1]))
    await actions.send(_session(), "1", enter=False)
    assert calls == [("send-keys", "-t", "=session_01ABC:", "-l", "1")]


async def test_tail_returns_last_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    pane = "\n".join(f"line{i}" for i in range(10)) + "\n\n\n"
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, pane)))
    assert await actions.tail(_session(), lines=3) == "line7\nline8\nline9"


async def test_rename_sets_label_and_reports_app_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a)
        if a[0] == "capture-pane":
            return 0, "❯ \n"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    result = await actions.rename(_session(), "new", settle_s=0)

    assert result.label == "oms@new"
    assert result.app_renamed is True
    assert ("set-option", "-t", "=session_01ABC:", "@rc_label", "oms@new") in calls
    assert ("send-keys", "-t", "=session_01ABC:", "-l", "/rename new") in calls


async def test_rename_notices_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(*a: str) -> tuple[int, str]:
        if a[0] == "capture-pane":
            return 0, "Unknown slash command: /rename\n❯ \n"
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    result = await actions.rename(_session(), "new", settle_s=0)
    assert result.app_renamed is False


async def test_rename_keeps_repo_from_old_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, "")))
    result = await actions.rename(_session(label="city-manager@wt/2026"), "fix", settle_s=0)
    assert result.label == "city-manager@fix"


async def test_rename_falls_back_to_directory_when_no_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, "")))
    result = await actions.rename(_session(label=""), "fix", settle_s=0)
    assert result.label == "oms@fix"


async def test_rename_raises_when_tmux_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (1, "no such session")))
    with pytest.raises(actions.ActionError, match="no such session"):
        await actions.rename(_session(), "new", settle_s=0)
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_actions.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `clauderc/actions.py`**

```python
"""Действия над живой сессией: набрать текст, прочитать хвост, переименовать,
перезапустить. Ни одно не гасит сессию само — гашение идёт через того, кому
его передали (в боте это Watcher), см. `restart`.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

from clauderc import remote
from clauderc.remote import RemoteSession

LABEL_OPTION = "@rc_label"
_UNKNOWN_COMMAND = re.compile(r"Unknown (?:slash )?command", re.IGNORECASE)


class ActionError(RuntimeError):
    """tmux отказал: сессии нет или команда не прошла."""


@dataclass(frozen=True)
class RenameResult:
    label: str
    app_renamed: bool


def _pane(session: RemoteSession) -> str:
    return f"={session.tmux_name}:"


async def send(session: RemoteSession, text: str, *, enter: bool = True) -> None:
    """Набирает `text` в панель как есть (`-l`) и, если надо, жмёт Enter.

    Без `-l` tmux трактует слова вроде `Enter` и `C-c` как имена клавиш. Enter
    — отдельным вызовом: это и есть клавиша.
    """
    code, out = await remote._run("send-keys", "-t", _pane(session), "-l", text, check=False)
    if code != 0:
        raise ActionError(out.strip() or f"send-keys: код {code}")
    if enter:
        await remote._run("send-keys", "-t", _pane(session), "Enter", check=False)


async def tail(session: RemoteSession, lines: int = 5) -> str:
    """Последние непустые строки панели."""
    code, pane = await remote._run("capture-pane", "-p", "-J", "-t", _pane(session), check=False)
    if code != 0:
        raise ActionError(pane.strip() or "capture-pane failed")
    rows = pane.rstrip("\n").split("\n")
    return "\n".join(rows[-lines:]) if lines > 0 else ""


def _repo_of(session: RemoteSession) -> str:
    label = session.label or ""
    if "@" in label:
        return label.split("@", 1)[0]
    return os.path.basename(session.cwd.rstrip(os.sep)) or session.name


async def rename(session: RemoteSession, name: str, *, settle_s: float = 1.5) -> RenameResult:
    """Новый ярлык `repo@name`: в tmux всегда, в приложении — если claude знает /rename.

    Ветку и каталог не трогает: они git, а не название.
    """
    label = f"{_repo_of(session)}@{name.strip()}"
    code, out = await remote._run(
        "set-option", "-t", _pane(session), LABEL_OPTION, label, check=False
    )
    if code != 0:
        raise ActionError(out.strip() or f"set-option: код {code}")
    await send(session, f"/rename {name.strip()}")
    await asyncio.sleep(settle_s)
    recent = await tail(session, lines=8)
    return RenameResult(label=label, app_renamed=_UNKNOWN_COMMAND.search(recent) is None)
```

Использование `remote._run` из другого модуля — сознательно: подмена в тестах тогда одна на всё (`monkeypatch.setattr(remote, "_run", ...)`). Добавь в `remote.py` над `_run` комментарий, что `actions` и `state_probe` тоже через него ходят.

- [ ] **Step 4: CLI `rename`**

Парсер:

```python
    rename_cmd = sub.add_parser("rename", help="переименовать сессию: ярлык в tmux и /rename в приложении")
    rename_cmd.add_argument("target", help="ярлык, каталог или session_…")
    rename_cmd.add_argument("name")
```

Общий резолвер цели (используется `rename`, потом `send`, `restart`, `connect`):

```python
class _Ambiguous(Exception):
    def __init__(self, sessions: list[RemoteSession]) -> None:
        super().__init__("ambiguous target")
        self.sessions = sessions


async def _one(target: str) -> RemoteSession | None:
    matches = await resolve(target)
    if len(matches) > 1:
        raise _Ambiguous(matches)
    return matches[0] if matches else None


def _print_ambiguous(exc: _Ambiguous) -> int:
    print("Таких сессий несколько — назови id:", file=sys.stderr)
    for session in exc.sessions:
        print(f"  {session.tmux_name}\t{session.cwd}", file=sys.stderr)
    return EXIT_FAILED
```

`_StopAmbiguous` заменить на `_Ambiguous` (поправить `_stop` и `_Commands.stop`).

```python
    @staticmethod
    def rename(args: argparse.Namespace) -> int:
        try:
            session = asyncio.run(_one(args.target))
        except _Ambiguous as exc:
            return _print_ambiguous(exc)
        if session is None:
            print(f"Сессия не найдена: {args.target}", file=sys.stderr)
            return EXIT_FAILED
        try:
            result = asyncio.run(actions.rename(session, args.name))
        except actions.ActionError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        print(result.label)
        if not result.app_renamed:
            print("Приложение Claude имя не подхватило: у этой версии claude нет /rename.", file=sys.stderr)
        return 0
```

Тест в `tests/test_cli.py`:

```python
def test_rename_prints_new_label(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    async def fake_rename(session: RemoteSession, name: str) -> actions.RenameResult:
        return actions.RenameResult(label=f"oms@{name}", app_renamed=False)

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.actions, "rename", fake_rename)
    assert cli.main(["rename", "oms", "fix"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "oms@fix"
    assert "/rename" in err
```

- [ ] **Step 5: Гейт и коммит**

```bash
git add clauderc/actions.py clauderc/cli.py tests/test_actions.py tests/test_cli.py
git commit -m "feat: rename — ярлык в tmux и /rename в приложении

Имя сессии должно меняться и после запуска. Ярлык меняется всегда; если
claude этой версии /rename не знает, CLI об этом говорит, а не молчит."
```

---

### Task 7: `--host`: тот же CLI, выполненный на другой машине

**Files:**
- Create: `clauderc/proxy.py`
- Modify: `clauderc/cli.py` (`main`, `_parser`)
- Test: `tests/test_proxy.py` (новый), `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  # proxy.py
  HOST_ENV = "CLAUDE_RC_HOST"
  LOCAL_ONLY = frozenset({"bot", "forward", "update"})
  TTY_COMMANDS = frozenset({"connect", "start", "setup"})
  PATH_COMMANDS = frozenset({"start", "whoami", "sync"})
  def strip_host(argv: list[str]) -> tuple[str | None, list[str]]
  def command_of(argv: list[str]) -> str | None
  def relative_paths(argv: list[str]) -> list[str]
  def remote_argv(host: str, args: list[str], *, tty: bool) -> list[str]
  def exec_remote(host: str, args: list[str]) -> None   # os.execvp
  def run_remote(host: str, args: list[str], *, timeout_s: float = 30.0) -> tuple[int, str]
  ```

- [ ] **Step 1: Тесты `tests/test_proxy.py`**

```python
import pytest
from clauderc import proxy


def test_strip_host_handles_both_spellings() -> None:
    assert proxy.strip_host(["--host", "m1", "sessions"]) == ("m1", ["sessions"])
    assert proxy.strip_host(["--host=m1", "sessions", "--json"]) == ("m1", ["sessions", "--json"])
    assert proxy.strip_host(["sessions"]) == (None, ["sessions"])


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
    assert argv[:3] == ["ssh", "-T", "m1"]
    command = argv[3]
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
```

Тесты в `tests/test_cli.py`:

```python
def test_host_flag_proxies_through_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.proxy, "exec_remote", lambda host, args: seen.append([host, *args]))
    cli.main(["--host", "m1", "sessions", "--json"])
    assert seen == [["m1", "sessions", "--json"]]


def test_host_env_is_used_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setenv(proxy.HOST_ENV, "m3")
    monkeypatch.setattr(cli.proxy, "exec_remote", lambda host, args: seen.append(host))
    cli.main(["sessions"])
    assert seen == ["m3"]


@pytest.mark.parametrize("command", ["bot", "update"])
def test_host_refuses_local_only_commands(monkeypatch, capsys, command: str) -> None:
    monkeypatch.setattr(cli.proxy, "exec_remote", lambda host, args: pytest.fail("proxied"))
    assert cli.main(["--host", "m1", command]) == 2
    assert command in capsys.readouterr().err


def test_host_refuses_relative_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.proxy, "exec_remote", lambda host, args: pytest.fail("proxied"))
    assert cli.main(["--host", "m1", "start", "."]) == 2
    assert "абсолютн" in capsys.readouterr().err
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_proxy.py tests/test_cli.py -k host -q` → FAIL.

- [ ] **Step 3: Реализовать `clauderc/proxy.py`**

```python
"""`claude-rc --host m1 …` — та же команда, выполненная на другой машине.

Транспорт — ssh и только он: аутентификация, шифрование и ключи у него уже
есть. Процесс замещается через execvp: коды возврата, stdout и Ctrl+C
достаются ssh без нашего посредничества. Никакой роли «управляющий» у машины
нет — любая с claude-rc может звать любую другую.
"""

from __future__ import annotations

import os
import shlex
import subprocess

HOST_ENV = "CLAUDE_RC_HOST"
# bot — умер бы вместе с ssh-сессией; update — гасит приложение на той машине
# и должен идти из её Терминала; forward — исполняется здесь по определению.
LOCAL_ONLY = frozenset({"bot", "forward", "update"})
# Команды, где человек отвечает на вопрос в терминале.
TTY_COMMANDS = frozenset({"connect", "start", "setup"})
# Команды с путём в позиционном аргументе: относительный путь означал бы
# каталог этой машины, а исполняется команда на той.
PATH_COMMANDS = frozenset({"start", "whoami", "sync"})
# Опции этих команд, у которых есть значение — чтобы не принять его за путь.
_VALUED_OPTIONS = frozenset({"--branch", "--resume", "--permission-mode", "--name", "--mode"})
# Неинтерактивный ssh не читает .zshrc; uv tool кладёт бинарь в ~/.local/bin.
_PATH_PREFIX = 'export PATH="$HOME/.local/bin:$PATH"; '


def strip_host(argv: list[str]) -> tuple[str | None, list[str]]:
    host: str | None = None
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--host":
            host = next(it, None)
        elif arg.startswith("--host="):
            host = arg.removeprefix("--host=")
        else:
            rest.append(arg)
    return host, rest


def command_of(argv: list[str]) -> str | None:
    return next((a for a in argv if not a.startswith("-")), None)


def relative_paths(argv: list[str]) -> list[str]:
    command = command_of(argv)
    if command not in PATH_COMMANDS:
        return []
    positionals: list[str] = []
    skip = False
    for arg in argv[argv.index(command) + 1 :]:
        if skip:
            skip = False
            continue
        if arg in _VALUED_OPTIONS:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        positionals.append(arg)
    if not positionals and command != "sync":
        positionals = ["."]
    return [p for p in positionals if not (os.path.isabs(p) or p.startswith("~"))]


def remote_argv(host: str, args: list[str], *, tty: bool) -> list[str]:
    command = _PATH_PREFIX + shlex.join(["claude-rc", *args])
    return ["ssh", "-t" if tty else "-T", host, command]


def exec_remote(host: str, args: list[str]) -> None:
    argv = remote_argv(host, args, tty=command_of(args) in TTY_COMMANDS)
    os.execvp(argv[0], argv)


def run_remote(host: str, args: list[str], *, timeout_s: float = 30.0) -> tuple[int, str]:
    """Выполнить и вернуть вывод — для forward, которому нужен sessions --json той стороны."""
    argv = remote_argv(host, args, tty=False)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return 1, f"ssh {host} не ответил за {timeout_s:.0f}с"
    return done.returncode, done.stdout if done.returncode == 0 else done.stderr
```

`clauderc/cli.py`, `main`:

```python
def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    host, rest = proxy.strip_host(raw)
    host = host or os.environ.get(proxy.HOST_ENV) or None
    command = proxy.command_of(rest)
    if host and command != "forward":
        if command in proxy.LOCAL_ONLY:
            print(f"{command} при --host не выполняется: {_LOCAL_ONLY_WHY[command]}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        relative = proxy.relative_paths(rest)
        if relative:
            print(
                f"При --host путь должен быть абсолютным или от ~: {', '.join(relative)}",
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        proxy.exec_remote(host, rest)
        return 0  # execvp не возвращается; сюда попадает только тест с подменой
    parser = _parser()
    args = parser.parse_args(rest)
    args.host = host  # forward читает отсюда (задача 16)
    ...


_LOCAL_ONLY_WHY = {
    "bot": "бот умер бы вместе с ssh-сессией — запусти его на той машине",
    "update": "обновление гасит приложение той машины и должно идти из её Терминала",
    "forward": "туннель строится с этой стороны",
}
```

`_parser()`: `parser.add_argument("--host", help="выполнить команду на этой машине по ssh")` — чтобы `--help` его показывал; из `rest` он уже вырезан.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/proxy.py clauderc/cli.py tests/test_proxy.py tests/test_cli.py
git commit -m "feat(cli): --host — та же команда, выполненная по ssh на другой машине

Стюард на второй машине изобретал ssh-команды сам. Теперь claude-rc
--host m1 <cmd> замещает процесс на ssh; конфиг на клиенте не читается,
bot/update/forward не проксируются, относительные пути отклоняются —
они означали бы каталог не той машины."
```

---

### Task 8: `state_probe`: состояние по панели и хвост

**Files:**
- Create: `clauderc/state_probe.py`, `tests/fixtures/panes/{idle,working,needs_input,trust,garbage}.txt`
- Test: `tests/test_state_probe.py`

**Interfaces:**
- Produces:
  ```python
  class State(StrEnum): WORKING="working"; IDLE="idle"; NEEDS_INPUT="needs_input"; DEAD="dead"; UNKNOWN="unknown"
  @dataclass(frozen=True)
  class SessionState:
      state: State
      last_lines: tuple[str, ...]
      listening: tuple[int, ...] = ()
  def classify(pane: str) -> State
  async def probe(tmux_name: str, *, lines: int = 5) -> SessionState   # порты — задача 9
  ```

- [ ] **Step 1: Снять настоящие снимки панели**

На машине с tmux и claude поднять сессию (`claude-rc start /tmp/x` или руками `tmux new -d -s probe -x 120 -y 40 'claude'`), и в каждом из состояний выполнить `tmux capture-pane -p -J -t =probe: > tests/fixtures/panes/<state>.txt`:
- `idle.txt` — пустая рамка ввода после ответа;
- `working.txt` — во время генерации (виден спиннер и «esc to interrupt»);
- `needs_input.txt` — диалог подтверждения инструмента («Do you want to proceed?» с нумерованными пунктами; получить его можно попросив `claude` в режиме `manual` выполнить `ls` через Bash);
- `trust.txt` — диалог доверия каталогу (незнакомый каталог);
- `garbage.txt` — произвольный текст, например `seq 1 30`.
Снимки — часть теста: ломается TUI, ломается тест, и это правильно. Если снять нет возможности (нет claude на машине исполнителя), написать снимки по образцам ниже и пометить в коммите, что они синтетические.

Образцы (что должно быть в файлах, если снимаются руками):
- idle: последняя непустая строка начинается с `❯` и после него пусто, либо строка вида `│ > ` внутри рамки;
- working: строка с одним из глифов `✻ ✶ ✳ ✢ · ✽` и текстом `…ing…`, и строка `esc to interrupt`;
- needs_input: `Do you want to proceed?` и строки `❯ 1. Yes` / `2. No`;
- trust: `Yes, I trust this folder`.

- [ ] **Step 2: Тесты `tests/test_state_probe.py`**

```python
from pathlib import Path

import pytest
from clauderc import remote, state_probe
from clauderc.state_probe import State

FIXTURES = Path(__file__).parent / "fixtures" / "panes"


def _pane(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("idle", State.IDLE),
        ("working", State.WORKING),
        ("needs_input", State.NEEDS_INPUT),
        ("trust", State.NEEDS_INPUT),
        ("garbage", State.UNKNOWN),
    ],
)
def test_classify_real_panes(fixture: str, expected: State) -> None:
    assert state_probe.classify(_pane(fixture)) is expected


def test_dialog_wins_over_prompt() -> None:
    # Диалог рисуется поверх рамки ввода — пустой ❯ ниже не делает сессию idle.
    pane = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n\n❯ \n"
    assert state_probe.classify(pane) is State.NEEDS_INPUT


async def test_probe_reports_dead_when_tmux_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 1, "can't find session"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "listening_ports", _no_ports)
    result = await state_probe.probe("session_X")
    assert result.state is State.DEAD
    assert result.last_lines == ()


async def test_probe_keeps_last_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 0, _pane("idle")

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "listening_ports", _no_ports)
    result = await state_probe.probe("session_X", lines=3)
    assert result.state is State.IDLE
    assert len(result.last_lines) == 3


async def _no_ports(tmux_name: str) -> tuple[int, ...]:
    return ()
```

- [ ] **Step 3: Убедиться, что падают**

Run: `uv run pytest tests/test_state_probe.py -q` → `ModuleNotFoundError`.

- [ ] **Step 4: Реализовать `clauderc/state_probe.py`**

```python
"""Состояние сессии, каким его видно на панели.

У tmux нет событий, у claude нет API статуса сессии. Единственный источник —
последние строки панели, поэтому всё здесь — наблюдение, а не знание, и
рядом всегда отдаётся сырой хвост: читатель должен иметь возможность не
поверить ярлыку. Шаблон, сломанный обновлением claude, даёт UNKNOWN, а не
ложный IDLE; на каждое состояние есть тест с настоящим снимком панели.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from clauderc import remote

_TAIL_FOR_CLASSIFY = 12

_NEEDS_INPUT = (
    re.compile(r"Yes, I trust this folder"),
    re.compile(r"Do you want to"),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"^\s*❯?\s*\d+[.)]\s+\S", re.MULTILINE),
)
_WORKING = (
    re.compile(r"esc to interrupt"),
    re.compile(r"^[✻✶✳✢·✽]\s+\S+ing…", re.MULTILINE),
)
_IDLE = (
    re.compile(r"^\s*❯\s*$", re.MULTILINE),
    re.compile(r"^\s*│\s*>\s*│?\s*$", re.MULTILINE),
)


class State(StrEnum):
    WORKING = "working"
    IDLE = "idle"
    NEEDS_INPUT = "needs_input"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionState:
    state: State
    last_lines: tuple[str, ...]
    listening: tuple[int, ...] = ()


def _tail(pane: str, lines: int) -> tuple[str, ...]:
    rows = [row.rstrip() for row in pane.rstrip("\n").split("\n")]
    return tuple(rows[-lines:]) if lines > 0 else ()


def classify(pane: str) -> State:
    """Порядок важен: диалог рисуется поверх рамки ввода, спиннер — над ней."""
    recent = "\n".join(_tail(pane, _TAIL_FOR_CLASSIFY))
    if any(p.search(recent) for p in _NEEDS_INPUT):
        return State.NEEDS_INPUT
    if any(p.search(recent) for p in _WORKING):
        return State.WORKING
    if any(p.search(recent) for p in _IDLE):
        return State.IDLE
    return State.UNKNOWN


async def listening_ports(tmux_name: str) -> tuple[int, ...]:
    return ()  # задача 9


async def probe(tmux_name: str, *, lines: int = 5) -> SessionState:
    code, pane = await remote._run("capture-pane", "-p", "-J", "-t", f"={tmux_name}:", check=False)
    if code != 0:
        return SessionState(state=State.DEAD, last_lines=())
    return SessionState(
        state=classify(pane),
        last_lines=_tail(pane, lines),
        listening=await listening_ports(tmux_name),
    )
```

После снятия настоящих снимков подгони регэкспы под них — и только под них: тест на фикстуре первичен, регэксп вторичен.

- [ ] **Step 5: Гейт и коммит**

```bash
git add clauderc/state_probe.py tests/test_state_probe.py tests/fixtures/panes
git commit -m "feat: состояние сессии по панели — idle/working/needs_input/dead/unknown

API статуса у claude нет; состояние выводится из последних строк панели
и всегда отдаётся вместе с ними. Неузнанная панель — unknown, а не
ложный idle. Шаблоны проверены на снимках настоящих панелей."
```

---

### Task 9: Порты, которые слушает сессия

**Files:**
- Modify: `clauderc/state_probe.py` (`listening_ports`, `_exec`, `_descendants`, `_parse_lsof`)
- Test: `tests/test_state_probe.py`

**Interfaces:**
- Produces: `listening_ports(tmux_name) -> tuple[int, ...]` (отсортированные уникальные), `_exec(argv, timeout_s) -> tuple[int, str]`.

- [ ] **Step 1: Тесты**

```python
def test_parse_lsof_extracts_ports() -> None:
    out = "p123\nn*:3000\np456\nn127.0.0.1:5173\nn[::1]:5173\n"
    assert state_probe._parse_lsof(out) == (3000, 5173)


async def test_listening_ports_walks_children(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        assert a == ("list-panes", "-t", "=session_X:", "-F", "#{pane_pid}")
        return 0, "100\n"

    calls: list[list[str]] = []

    async def exec_(argv: list[str], timeout_s: float = 5.0) -> tuple[int, str]:
        calls.append(argv)
        if argv[0] == "pgrep":
            parent = argv[-1]
            return (0, "101\n102\n") if parent == "100" else (1, "")
        assert argv[0] == "lsof"
        assert "100,101,102" in argv
        return 0, "n*:3000\n"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "_exec", exec_)
    assert await state_probe.listening_ports("session_X") == (3000,)


async def test_listening_ports_empty_when_lsof_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*a: str, check: bool = True) -> tuple[int, str]:
        return 0, "100\n"

    async def exec_(argv: list[str], timeout_s: float = 5.0) -> tuple[int, str]:
        return 127, "not found"

    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(state_probe, "_exec", exec_)
    assert await state_probe.listening_ports("session_X") == ()
```

- [ ] **Step 2: Убедиться, что падают** — `uv run pytest tests/test_state_probe.py -k ports -q`.

- [ ] **Step 3: Реализовать**

```python
import asyncio

_EXEC_TIMEOUT_S = 5.0


async def _exec(argv: list[str], timeout_s: float = _EXEC_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:  # бинаря нет
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"{argv[0]} не ответил за {timeout_s:.0f}с"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _descendants(root: str) -> list[str]:
    pids, queue = [root], [root]
    while queue:
        code, out = await _exec(["pgrep", "-P", queue.pop()])
        if code != 0:
            continue
        children = out.split()
        pids.extend(children)
        queue.extend(children)
    return pids


def _parse_lsof(out: str) -> tuple[int, ...]:
    ports: set[int] = set()
    for line in out.splitlines():
        if line.startswith("n") and ":" in line:
            tail = line.rsplit(":", 1)[1]
            if tail.isdigit():
                ports.add(int(tail))
    return tuple(sorted(ports))


async def listening_ports(tmux_name: str) -> tuple[int, ...]:
    """TCP-порты, которые слушают процессы панели. Детерминировано: pid → потомки → lsof."""
    code, out = await remote._run("list-panes", "-t", f"={tmux_name}:", "-F", "#{pane_pid}", check=False)
    root = out.strip().split("\n")[0] if code == 0 else ""
    if not root.isdigit():
        return ()
    pids = await _descendants(root)
    code, out = await _exec(
        ["lsof", "-a", "-p", ",".join(pids), "-iTCP", "-sTCP:LISTEN", "-P", "-n", "-Fn"]
    )
    return _parse_lsof(out) if code == 0 else ()
```

Убрать заглушку `listening_ports` из задачи 8.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/state_probe.py tests/test_state_probe.py
git commit -m "feat: порты, которые слушает сессия — вход для forward

pane_pid → потомки через pgrep → lsof LISTEN. Без эвристик; нет lsof —
пустой список, а не ошибка."
```

---

### Task 10: Состояние и порты — в паспорт

**Files:**
- Modify: `clauderc/passport.py` (`collect(..., probe=True)`, `as_dict`, `as_text`, `as_html`, `state_line`)
- Modify: `clauderc/cli.py` (`sessions`: флаг `--no-probe`), `clauderc/bot.py` (`show_chats` собирает через `collect`)
- Test: `tests/test_passport.py`

**Interfaces:**
- Produces: `collect(sessions, *, host, probe: bool = True)`; `Passport.state/last_lines/listening` заполнены; в `as_dict` ключи `state`, `last_lines`, `listening`; `state_line(p) -> str` (`⏳ ждёт ответа · порты 3000, 5173`).

- [ ] **Step 1: Тесты**

```python
async def test_collect_probes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return None

    async def fake_probe(tmux_name: str, *, lines: int = 5) -> state_probe.SessionState:
        return state_probe.SessionState(
            state=state_probe.State.NEEDS_INPUT, last_lines=("❯ 1. Yes",), listening=(3000,)
        )

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    monkeypatch.setattr(passport.state_probe, "probe", fake_probe)
    (p,) = await passport.collect([_session()], host="")
    assert p.state == "needs_input"
    assert p.listening == (3000,)
    d = passport.as_dict(p)
    assert d["state"] == "needs_input" and d["listening"] == [3000]
    assert d["last_lines"] == ["❯ 1. Yes"]
    assert "ждёт ответа" in passport.as_text(p) and "3000" in passport.as_text(p)


async def test_collect_can_skip_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(path: Path) -> Worktree | None:
        return None

    async def boom(tmux_name: str, *, lines: int = 5) -> state_probe.SessionState:
        raise AssertionError("probe must not be called")

    monkeypatch.setattr(passport.worktrees, "inspect", fake_inspect)
    monkeypatch.setattr(passport.state_probe, "probe", boom)
    (p,) = await passport.collect([_session()], host="", probe=False)
    assert p.state == ""


def test_state_line_words() -> None:
    base = passport.build(_session(), host="", tree=None)
    for state, word in [("idle", "свободна"), ("working", "работает"), ("needs_input", "ждёт ответа"), ("unknown", "состояние неясно")]:
        assert word in passport.state_line(dataclasses.replace(base, state=state))
    assert passport.state_line(base) == ""
```

Обнови `test_as_dict_keeps_the_old_keys` (задача 2): теперь `state`, `last_lines`, `listening` тоже в dict.

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

`passport.py`:

```python
from clauderc import state_probe

_STATE_WORDS = {
    "idle": "🟢 свободна",
    "working": "⚙️ работает",
    "needs_input": "⏳ ждёт ответа",
    "dead": "💀 мертва",
    "unknown": "❔ состояние неясно",
}


def state_line(p: Passport) -> str:
    if not p.state:
        return ""
    line = _STATE_WORDS.get(p.state, p.state)
    if p.listening:
        line += " · порты " + ", ".join(str(port) for port in p.listening)
    return line


async def collect(sessions, *, host, probe: bool = True) -> list[Passport]:
    result = []
    for session in sessions:
        tree = await worktrees.inspect(Path(session.cwd))
        p = build(session, host=host, tree=tree)
        if probe:
            observed = await state_probe.probe(session.tmux_name)
            p = dataclasses.replace(
                p, state=str(observed.state), last_lines=observed.last_lines,
                listening=observed.listening,
            )
        result.append(p)
    return result
```

В `as_dict` добавить `"state": p.state, "last_lines": list(p.last_lines), "listening": list(p.listening)`. В `as_text`/`as_html` вставить `state_line(p)` после строки ветки, и хвост панели: в `as_text` — строки `last_lines` с отступом двумя пробелами после всех входов; в `as_html` — `<pre>…</pre>` с экранированием, только если `last_lines` непусто.

CLI `sessions --no-probe` (`action="store_false", dest="probe"`) → `collect(found, host=host, probe=args.probe)`. `whoami` — `probe=False` (агент внутри сессии спрашивает про себя, состояние он и так знает).

Бот `show_chats`: `passports = await passport.collect(sessions, host=config.host)` и карточка `passport.as_html(p)` — `_session_card` из задачи 3 получает перегрузку: принимает готовый `Passport`. Проще: `_session_card(p: Passport) -> str` и вызывающие сами строят паспорт (`build` там, где состояние не нужно: «Уже поднята», финальная карточка запуска).

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/passport.py clauderc/cli.py clauderc/bot.py tests/test_passport.py
git commit -m "feat: состояние и порты в паспорте — sessions --json отдаёт всё разом

Стюарду нужен один вызов: ярлык, ссылка, состояние, хвост панели и
порты. --no-probe — когда capture-pane и lsof на каждую сессию лишние."
```

---

### Task 11: `@rc_mode`, `RemoteSession.mode`, режим `auto` по умолчанию

**Files:**
- Modify: `clauderc/remote.py` (`_FORMAT`, `_MODE_OPTION`, `RemoteSession.mode`, `list_sessions`, `launch`, `DEFAULT_PERMISSION_MODE`)
- Modify: `clauderc/passport.py` (`build`: `mode=session.mode`)
- Modify: `config.example.toml` (комментарий про `auto`)
- Test: `tests/test_remote.py`

**Interfaces:**
- Produces: `RemoteSession.mode: str = ""`, `remote.DEFAULT_PERMISSION_MODE = "auto"`, user-опция `@rc_mode`; `launch(...)` без `permission_mode` ставит `--permission-mode auto`.

- [ ] **Step 1: Тесты**

В `tests/test_remote.py` `_ROW` и все строки формата `list-sessions` получают шестую колонку (`\t` + режим). Проверь grep `\\t` по файлу: каждая строка-снимок должна иметь 5 табов. Далее:

```python
async def test_list_sessions_reads_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    row = "session_A\t/repos/oms\t1700000000\thttps://claude.ai/code/session_A\toms@x\tplan"
    monkeypatch.setattr(remote, "_run", _stub(lambda *a: (0, row)))
    (s,) = await remote.list_sessions()
    assert s.mode == "plan"


async def test_launch_defaults_to_auto_and_stores_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # Переписать существующий test_launch_without_a_permission_mode_adds_no_flag:
    # без флага режим теперь auto, и он же уходит в @rc_mode.
    created: list[tuple[str, ...]] = []
    options: list[tuple[str, ...]] = []

    def handler(*a: str) -> tuple[int, str]:
        if a[0] == "new-session":
            created.append(a)
        if a[0] == "set-option":
            options.append(a)
        if a[0] == "list-sessions":
            return 0, ""
        return 0, ""

    monkeypatch.setattr(remote, "_run", _stub(handler))
    with pytest.raises(LaunchError):
        await remote.launch("oms", "/repos/oms", timeout_s=0.05)
    assert "--permission-mode auto" in created[0][-1]
    assert ("set-option", "-t", "=rc-oms:", "@rc_mode", "auto") in options
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

`remote.py`:

```python
# Режим, с которого начинает сессия, если ни флаг, ни конфиг не сказали иначе.
# `auto` — потому что с телефона на каждый шаг не наотвечаешься, а претензии
# на права всё равно решает приложение Claude, не мы.
DEFAULT_PERMISSION_MODE = "auto"
_MODE_OPTION = "@rc_mode"
_FORMAT = "#{session_name}\t#{session_path}\t#{session_created}\t#{@rc_url}\t#{@rc_label}\t#{@rc_mode}"
```

`RemoteSession`: `mode: str = ""`. `list_sessions`: `len(parts) != 6` → skip; распаковать `mode`; передать в конструктор. `launch`:

```python
    mode = permission_mode or DEFAULT_PERMISSION_MODE
    command = (... + _permission_flag(mode) + ...)
    await _run("new-session", ...)
    for option, value in ((_LABEL_OPTION, label), (_MODE_OPTION, mode)):
        stored, why = await _run("set-option", "-t", f"={name}:", option, value, check=False)
        if stored != 0:
            log.warning("set %s on %s failed: %s", option, name, why.strip())
```

`passport.build`: `mode=session.mode`. `config.example.toml`: в комментарии к `permission_mode` заменить «Без него — как у claude по умолчанию» на «Без него — `auto`».

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/remote.py clauderc/passport.py config.example.toml tests/test_remote.py
git commit -m "feat: режим прав хранится в @rc_mode, по умолчанию auto

restart должен поднимать сессию с прежним режимом, а прежний нигде не
хранился. auto по умолчанию — с телефона на каждый шаг не наотвечаешься."
```

---

### Task 12: `send` в CLI

**Files:**
- Modify: `clauderc/cli.py` (парсер `send`, `_Commands.send`)
- Modify: `clauderc/actions.py` (`send_and_tail`)
- Test: `tests/test_actions.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `actions.send_and_tail(session, text, *, enter=True, wait_s=3.0, lines=20) -> str`; CLI `send <target> <text> [--no-enter] [--tail N]`.

- [ ] **Step 1: Тесты**

`tests/test_actions.py`:

```python
async def test_send_and_tail_waits_then_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(*a: str) -> tuple[int, str]:
        calls.append(a[0])
        return (0, "a\nb\nc\n") if a[0] == "capture-pane" else (0, "")

    monkeypatch.setattr(remote, "_run", _stub(handler))
    out = await actions.send_and_tail(_session(), "/mcp", wait_s=0, lines=2)
    assert out == "b\nc"
    assert calls == ["send-keys", "send-keys", "capture-pane"]
```

`tests/test_cli.py`:

```python
def test_send_prints_tail_when_asked(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    seen: dict[str, Any] = {}

    async def fake_send_and_tail(session, text, *, enter=True, wait_s=3.0, lines=20) -> str:
        seen.update(text=text, enter=enter, lines=lines)
        return "tail here"

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.actions, "send_and_tail", fake_send_and_tail)
    assert cli.main(["send", "oms", "/mcp", "--tail", "7"]) == 0
    assert seen == {"text": "/mcp", "enter": True, "lines": 7}
    assert "tail here" in capsys.readouterr().out


def test_send_no_enter_and_no_tail(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    seen: dict[str, Any] = {}

    async def fake_send(session, text, *, enter=True) -> None:
        seen.update(text=text, enter=enter)

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.actions, "send", fake_send)
    assert cli.main(["send", "oms", "1", "--no-enter"]) == 0
    assert seen == {"text": "1", "enter": False}
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

`actions.py`:

```python
async def send_and_tail(session, text, *, enter=True, wait_s=3.0, lines=20) -> str:
    await send(session, text, enter=enter)
    await asyncio.sleep(wait_s)
    return await tail(session, lines=lines)
```

`cli.py`:

```python
    send_cmd = sub.add_parser("send", help="набрать текст в панель сессии")
    send_cmd.add_argument("target")
    send_cmd.add_argument("text")
    send_cmd.add_argument("--no-enter", action="store_false", dest="enter", help="без Enter — для ответа одной клавишей")
    send_cmd.add_argument("--tail", type=int, default=0, metavar="N", help="через 3 с напечатать N последних строк панели")

    @staticmethod
    def send(args: argparse.Namespace) -> int:
        try:
            session = asyncio.run(_one(args.target))
        except _Ambiguous as exc:
            return _print_ambiguous(exc)
        if session is None:
            print(f"Сессия не найдена: {args.target}", file=sys.stderr)
            return EXIT_FAILED
        try:
            if args.tail > 0:
                print(asyncio.run(actions.send_and_tail(session, args.text, enter=args.enter, lines=args.tail)))
            else:
                asyncio.run(actions.send(session, args.text, enter=args.enter))
        except actions.ActionError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        return 0
```

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/actions.py clauderc/cli.py tests/test_actions.py tests/test_cli.py
git commit -m "feat(cli): send — текст в панель сессии, --tail для ответа

Стюарду нужно скинуть /mcp или ответить на диалог одной клавишей.
Текст уходит в send-keys -l, минуя shell."
```

---

### Task 13: `restart --mode`

**Files:**
- Modify: `clauderc/actions.py` (`restart`, `Killer`)
- Modify: `clauderc/cli.py` (парсер `restart`, `_Commands.restart`)
- Test: `tests/test_actions.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  Killer = Callable[[str, str], Awaitable[bool]]   # (tmux_name, cwd) -> погашена ли
  async def restart(session: RemoteSession, *, kill: Killer, mode: str | None = None,
                    timeout_s: float = 90.0) -> RemoteSession
  ```
  Бот передаёт `watcher.kill`, CLI — обёртку над `remote.kill_tmux`.

- [ ] **Step 1: Тесты**

```python
async def test_restart_kills_then_relaunches_with_resume_and_old_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    killed: list[tuple[str, str]] = []

    async def kill(tmux_name: str, cwd: str) -> bool:
        order.append("kill")
        killed.append((tmux_name, cwd))
        return True

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        order.append("launch")
        assert label == "oms@old"
        assert kw["resume"] == "session_01ABC"
        assert kw["permission_mode"] == "plan"
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    session = dataclasses.replace(_session(), mode="plan")
    await actions.restart(session, kill=kill)
    assert order == ["kill", "launch"]
    assert killed == [("session_01ABC", "/repos/oms")]


async def test_restart_overrides_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return True

    seen: dict[str, Any] = {}

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        seen.update(kw)
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    await actions.restart(_session(), kill=kill, mode="bypassPermissions")
    assert seen["permission_mode"] == "bypassPermissions"


async def test_restart_without_session_id_launches_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return True

    seen: dict[str, Any] = {}

    async def fake_launch(label: str, cwd: str, **kw: Any) -> RemoteSession:
        seen.update(kw)
        return _session()

    monkeypatch.setattr(actions.remote, "launch", fake_launch)
    await actions.restart(_session(tmux_name="rc-oms"), kill=kill)
    assert seen["resume"] is None


async def test_restart_refuses_when_kill_fails() -> None:
    async def kill(tmux_name: str, cwd: str) -> bool:
        return False

    with pytest.raises(actions.ActionError, match="не погас"):
        await actions.restart(_session(), kill=kill)
```

CLI:

```python
def test_restart_uses_kill_tmux_and_prints_passport(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    seen: dict[str, Any] = {}

    async def fake_restart(session, *, kill, mode=None, timeout_s=90.0) -> RemoteSession:
        seen["mode"] = mode
        assert await kill("rc-oms", "/repos/oms") is True
        return _session()

    async def fake_kill(tmux_name: str) -> bool:
        return True

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli, "kill_tmux", fake_kill)
    monkeypatch.setattr(cli.actions, "restart", fake_restart)
    monkeypatch.setattr(cli.passport.worktrees, "inspect", _none)  # хелпер: async -> None
    assert cli.main(["restart", "oms", "--mode", "bypassPermissions"]) == 0
    assert seen["mode"] == "bypassPermissions"
    assert "session_A" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

`actions.py`:

```python
from collections.abc import Awaitable, Callable

Killer = Callable[[str, str], Awaitable[bool]]
_SESSION_PREFIX = "session_"


async def restart(session, *, kill: Killer, mode: str | None = None, timeout_s: float = 90.0) -> RemoteSession:
    """Гасит и поднимает заново в том же каталоге с тем же ярлыком и --resume.

    `kill` — чужой: в боте это Watcher.kill, иначе намеренное гашение доехало
    бы карточкой «сессия упала». Режим — из аргумента, иначе прежний из
    @rc_mode. Ссылка после перезапуска новая.
    """
    session_id = session.tmux_name if session.tmux_name.startswith(_SESSION_PREFIX) else None
    if not await kill(session.tmux_name, session.cwd):
        raise ActionError(f"сессия {session.name} не погасла — перезапуск отменён")
    return await remote.launch(
        session.label or session.name,
        session.cwd,
        timeout_s=timeout_s,
        resume=session_id,
        permission_mode=mode or session.mode or None,
    )
```

`cli.py`:

```python
    restart_cmd = sub.add_parser("restart", help="погасить и поднять заново с --resume")
    restart_cmd.add_argument("target")
    restart_cmd.add_argument("--mode", choices=PERMISSION_MODES, help="сменить режим прав")

    @staticmethod
    def restart(args: argparse.Namespace) -> int:
        try:
            session = asyncio.run(_one(args.target))
        except _Ambiguous as exc:
            return _print_ambiguous(exc)
        if session is None:
            print(f"Сессия не найдена: {args.target}", file=sys.stderr)
            return EXIT_FAILED

        async def kill(tmux_name: str, cwd: str) -> bool:
            return await kill_tmux(tmux_name)

        try:
            fresh = asyncio.run(actions.restart(session, kill=kill, mode=args.mode))
        except (actions.ActionError, LaunchError) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        except TrustRequired as need:
            print(f"Каталог снова ждёт доверия: {attach_command(need.tmux_name)}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        print(passport.as_text(asyncio.run(_one_passport(fresh))))
        return 0


async def _one_passport(session: RemoteSession) -> passport.Passport:
    (p,) = await passport.collect([session], host=_host_name(), probe=False)
    return p
```

- [ ] **Step 3b: Сквозной тест на настоящем tmux**

В `tests/test_remote.py` рядом с `test_launch_against_real_tmux` (тот же фейковый `claude`, тот же изолированный сокет, тот же `finally`):

```python
@pytest.mark.skipif(not remote.tmux_available(), reason="нет tmux")
async def test_send_and_restart_against_real_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauderc import actions

    socket_name = f"claude-rc-pytest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(remote.TMUX_SOCKET_ENV, socket_name)
    url = "https://claude.ai/code/session_" + uuid.uuid4().hex[:16]
    stub = tmp_path / "fake-claude"
    # `cat` — чтобы набранный текст остался на экране: echo его печатает как ввод.
    stub.write_text(f'#!/bin/sh\necho "remote control active at"\necho "{url}"\ncat\n')
    stub.chmod(0o755)
    monkeypatch.setattr(remote, "CLAUDE_BIN", str(stub))
    label = f"pytest-{uuid.uuid4().hex[:8]}@mcp-fix"

    async def kill(tmux_name: str, cwd: str) -> bool:
        return await remote.kill_tmux(tmux_name)

    try:
        session = await remote.launch(label, str(tmp_path), timeout_s=20)
        await actions.send(session, "hello pane")
        await asyncio.sleep(0.5)
        assert "hello pane" in await actions.tail(session, lines=10)

        fresh = await actions.restart(session, kill=kill, timeout_s=20)
        assert fresh.label == label
        assert fresh.mode == remote.DEFAULT_PERMISSION_MODE
        assert fresh.cwd == session.cwd
    finally:
        await remote._run("kill-server", check=False)
```

Run: `uv run pytest tests/test_remote.py -k real_tmux -v` → оба теста PASS (или SKIPPED без tmux).

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/actions.py clauderc/cli.py tests/test_actions.py tests/test_cli.py tests/test_remote.py
git commit -m "feat: restart --mode — перезапуск с --resume и сменой режима прав

Режим задаётся только при старте, поэтому «переоткрыть с
bypassPermissions» — это гашение и запуск в том же каталоге с прежним
ярлыком. Гасит переданный kill: в боте — Watcher."
```

---

### Task 14: Кнопки в боте: `Bypass`, `/mcp`, `Tail`, `Rename`, `Start (bypass)`

**Files:**
- Modify: `clauderc/bot.py` (`_session_keyboard`, `card_pending`, колбэки `byp:`/`mcp:`/`tail:`/`ren:`, `rename_pending`, `LaunchRequest.mode`, `nav:bypass`, `_browse_card`)
- Test: `tests/test_bot_cards.py`

**Interfaces:**
- Produces: `_session_keyboard(token: str, url: str) -> InlineKeyboardMarkup`; `LaunchRequest.mode: str | None = None`; `_browse_card` со второй кнопкой запуска `nav:bypass`.

- [ ] **Step 1: Тесты**

```python
def test_session_keyboard_has_every_lever() -> None:
    markup = _session_keyboard("tok", "https://claude.ai/code/session_A")
    data = [b.callback_data or b.url for row in markup.inline_keyboard for b in row]
    assert data == [
        "https://claude.ai/code/session_A",
        "stop:tok", "byp:tok",
        "mcp:tok", "tail:tok", "ren:tok",
    ]
    assert all(len((b.callback_data or "").encode()) <= 64 for row in markup.inline_keyboard for b in row)


def test_browse_card_offers_bypass_start(tmp_path: Path) -> None:
    _, keyboard = _browse_card(tmp_path)
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "nav:here" in data and "nav:bypass" in data
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

```python
def _session_keyboard(token: str, url: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        rows.append([InlineKeyboardButton(text="Open in Claude", url=url)])
    rows.append([
        InlineKeyboardButton(text="⏹ Stop", callback_data=f"stop:{token}"),
        InlineKeyboardButton(text="🔓 Bypass", callback_data=f"byp:{token}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔌 /mcp", callback_data=f"mcp:{token}"),
        InlineKeyboardButton(text="📋 Tail", callback_data=f"tail:{token}"),
        InlineKeyboardButton(text="✏️ Rename", callback_data=f"ren:{token}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

`stop_pending` переименовать в `card_pending` (тот же тип `dict[str, tuple[str, int]]` — cwd, created_at) и **не** `pop`-ать в колбэках кроме `stop:` — одна карточка обслуживает несколько нажатий. `show_chats` и финальная карточка `start_session` вешают `_session_keyboard(token, session.url)`.

Из `start_session` вынести блок `except TrustRequired` в помощник, которым пользуются и запуск, и `Bypass`:

```python
    async def offer_trust(message: Message, need: TrustRequired) -> None:
        token = uuid.uuid4().hex[:8]
        trust_pending[token] = (need.tmux_name, need.cwd)
        await message.answer(
            "🔐 Claude впервые видит этот каталог и ждёт подтверждения.\n"
            f"<code>{html.escape(need.cwd)}</code>\n\n"
            "Он получит право читать, менять и запускать здесь файлы.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="Trust", callback_data=f"trust:{token}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"notrust:{token}"),
                ]]
            ),
        )
```

(в `start_session` текст уходит через `notice.edit_text(told(...))` — там оставить как есть, а помощник использовать для `answer`; либо дать помощнику параметр `render: Callable[[str, InlineKeyboardMarkup], Awaitable[None]]`. Первое проще.)

Общий помощник внутри `main`:

```python
    async def card_session(query: CallbackQuery, prefix: str) -> tuple[RemoteSession | None, Message | None]:
        """Сессия за кнопкой карточки, добытая заново по каталогу и времени создания."""
        pending = card_pending.get((query.data or "").removeprefix(prefix))
        message = _live_message(query)
        if pending is None:
            await query.answer("Карточка устарела")
            return None, message
        cwd, created_at = pending
        session = _same_session(await find(cwd), created_at)
        if session is None:
            await query.answer("Сессия уже не жива")
        return session, message
```

Колбэки:

```python
    @dp.callback_query(F.data.startswith("byp:"))
    async def on_bypass(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        session, message = await card_session(query, "byp:")
        if session is None or message is None:
            return
        await query.answer("Переоткрываю…")
        try:
            fresh = await actions.restart(
                session, kill=watcher.kill, mode="bypassPermissions", timeout_s=config.launch_timeout_s
            )
        except (actions.ActionError, LaunchError) as exc:
            await message.answer(f"❌ Не перезапустилась.\n<pre>{html.escape(str(exc))}</pre>"[:3800], parse_mode="HTML")
            return
        except TrustRequired as need:
            await offer_trust(message, need)
            return
        token = uuid.uuid4().hex[:8]
        card_pending[token] = (os.path.realpath(fresh.cwd), fresh.created_at)
        await message.answer(
            "🔓 Переоткрыта с bypassPermissions\n" + _session_card(passport.build(fresh, host=config.host, tree=None)),
            parse_mode="HTML",
            reply_markup=_session_keyboard(token, fresh.url),
        )

    @dp.callback_query(F.data.startswith(("mcp:", "tail:")))
    async def on_peek(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        prefix = "mcp:" if (query.data or "").startswith("mcp:") else "tail:"
        session, message = await card_session(query, prefix)
        if session is None or message is None:
            return
        await query.answer()
        try:
            text = (
                await actions.send_and_tail(session, "/mcp", lines=25)
                if prefix == "mcp:"
                else await actions.tail(session, lines=25)
            )
        except actions.ActionError as exc:
            await message.answer(f"❌ {html.escape(str(exc))}", parse_mode="HTML")
            return
        await message.answer(f"<pre>{html.escape(text)}</pre>"[:3800], parse_mode="HTML")

    @dp.callback_query(F.data.startswith("ren:"))
    async def on_rename(query: CallbackQuery) -> None:
        if not _is_authorized(query.from_user, config.allowed_user_id):
            return
        session, message = await card_session(query, "ren:")
        if session is None or message is None:
            return
        await query.answer()
        prompt = await message.answer(
            "Новое имя сессии — <b>ответом на это сообщение</b>.",
            parse_mode="HTML",
            reply_markup=ForceReply(force_reply=True, selective=True, input_field_placeholder="имя сессии"),
        )
        rename_pending[prompt.message_id] = (os.path.realpath(session.cwd), session.created_at)
```

В `on_text_reply` (задача 5) вторым блоком — `rename_pending.pop(reply.message_id)`: найти сессию через `_same_session(await find(cwd), created_at)`, вызвать `actions.rename`, ответить `✏️ Теперь <b>{label}</b>` плюс предупреждение, если `app_renamed` ложно.

`LaunchRequest.mode: str | None = None`; `start_session` передаёт `permission_mode=request.mode or config.permission_mode`. `_browse_card`: `launch_row` получает `InlineKeyboardButton(text="🔓 Start (bypass)", callback_data="nav:bypass")`; `on_nav` для `bypass`: `offer_start(message, state.cwd, mode="bypassPermissions")` — `offer_start` получает параметр `mode` и кладёт его во все `LaunchRequest`, которые создаёт.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/bot.py tests/test_bot_cards.py
git commit -m "feat(bot): пульт под карточкой — Bypass, /mcp, Tail, Rename, Start (bypass)

Кнопки только зовут actions; карточка держит каталог и время создания,
имя добывается заново — оно меняется у сессии под ногами."
```

---

### Task 15: `connect`

**Files:**
- Modify: `clauderc/remote.py` (`attach_argv(tmux_name, *, read_only=False, control=False)`)
- Modify: `clauderc/cli.py` (парсер `connect`, `_Commands.connect`)
- Test: `tests/test_remote.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `attach_argv(tmux_name, *, read_only: bool = False, control: bool = False) -> list[str]`; CLI `connect [target] [--read-only] [--cc] [--url] [--start] [--branch B]`.

- [ ] **Step 1: Тесты**

`tests/test_remote.py`:

```python
def test_attach_argv_read_only_and_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(remote.TMUX_SOCKET_ENV, raising=False)
    assert remote.attach_argv("s", read_only=True) == ["tmux", "attach", "-d", "-r", "-t", "=s"]
    assert remote.attach_argv("s", control=True) == ["tmux", "-CC", "attach", "-d", "-t", "=s"]
```

`tests/test_cli.py`:

```python
def test_connect_execs_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: seen.append(args))
    assert cli.main(["connect", "oms"]) == 0
    assert seen == [["tmux", "attach", "-d", "-t", "=rc-oms"]]


def test_connect_without_tty_hints_ssh_t(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: pytest.fail("exec"))
    assert cli.main(["connect", "oms"]) == 2
    assert "ssh -t" in capsys.readouterr().err


def test_connect_picks_the_only_session(monkeypatch) -> None:
    async def fake_list() -> list[RemoteSession]:
        return [_session()]

    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "list_sessions", fake_list)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: seen.append(args))
    assert cli.main(["connect"]) == 0
    assert seen[0][-1] == "=rc-oms"


def test_connect_url_only_prints(monkeypatch, capsys) -> None:
    async def fake_resolve(target: str) -> list[RemoteSession]:
        return [_session()]

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: pytest.fail("exec"))
    assert cli.main(["connect", "oms", "--url"]) == 0
    assert capsys.readouterr().out.strip() == "https://claude.ai/code/session_A"


def test_connect_lists_when_several_and_no_target(monkeypatch, capsys) -> None:
    async def fake_list() -> list[RemoteSession]:
        return [_session("a"), _session("b")]

    monkeypatch.setattr(cli, "list_sessions", fake_list)
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
    assert cli.main(["connect"]) == 2
    assert "rc-a" in capsys.readouterr().err
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать**

`remote.attach_argv`:

```python
def attach_argv(tmux_name: str, *, read_only: bool = False, control: bool = False) -> list[str]:
    socket = os.environ.get(TMUX_SOCKET_ENV)
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    if control:
        argv.append("-CC")  # iTerm2: окна tmux становятся нативными вкладками
    argv += ["attach", "-d"]
    if read_only:
        argv.append("-r")
    argv += ["-t", f"={tmux_name}"]
    return argv
```

`cli.py`:

```python
    connect = sub.add_parser("connect", help="подсесть к сессии терминалом (tmux attach)")
    connect.add_argument("target", nargs="?", help="ярлык, каталог или session_…; пусто — единственная живая")
    connect.add_argument("--read-only", action="store_true", dest="read_only", help="смотреть, не вводя")
    connect.add_argument("--cc", action="store_true", help="tmux -CC для iTerm2")
    connect.add_argument("--url", action="store_true", dest="url_only", help="напечатать ссылку и выйти")
    connect.add_argument("--start", action="store_true", help="нет сессии — поднять и подсесть")
    connect.add_argument("--branch", help="вместе с --start: worktree под ветку")

    @staticmethod
    def connect(args: argparse.Namespace) -> int:
        try:
            session = asyncio.run(_pick(args.target))
        except _Ambiguous as exc:
            return _print_ambiguous(exc)
        if session is None:
            if not args.start:
                print("Живой сессии нет. Добавь --start, чтобы поднять.", file=sys.stderr)
                return EXIT_FAILED
            target = Path(args.target or ".").expanduser().resolve()
            try:
                session = asyncio.run(_start(target, args.branch, None))
            except (LaunchError, WorktreeError) as exc:
                print(str(exc), file=sys.stderr)
                return EXIT_FAILED
            except _TrustDeclined as exc:
                print(str(exc), file=sys.stderr)
                return exc.exit_code
        if args.url_only:
            print(session.url or "ссылка неизвестна")
            return 0
        if not sys.stdin.isatty():
            print(
                "connect нужен терминал: без tty tmux ответит «open terminal failed».\n"
                "Через ssh — `ssh -t`, или напрямую: " + attach_command(session.tmux_name),
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        argv = attach_argv(session.tmux_name, read_only=args.read_only, control=args.cc)
        os.execvp(argv[0], argv)
        return 0  # только под подменённым execvp


async def _pick(target: str | None) -> RemoteSession | None:
    """Цель для connect: явная — через resolve; пустая — единственная живая."""
    if target:
        return await _one(target)
    sessions = await list_sessions()
    if len(sessions) > 1:
        raise _Ambiguous(sessions)
    return sessions[0] if sessions else None
```

Импорт `attach_argv` из `remote`.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/remote.py clauderc/cli.py tests/test_remote.py tests/test_cli.py
git commit -m "feat(cli): connect — подсесть к сессии терминалом

Единственный путь терминалом внутрь живой RC-сессии — прийти в её панель
tmux. exec, а не subprocess: tty должен достаться tmux напрямую. Без tty
— код 2 и подсказка про ssh -t."
```

---

### Task 16: `forward` — ssh-туннели к портам сессии

**Files:**
- Create: `clauderc/forward.py`
- Modify: `clauderc/cli.py` (парсер `forward`, `_Commands.forward`)
- Test: `tests/test_forward.py` (новый), `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  # forward.py
  @dataclass(frozen=True)
  class Forward: host: str; port: int; pid: int
  def pid_dir() -> Path                      # ~/.claude-rc/forwards, переопределяется CLAUDE_RC_FORWARDS
  def port_busy(port: int) -> bool
  def start(host: str, ports: list[int]) -> list[Forward]     # ForwardError на занятом порту
  def stop(host: str, ports: list[int] | None = None) -> list[Forward]
  def active() -> list[Forward]
  ```
  CLI `forward <target> [port…] [--stop]` — единственная команда, которая при `--host` исполняется локально; `host` берётся из `args.host` (задача 7), без него — код 2.

- [ ] **Step 1: Тесты `tests/test_forward.py`**

```python
import socket
from pathlib import Path

import pytest
from clauderc import forward


@pytest.fixture
def pid_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(forward.PID_DIR_ENV, str(tmp_path))
    return tmp_path


class _FakeProc:
    def __init__(self, argv: list[str], **kw: object) -> None:
        self.argv = argv
        self.pid = 4242


def test_start_spawns_ssh_and_writes_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(forward.subprocess, "Popen", lambda argv, **kw: (spawned.append(argv), _FakeProc(argv))[1])
    monkeypatch.setattr(forward, "port_busy", lambda port: False)
    (f,) = forward.start("m1", [3000])
    assert spawned == [["ssh", "-N", "-L", "3000:localhost:3000", "m1"]]
    assert f == forward.Forward(host="m1", port=3000, pid=4242)
    assert (pid_dir / "m1-3000.pid").read_text().strip() == "4242"


def test_start_refuses_busy_port(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    monkeypatch.setattr(forward, "port_busy", lambda port: True)
    with pytest.raises(forward.ForwardError, match="3000"):
        forward.start("m1", [3000])


def test_port_busy_detects_a_listener() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert forward.port_busy(port) is True
    assert forward.port_busy(port) is False


def test_stop_kills_and_removes_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("4242")
    killed: list[int] = []
    monkeypatch.setattr(forward.os, "kill", lambda pid, sig: killed.append(pid))
    assert forward.stop("m1") == [forward.Forward(host="m1", port=3000, pid=4242)]
    assert killed == [4242]
    assert not (pid_dir / "m1-3000.pid").exists()


def test_stop_ignores_dead_pid(monkeypatch: pytest.MonkeyPatch, pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("4242")

    def kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(forward.os, "kill", kill)
    assert forward.stop("m1", [3000]) == []
    assert not (pid_dir / "m1-3000.pid").exists()


def test_active_lists_pid_files(pid_dir: Path) -> None:
    (pid_dir / "m1-3000.pid").write_text("1")
    (pid_dir / "m3-80.pid").write_text("2")
    assert forward.active() == [
        forward.Forward(host="m1", port=3000, pid=1),
        forward.Forward(host="m3", port=80, pid=2),
    ]
```

`tests/test_cli.py`:

```python
def test_forward_requires_host(capsys) -> None:
    assert cli.main(["forward", "oms"]) == 2
    assert "--host" in capsys.readouterr().err


def test_forward_takes_ports_from_remote_passport(monkeypatch, capsys) -> None:
    payload = json.dumps({"sessions": [{"label": "oms@x", "cwd": "/r/oms", "tmux_name": "session_A", "listening": [3000, 5173]}]})
    monkeypatch.setattr(cli.proxy, "run_remote", lambda host, args, **kw: (0, payload))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli.forward, "start", lambda host, ports: (seen.update(host=host, ports=ports), [])[1])
    assert cli.main(["--host", "m1", "forward", "oms@x"]) == 0
    assert seen == {"host": "m1", "ports": [3000, 5173]}


def test_forward_explicit_ports_skip_remote_lookup(monkeypatch) -> None:
    monkeypatch.setattr(cli.proxy, "run_remote", lambda host, args, **kw: pytest.fail("looked up"))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli.forward, "start", lambda host, ports: (seen.update(ports=ports), [])[1])
    assert cli.main(["--host", "m1", "forward", "oms", "8080"]) == 0
    assert seen["ports"] == [8080]


def test_forward_stop(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.forward, "stop", lambda host, ports=None: [cli.forward.Forward("m1", 3000, 1)])
    assert cli.main(["--host", "m1", "forward", "oms", "--stop"]) == 0
    assert "3000" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что падают.**

- [ ] **Step 3: Реализовать `clauderc/forward.py`**

```python
"""ssh-туннели к портам, которые слушает сессия на другой машине.

Единственная команда, исполняемая на стороне того, кто сидит за клавиатурой:
туннель строится отсюда туда. pid каждого ssh — в файле, чтобы --stop умел снять.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

PID_DIR_ENV = "CLAUDE_RC_FORWARDS"
_DEFAULT_DIR = "~/.claude-rc/forwards"


class ForwardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Forward:
    host: str
    port: int
    pid: int


def pid_dir() -> Path:
    return Path(os.environ.get(PID_DIR_ENV) or _DEFAULT_DIR).expanduser()


def _pid_file(host: str, port: int) -> Path:
    return pid_dir() / f"{host}-{port}.pid"


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def start(host: str, ports: list[int]) -> list[Forward]:
    busy = [p for p in ports if port_busy(p)]
    if busy:
        raise ForwardError("порт уже занят локально: " + ", ".join(map(str, busy)))
    pid_dir().mkdir(parents=True, exist_ok=True)
    started: list[Forward] = []
    for port in ports:
        argv = ["ssh", "-N", "-L", f"{port}:localhost:{port}", host]
        proc = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _pid_file(host, port).write_text(str(proc.pid))
        started.append(Forward(host=host, port=port, pid=proc.pid))
    return started


def _parse(path: Path) -> Forward | None:
    host, _, port = path.stem.rpartition("-")
    pid = path.read_text().strip()
    if not host or not port.isdigit() or not pid.isdigit():
        return None
    return Forward(host=host, port=int(port), pid=int(pid))


def active() -> list[Forward]:
    if not pid_dir().is_dir():
        return []
    found = [_parse(p) for p in sorted(pid_dir().glob("*.pid"))]
    return [f for f in found if f is not None]


def stop(host: str, ports: list[int] | None = None) -> list[Forward]:
    stopped: list[Forward] = []
    for f in active():
        if f.host != host or (ports is not None and f.port not in ports):
            continue
        _pid_file(f.host, f.port).unlink(missing_ok=True)
        try:
            os.kill(f.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue  # ssh уже умер сам; файл убрали
        stopped.append(f)
    return stopped
```

`cli.py`:

```python
    forward_cmd = sub.add_parser("forward", help="ssh-туннель к портам сессии на --host")
    forward_cmd.add_argument("target")
    forward_cmd.add_argument("ports", nargs="*", type=int, help="порты; пусто — те, что сессия слушает")
    forward_cmd.add_argument("--stop", action="store_true")

    @staticmethod
    def forward(args: argparse.Namespace) -> int:
        host: str | None = getattr(args, "host", None)
        if not host:
            print("forward работает только с --host: на одной машине пробрасывать нечего.", file=sys.stderr)
            return EXIT_ENVIRONMENT
        if args.stop:
            for f in forward.stop(host, args.ports or None):
                print(f"снят {f.host}:{f.port} (pid {f.pid})")
            return 0
        ports = list(args.ports)
        if not ports:
            ports = _remote_ports(host, args.target)
            if ports is None:
                return EXIT_FAILED
            if not ports:
                print("Сессия ничего не слушает — назови порт явно.", file=sys.stderr)
                return EXIT_FAILED
        try:
            started = forward.start(host, ports)
        except forward.ForwardError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_FAILED
        for f in started:
            print(f"http://localhost:{f.port} → {f.host}:{f.port} (pid {f.pid})")
        return 0


def _remote_ports(host: str, target: str) -> list[int] | None:
    code, out = proxy.run_remote(host, ["sessions", "--json"])
    if code != 0:
        print(f"ssh {host}: {out.strip()}", file=sys.stderr)
        return None
    sessions = json.loads(out)["sessions"]
    wanted = target.strip()
    hits = [s for s in sessions if wanted in {s.get("label"), s.get("tmux_name"), s.get("cwd"), s.get("name")}]
    if len(hits) != 1:
        print("Сессия не найдена или их несколько:", file=sys.stderr)
        for s in sessions:
            print(f"  {s.get('label')}\t{s.get('tmux_name')}\t{s.get('cwd')}", file=sys.stderr)
        return None
    return [int(p) for p in hits[0].get("listening", [])]
```

`main` из задачи 7 уже пропускает `forward` мимо проксирования и кладёт `args.host`.

- [ ] **Step 4: Гейт и коммит**

```bash
git add clauderc/forward.py clauderc/cli.py tests/test_forward.py tests/test_cli.py
git commit -m "feat(cli): forward — ssh-туннели к портам сессии на другой машине

Порты берутся из паспорта той стороны, туннель строится отсюда. pid в
файле, чтобы --stop умел снять; занятый порт — ошибка, не тихий провал."
```

---

### Task 17: Документация: README (обе версии), CLAUDE.md, Design notes

**Files:**
- Modify: `README.md`, `README.ru.md`, `CLAUDE.md`

- [ ] **Step 1: README.md**

В таблицу `## CLI` добавить строки:

| Command | Action |
|---|---|
| `claude-rc --host m1 <command>` | run the same command on another machine over ssh (`bot`, `update`, `forward` excluded; paths must be absolute) |
| `claude-rc start … [--name n] [--new-worktree]` | name the session: label `repo@n`, branch `wt/<n>` for a new worktree |
| `claude-rc rename <target> <name>` | relabel in tmux and `/rename` in the app |
| `claude-rc send <target> <text> [--no-enter] [--tail N]` | type into the session pane |
| `claude-rc restart <target> [--mode m]` | kill and relaunch with `--resume`, optionally with another permission mode |
| `claude-rc connect [target] [--read-only] [--cc] [--url] [--start]` | attach a terminal to the session's tmux pane |
| `claude-rc --host m1 forward <target> [ports…] [--stop]` | ssh tunnels to the ports the session listens on |

Новый подраздел `### For a steering agent` (после таблицы CLI):

> One machine is the whole product; add `host = "m1"` to its config when you have a second one — the session passport then prints `ssh m1 -t 'tmux attach …'` and `claude-rc --host m1 …` forms. A Claude session that manages other sessions (on the same machine or another) needs exactly one call to see everything:
>
> ```bash
> claude-rc --host m1 sessions --json      # label, url, branch, state, last pane lines, listening ports
> claude-rc --host m1 send oms@fix '/mcp' --tail 20
> claude-rc --host m1 restart oms@fix --mode bypassPermissions
> claude-rc --host m1 forward oms@fix       # then open http://localhost:<port>
> ```
>
> `whoami` is for the agent *inside* a session; a steering agent is not inside one and should use `sessions --json`. `state` is what the pane shows (`idle`, `working`, `needs_input`, `unknown`), never what claude knows — read `last_lines` when in doubt.

В `## Design notes` добавить пункты (по одному абзацу): passport as the single render; state is an observation with `unknown` fallback; `@rc_mode` and `auto` default; `send-keys -l`; `restart` goes through `Watcher` in the bot; `forward` is the one client-side command; relative paths refused under `--host`; non-interactive ssh gets `~/.local/bin` on PATH explicitly.

- [ ] **Step 2: README.ru.md** — то же по-русски, в тех же разделах.

- [ ] **Step 3: CLAUDE.md**

В таблицу «Структура» — `passport.py`, `state_probe.py`, `actions.py`, `proxy.py`, `forward.py`. В «Команды» — примеры `send`/`restart`/`--host`. В «Грабли» — новые пункты из спеки («Грабли, которые видны заранее») плюс: «Неинтерактивный ssh не читает `.zshrc`: `proxy.remote_argv` подставляет `~/.local/bin` в PATH сам», «`actions` и `state_probe` ходят через `remote._run`, чтобы подмена в тестах была одна».

- [ ] **Step 4: Проверка и коммит**

Run: `make check` (ruff на docs не смотрит, но тесты должны остаться зелёными).

```bash
git add README.md README.ru.md CLAUDE.md
git commit -m "docs: пульт для сессий — паспорт, стюард, --host, состояние

README в обеих версиях: таблица CLI, раздел для управляющего агента,
Design notes. CLAUDE.md: новые модули и грабли."
```

---

## Self-review

**Покрытие спеки.** Принципы → задачи 1 (host опционален), 7 (нет хоста по умолчанию), 17 (README). Адрес `repo@name` → 4, 5, 6. Паспорт → 2, 3, 10, 11. Имя при старте и `rename` → 4, 5, 6, 14. Состояние, MCP, порты → 8, 9, 10, 12 (`/mcp` = `send --tail`). Действия: `send` 12, `restart` 13, `rename` 6, `connect` 15, `forward` 16, `stop` без изменений. `--host` → 7. Бот → 3, 5, 14. Тесты — в каждой задаче; сквозной tmux-тест (`send` доходит до панели, `restart` сохраняет ярлык) — задача 13, шаг 3b. Порядок работ 0–6 спеки соответствует задачам 0, 1–6, 7, 8–10, 11–14, 15–16, 17.

**Отклонение от спеки, зафиксированное осознанно.** Спека кладёт ожидание текста (имя, rename) в `state.py`, чтобы пережить рестарт бота. План использует существующий в репозитории механизм `ForceReply` + `reply_to_message` (как у ветки для Sync): он решает главную проблему — чужой текст не попадает в имя — а после рестарта бота человек просто нажимает кнопку ещё раз. Файл состояния для этого не нужен.

**Согласованность имён.** `passport.build/collect/as_dict/as_text/as_html/state_line`, `actions.send/tail/send_and_tail/rename/restart/ActionError/RenameResult/Killer`, `state_probe.State/SessionState/classify/probe/listening_ports/_exec/_parse_lsof`, `proxy.strip_host/command_of/relative_paths/remote_argv/exec_remote/run_remote/HOST_ENV/LOCAL_ONLY/TTY_COMMANDS/PATH_COMMANDS`, `forward.Forward/ForwardError/start/stop/active/port_busy/pid_dir/PID_DIR_ENV`, `cli._one/_pick/_Ambiguous/_print_ambiguous/_host_name/_one_passport/_remote_ports`, `bot.LaunchRequest/_apply_name/_name_prompt/_session_card/_session_keyboard/card_pending/rename_pending/name_pending`, `remote.DEFAULT_PERMISSION_MODE/_MODE_OPTION/RemoteSession.mode/attach_argv(read_only, control)`, `worktrees.branch_for/label(name=)` — используются одинаково во всех задачах.

## Приложение: отложенные замечания ревью (после реализации)

Ветка `feat/control-plane`, 38 коммитов. Всё ниже — minor, оставлено на потом; ничего из этого не блокирует слияние. Формулировки — из леджера выполнения.

- **Task 2.** passport._uptime дублирует bot._uptime — Task 3 обязан убрать копию в боте
- **Task 4.** worktrees.label использует truthiness `if name:` — пустое --name молча даёт repo@branch (по брифу, безвредно)
- **Task 6.** _repo_of при cwd="/" и пустом name даёт "@name" — крайний случай
- **Task 7.** LOCAL_ONLY содержит "forward", ветка недостижима до Task 16
- **Task 8.** второй паттерн _IDLE (`│ > │`) не покрыт ни одной фикстурой — мёртвая ветка
- **Task 8.** паттерн нумерованного списка в _NEEDS_INPUT (`^\s*❯?\s*\d+[.)]\s+\S`) даст ложный needs_input на idle-панели с нумерованным списком в ответе; рекомендация финальному ревью — требовать `❯` перед номером (в настоящих диалогах подсвеченный пункт его несёт) или ограничить окно последними 3 строками
- **Task 9.** _descendants назван BFS, а queue.pop() — LIFO; на корректность не влияет
- **Task 10.** whoami «probe=False» держится на том, что он не зовёт collect — тест-инвариант отсутствует
- **Task 10.** отрез экранированного хвоста по фиксированной позиции может разорвать сущность на границе (теоретически)
- **Task 11.** ветка `if mode else ""` в _permission_flag мертва — launch всегда передаёт непустой режим
- **Task 14.** двойной тап по Bypass не блокируется (второй restart упадёт на kill — самозалечивается)
- **Task 14.** [:3800] после закрытого </pre> может разрезать тег — паттерн унаследован из start_session
- **Task 14.** card_pending/rename_pending растут без вытеснения, как прежний stop_pending
- **Task 15.** -CC с -L socket не покрыт тестом; --branch без --start молча игнорируется
- **Task 16.** pid из файла может быть переиспользован чужим процессом — проверка cmdline не делается (модель доверия одного оператора); TOCTOU между port_busy и bind ssh
- **Task 16.** _HOST_RE отвергает user@host, хотя proxy его пропускает — расширить регэксп `^([A-Za-z0-9_.-]+@)?[A-Za-z0-9][A-Za-z0-9._-]*$`
- **Task 16.** test_forward_ambiguous_target_lists_matches на деле даёт 0 совпадений, а не 2 — поправить данные теста (одинаковый label у двух сессий)
- **Task 16.** при PermissionError в stop() уже снятые туннели не печатаются
- **Task 17.** Design notes в README.md — выжимка 10 из 15 новых «Грабель», сознательная выборка
Final Ruling: в волну правок входят Critical 1–2, Important 3–6 и дешёвые Minor 7, 11, 12, 15; остальные minor — fix later.
- **Волна правок.** строгая регулярка хоста (часть до @ тоже с буквы/цифры) принята — защищает ssh argv в forward.start; остальные `<pre>` с текстом ошибок ограничены _TAIL_CHARS=400 в remote._failure — fix later.
- **Финальное ревью, fix later.** name-keyed mark `_expected` имеет тот же дефект при гашении сессии под именем rc-<slug> и перезапуске под ним же — закроется тем же session_id; _remote_ports без expanduser/precedence tmux_name; пять `<pre>{exc}</pre>[:3800]` на коротких текстах ошибок; README строки 517/450 длиннее ширины переноса.
