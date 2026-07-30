import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_id: int
    rc_roots: tuple[Path, ...]
    worktree_root: Path
    state_path: Path
    scan_depth: int
    launch_timeout_s: float


def load_config(path: Path) -> Config:
    """Читает config.toml. Неизвестные ключи игнорируются."""
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    raw_roots = raw.get("rc_roots") or ["~"]
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]

    roots = tuple(Path(item).expanduser() for item in raw_roots)
    missing = [str(p) for p in roots if not p.is_dir()]
    if missing:
        raise ValueError(f"rc_roots: каталог не найден: {', '.join(missing)}")

    return Config(
        bot_token=raw["bot_token"],
        allowed_user_id=raw["allowed_user_id"],
        rc_roots=roots,
        # Каталог создаётся при первом worktree, существования заранее не требуем.
        worktree_root=Path(raw.get("worktree_root", "~/.claude-rc/worktrees")).expanduser(),
        state_path=Path(raw.get("state_path", "~/.claude-rc/state.json")).expanduser(),
        scan_depth=int(raw.get("scan_depth", 3)),
        launch_timeout_s=float(raw.get("launch_timeout_s", 90)),
    )
