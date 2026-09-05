import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clauderc.remote import PERMISSION_MODES


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_id: int
    rc_roots: tuple[Path, ...]
    worktree_root: Path
    state_path: Path
    scan_depth: int
    launch_timeout_s: float
    permission_mode: str | None
    pull_before_start: bool


def load_config(path: Path) -> Config:
    """Читает config.toml. Неизвестные ключи игнорируются.

    Каждое поле проверяется на тип явно — раньше значения отдавались как
    есть, и неверный тип (например, `allowed_user_id = "123"` в кавычках —
    так пишут числа во многих форматах) проскакивал мимо `load_config` и
    падал уже в местах вызова: `TypeError` из `int()`/`float()`/`Path()` в
    визарде, `doctor`, боте — один и тот же класс ошибки в четырёх разных
    местах. Здесь — источник, поэтому здесь и единственная проверка;
    `ValueError` уже ловят все вызывающие. Сообщения называют только поле и
    ожидаемый тип, без значения: тут есть `bot_token`, а секрет незачем
    разносить по логам и сообщениям об ошибках.
    """
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    bot_token = _require_str(raw, "bot_token")
    allowed_user_id = _require_int(raw, "allowed_user_id")
    roots = _require_roots(raw)
    worktree_root = _require_path(raw, "worktree_root", "~/.claude-rc/worktrees")
    state_path = _require_path(raw, "state_path", "~/.claude-rc/state.json")
    scan_depth = _require_int(raw, "scan_depth", default=3)
    launch_timeout_s = _require_number(raw, "launch_timeout_s", default=90)
    permission_mode = _require_permission_mode(raw)
    pull_before_start = _require_bool(raw, "pull_before_start", default=False)

    missing = [str(p) for p in roots if not p.is_dir()]
    if missing:
        raise ValueError(f"rc_roots: каталог не найден: {', '.join(missing)}")

    return Config(
        bot_token=bot_token,
        allowed_user_id=allowed_user_id,
        rc_roots=roots,
        # Каталог создаётся при первом worktree, существования заранее не требуем.
        worktree_root=worktree_root,
        state_path=state_path,
        scan_depth=scan_depth,
        launch_timeout_s=launch_timeout_s,
        permission_mode=permission_mode,
        pull_before_start=pull_before_start,
    )


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw[key]  # KeyError на отсутствии — тот же контракт, что и раньше
    if not isinstance(value, str):
        raise ValueError(f"{key}: ожидалась строка")
    return value


def _require_permission_mode(raw: dict[str, Any]) -> str | None:
    """Режим прав для новых сессий или None — «как у claude по умолчанию».

    Опечатку называем здесь: claude на неизвестном режиме отказывается
    стартовать, и человек увидел бы не «в конфиге не тот режим», а мёртвую
    сессию без объяснений.
    """
    value = raw.get("permission_mode")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("permission_mode: ожидалась строка")
    mode = value.strip()
    if not mode:
        return None
    if mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode: {mode!r} — ожидалось одно из {', '.join(PERMISSION_MODES)}"
        )
    return mode


def _require_bool(raw: dict[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key}: ожидалось true или false")
    return value


def _require_int(raw: dict[str, Any], key: str, *, default: int | None = None) -> int:
    value = raw[key] if default is None else raw.get(key, default)
    # bool — подкласс int в Python: allowed_user_id = true иначе тихо стал бы 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key}: ожидалось целое число")
    return value


def _require_number(raw: dict[str, Any], key: str, *, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key}: ожидалось число")
    return float(value)


def _require_path(raw: dict[str, Any], key: str, default: str) -> Path:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key}: ожидалась строка (путь)")
    return Path(value).expanduser()


def _require_roots(raw: dict[str, Any]) -> tuple[Path, ...]:
    value = raw.get("rc_roots") or ["~"]
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("rc_roots: ожидалась строка или список строк")
    if not all(isinstance(item, str) for item in items):
        raise ValueError("rc_roots: ожидалась строка или список строк")
    return tuple(Path(item).expanduser() for item in items)
