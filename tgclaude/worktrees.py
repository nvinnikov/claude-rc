"""Git worktree'ы под RC-сессии: отдельная ветка — отдельный рабочий каталог.

Две сессии в одном каталоге репозитория неизбежно дерутся: общий индекс, общая
ветка, общие файлы. Worktree даёт каждой свой каталог при общей истории.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path

_SLUG = re.compile(r"[^A-Za-z0-9_-]+")
_ERROR_TAIL = 400


class WorktreeError(RuntimeError):
    """Git отказался создать или удалить worktree."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    repo: str
    branch: str
    dirty: bool
    unpushed: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def blockers(self) -> list[str]:
        """Причины, по которым удалять этот worktree опасно."""
        reasons = []
        if self.dirty:
            reasons.append("есть незакоммиченные изменения")
        if self.unpushed:
            reasons.append(f"{self.unpushed} коммит(ов) нет ни на одном remote")
        return reasons


def slug(text: str) -> str:
    return _SLUG.sub("-", text).strip("-").lower() or "wt"


def generate_branch(now: float | None = None) -> str:
    """Имя ветки, когда оно не важно — важна параллельная сессия.

    Секунды в метке нужны, чтобы два нажатия подряд не пришли в один worktree.
    """
    return "wt/" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


async def _git(cwd: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def ensure(repo: Path, branch: str, root: Path) -> Path:
    """Каталог worktree под ветку. Уже созданный переиспользуется."""
    if not (repo / ".git").exists():
        raise WorktreeError(f"{repo.name} — не git-репозиторий, worktree делать негде")

    path = root / f"{slug(repo.name)}-{slug(branch)}"
    if path.is_dir():
        return path

    root.mkdir(parents=True, exist_ok=True)
    # Сначала пробуем существующую ветку — git сам подхватит remote-only через DWIM.
    # Не вышло — создаём новую от текущего HEAD.
    code, out_existing = await _git(repo, "worktree", "add", str(path), branch)
    if code != 0:
        code, out_new = await _git(repo, "worktree", "add", "-b", branch, str(path))
        if code != 0:
            detail = out_new.strip() or out_existing.strip()
            raise WorktreeError(detail[-_ERROR_TAIL:])
    return path


async def inspect(path: Path) -> Worktree | None:
    """Состояние worktree или None, если это не рабочая копия git."""
    code, branch = await _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return None

    _, status = await _git(path, "status", "--porcelain")
    _, common = await _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    # Коммиты, которых нет ни на одном remote: покрывает и ветку без upstream.
    _, unpushed = await _git(path, "log", "--oneline", "HEAD", "--not", "--remotes")

    return Worktree(
        path=path,
        repo=Path(common.strip()).parent.name if common.strip() else "?",
        branch=branch.strip(),
        dirty=bool(status.strip()),
        unpushed=len([line for line in unpushed.splitlines() if line.strip()]),
    )


async def list_all(root: Path) -> list[Worktree]:
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not (path / ".git").exists():
            continue
        info = await inspect(path)
        if info is not None:
            found.append(info)
    return found


async def remove(root: Path, name: str, *, force: bool = False) -> Worktree:
    """Удаляет worktree. Без `force` отказывается, если работа не сохранена."""
    path = root / name
    info = await inspect(path) if path.is_dir() else None
    if info is None:
        raise WorktreeError(f"нет worktree {name}")
    if info.blockers and not force:
        raise WorktreeError("; ".join(info.blockers))

    code, common = await _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if code != 0:
        raise WorktreeError(common.strip()[-_ERROR_TAIL:])

    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(path)]
    code, out = await _git(Path(common.strip()).parent, *args)
    if code != 0:
        raise WorktreeError(out.strip()[-_ERROR_TAIL:])
    return info
