"""Состояние локальных репозиториев и подтягивание изменений из origin.

Модуль ничего не знает ни про Telegram, ни про терминал: он отвечает на два
вопроса — «в каком состоянии репозиторий» и «подтяни его».

Граница, которую здесь не переходят: `merge`, `rebase`, `stash`, `reset` и
`checkout -f`. Всё, что способно перезаписать или спрятать незакоммиченную
работу, живёт вне этого модуля.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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


class Outcome(StrEnum):
    updated = "updated"
    already = "already"
    skipped = "skipped"
    failed = "failed"


@dataclass(frozen=True)
class SyncResult:
    path: Path
    outcome: Outcome
    detail: str
    branch: str


async def sync(repo: Path, *, branch: str | None = None, fetch: bool = True) -> SyncResult:
    """Подтягивает изменения из origin, ничего не потеряв.

    Порядок и отказы описаны в спеке. Коротко: грязный репозиторий не трогаем
    вовсе, переключаем ветку только у чистого, тянем только перемоткой.
    """
    current = await status(repo)
    if current is None:
        return SyncResult(repo, Outcome.skipped, "не рабочая копия git", "?")

    # Дешёвые локальные проверки — до сети: нет смысла ходить в fetch ради
    # репозитория, который следующей же строкой пропустим.
    if current.detached:
        return SyncResult(repo, Outcome.skipped, "отсоединённый HEAD", current.branch)

    if current.dirty:
        # Ни переключать, ни тянуть: и то и другое трогает рабочее дерево.
        return SyncResult(repo, Outcome.skipped, "незакоммиченные изменения", current.branch)

    if fetch:
        code, out = await _git(repo, "fetch", "--quiet")
        if code != 0:
            return SyncResult(repo, Outcome.failed, _short(out), current.branch)
        current = await status(repo) or current

    if branch and branch != current.branch:
        switched = await _switch(repo, branch)
        if switched is not None:
            return switched
        current = await status(repo) or current

    if current.upstream is None:
        return SyncResult(repo, Outcome.skipped, "у ветки нет upstream в origin", current.branch)

    if current.behind == 0:
        return SyncResult(repo, Outcome.already, "уже актуально", current.branch)

    if not fetch:
        # `pull` сам полез бы в сеть — а без fetch обещано смотреть состояние, не трогая её.
        # merge/rebase здесь недоступны (граница модуля), так что перемотать нечем без сети.
        return SyncResult(
            repo, Outcome.skipped, f"отстаёт на {current.behind}, нужен fetch", current.branch
        )

    code, out = await _git(repo, "pull", "--ff-only", "--quiet")
    if code != 0:
        return SyncResult(
            repo, Outcome.failed, f"остался на {current.branch}: {_short(out)}", current.branch
        )

    return SyncResult(
        repo, Outcome.updated, f"подтянуто коммитов: {current.behind}", current.branch
    )


async def _switch(repo: Path, branch: str) -> SyncResult | None:
    """Переключает ветку. Возвращает результат только при отказе."""
    code, _ = await _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if code == 0:
        code, out = await _git(repo, "switch", "--quiet", branch)
        return None if code == 0 else SyncResult(repo, Outcome.failed, _short(out), branch)

    code, _ = await _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
    if code != 0:
        return SyncResult(repo, Outcome.skipped, "ветки нет ни локально, ни в origin", branch)

    code, out = await _git(repo, "switch", "--quiet", "-c", branch, "--track", f"origin/{branch}")
    return None if code == 0 else SyncResult(repo, Outcome.failed, _short(out), branch)


def _short(output: str) -> str:
    """Строка вывода git для отчёта: одна на репо, по возможности содержательная.

    Перед перемоткой git часто печатает несколько строк `hint:` и только потом
    `fatal:`/`error:` — в отчёт должна попасть причина, а не подсказка.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if line.startswith(("fatal:", "error:")):
            return line[:120]
    return lines[0][:120] if lines else "git завершился с ошибкой"
