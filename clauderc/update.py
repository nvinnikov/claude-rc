"""Обновление claude-rc: обновляем тем же способом, каким поставили.

Каналов установки три (клон, `uv tool`, Homebrew — см. README), и команда у
каждого своя. Спрашивать канал у человека незачем: путь окружения, в котором
запущен процесс, называет его однозначно, а `claude-rc` в PATH — нет, потому
что имя одинаковое у всех трёх.

Сеть здесь только читающая и только ради версии последнего релиза: сама
установка идёт штатной командой канала, а не нашей выдумкой.
"""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib import request as request  # реэкспорт для mypy --strict: тесты его подменяют

PACKAGE = "claude-rc"
REPO_URL = "https://github.com/nvinnikov/claude-rc"
RELEASES_API = "https://api.github.com/repos/nvinnikov/claude-rc/releases/latest"
TAP = "nvinnikov/tap"
APP_PATH = Path("/Applications/ClaudeRC.app")

# Внешний вызов без таймаута подвесил бы команду молча. Проверка версии —
# удобство, а не условие обновления, поэтому запас маленький.
_TIMEOUT_S = 10.0


class Channel(StrEnum):
    clone = "clone"
    uv_tool = "uv-tool"
    brew = "brew"
    unknown = "unknown"


@dataclass(frozen=True)
class Install:
    """Откуда взялась работающая копия тулзы."""

    channel: Channel
    root: Path | None = None  # клон, из которого ставили
    formula: str | None = None  # имя формулы Homebrew

    @property
    def label(self) -> str:
        if self.channel is Channel.clone and self.root is not None:
            return f"клон {self.root}"
        if self.channel is Channel.brew and self.formula is not None:
            return f"Homebrew, формула {self.formula}"
        return {
            Channel.uv_tool: "uv tool",
            Channel.brew: "Homebrew",
            Channel.unknown: "способ установки не опознан",
        }.get(self.channel, self.channel.value)


def detect(prefix: Path | None = None) -> Install:
    """Канал установки по пути окружения (`sys.prefix`).

    Порядок проверок важен: у клона рядом с окружением лежит `pyproject.toml`,
    а у `uv tool` — нет, поэтому файл-признак различает их надёжнее любого
    разбора пути.
    """
    root = Path(sys.prefix) if prefix is None else prefix
    parts = root.parts

    if "Cellar" in parts:
        # /opt/homebrew/Cellar/<формула>/<версия>/libexec
        index = parts.index("Cellar")
        formula = parts[index + 1] if index + 1 < len(parts) else None
        return Install(Channel.brew, formula=formula)

    # Именно наш клон, а не любой каталог с pyproject.toml: `uv run claude-rc`
    # из чужого проекта дал бы окружение с его pyproject рядом, и обновление
    # ушло бы тянуть и собирать чужой репозиторий.
    if _project_name(root.parent / "pyproject.toml") == PACKAGE:
        return Install(Channel.clone, root=root.parent)

    # ~/.local/share/uv/tools/claude-rc
    if "uv" in parts and "tools" in parts:
        # `make install` — заявленный основной способ — ставит тулзу из клона
        # (`uv tool install --force .`), и окружение при этом ровно такое же,
        # как у установки из git. Обновлять надо клон, а не master в интернете,
        # иначе правки человека молча заменяются чужой веткой, а приложение в
        # /Applications не переустанавливается вовсе. Путь клона uv записывает
        # в квитанцию рядом с окружением — она и различает эти два случая.
        source = _uv_source(root / "uv-receipt.toml")
        if source is not None and _project_name(source / "pyproject.toml") == PACKAGE:
            return Install(Channel.clone, root=source)
        return Install(Channel.uv_tool)

    return Install(Channel.unknown)


def _project_name(pyproject: Path) -> str | None:
    """`project.name` из pyproject.toml или None, если файла нет и он не читается."""
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    return name if isinstance(name, str) else None


def _uv_source(receipt: Path) -> Path | None:
    """Каталог, из которого `uv tool install` поставил нашу тулзу.

    `uv-receipt.toml` пишет источник рядом с окружением: у установки из каталога
    это `directory`, у установки из git — `git`, и второе нам не источник для
    обновления, а ровно то, что `plan` и так сделает.
    """
    try:
        with receipt.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    tool = data.get("tool")
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    if not isinstance(requirements, list):
        return None
    for requirement in requirements:
        if not isinstance(requirement, dict) or requirement.get("name") != PACKAGE:
            continue
        directory = requirement.get("directory")
        return Path(directory) if isinstance(directory, str) else None
    return None


def plan(install: Install, *, app_installed: bool, version: str | None = None) -> list[list[str]]:
    """Команды обновления для канала. Пустой список — обновить нечем.

    Клон обновляется двумя шагами: сначала `sync` подтягивает историю (его
    правила про грязное дерево и перемотку — единственные, которым здесь можно
    доверять), потом переустановка. Здесь только вторая половина.
    """
    if install.channel is Channel.clone and install.root is not None:
        # Приложение собирается через swift: на машине без него (и без самого
        # приложения) целиться в него незачем.
        target = "install" if app_installed else "install-tool"
        return [["make", "-C", str(install.root), target]]

    if install.channel is Channel.uv_tool:
        # На тег, а не на ветку по умолчанию: команда только что назвала номер
        # релиза, и ставить при этом что-то другое значит соврать. Версии нет
        # (сеть недоступна) — ставим ветку, это лучше, чем не обновиться вовсе.
        target = f"git+{REPO_URL}@v{version}" if version else f"git+{REPO_URL}"
        return [["uv", "tool", "install", "--force", target]]

    if install.channel is Channel.brew and install.formula is not None:
        commands = [["brew", "upgrade", f"{TAP}/{install.formula}"]]
        if app_installed:
            # Приложение приезжает каской, и формулу она тянет зависимостью —
            # но обновляются они порознь.
            commands.append(["brew", "upgrade", "--cask", f"{TAP}/claude-rc-app"])
        return commands

    return []


def latest_release(*, timeout_s: float = _TIMEOUT_S) -> str | None:
    """Версия последнего релиза на GitHub или None, если не дозвонились.

    Недоступная сеть — не ошибка обновления: обновиться можно и вслепую,
    поэтому наверх уходит None, а не исключение.
    """
    try:
        with request.urlopen(RELEASES_API, timeout=timeout_s) as response:
            payload = json.load(response)
    # URLError — подкласс OSError, отдельно его называть незачем.
    except (TimeoutError, ValueError, OSError):
        return None
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    return tag.lstrip("v") if isinstance(tag, str) else None


def is_newer(latest: str, current: str) -> bool:
    """Строго ли `latest` новее `current`.

    Версии проекта — только `X.Y.Z` (см. README, «Releases»). Всё, что не
    разбирается, считаем не новее: соврать «есть обновление» хуже, чем
    промолчать.
    """
    parsed_latest, parsed_current = _parse(latest), _parse(current)
    if parsed_latest is None or parsed_current is None:
        return False
    return parsed_latest > parsed_current


def _parse(version: str) -> tuple[int, ...] | None:
    parts = version.strip().lstrip("v").split(".")
    if not all(part.isdigit() for part in parts) or not parts:
        return None
    return tuple(int(part) for part in parts)
