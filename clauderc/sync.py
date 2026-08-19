"""Состояние локальных репозиториев и подтягивание изменений из origin.

Модуль ничего не знает ни про Telegram, ни про терминал: он отвечает на два
вопроса — «в каком состоянии репозиторий» и «подтяни его».

Граница, которую здесь не переходят: `merge`, `rebase`, `stash`, `reset` и
`checkout -f`. Всё, что способно перезаписать или спрятать незакоммиченную
работу, живёт вне этого модуля.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from clauderc.worktrees import _git


@dataclass(frozen=True)
class RepoStatus:
    path: Path
    branch: str
    dirty: bool
    ahead: int
    behind: int
    upstream: str | None
    detached: bool


async def status(repo: Path) -> RepoStatus | None:
    """Состояние репозитория или None, если каталог не рабочая копия git."""
    code, branch = await _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return None
    branch = branch.strip()

    # `git status --porcelain` показывает и неотслеживаемые файлы: их `git switch`
    # тоже унесёт на другую ветку, так что для нас это такая же грязь.
    _, dirty_out = await _git(repo, "status", "--porcelain")

    upstream_code, upstream = await _git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    ahead = behind = 0
    tracked = upstream.strip() if upstream_code == 0 else None
    if tracked:
        counts_code, counts = await _git(
            repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
        )
        if counts_code == 0:
            parts = counts.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                ahead, behind = int(parts[0]), int(parts[1])

    return RepoStatus(
        path=repo,
        branch=branch,
        dirty=bool(dirty_out.strip()),
        ahead=ahead,
        behind=behind,
        upstream=tracked,
        detached=branch == "HEAD",
    )
