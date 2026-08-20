"""Состояние локальных репозиториев и подтягивание изменений из origin.

Модуль ничего не знает ни про Telegram, ни про терминал: он отвечает на два
вопроса — «в каком состоянии репозиторий» и «подтяни его».

Граница, которую здесь не переходят: `merge`, `rebase`, `stash`, `reset` и
`checkout -f`. Всё, что способно перезаписать или спрятать незакоммиченную
работу, живёт вне этого модуля.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from clauderc.browse import is_repo
from clauderc.remote import find as _find_session
from clauderc.worktrees import _git

# fetch/pull ходят в сеть — общий таймаут локальных вызовов (30с, см. _git в
# worktrees.py) на большом или медленном репозитории может оборвать их на
# середине. Тот же приём, что у `worktree add` в worktrees.py, здесь — для
# сетевых операций sync.
_NETWORK_TIMEOUT_S = 120.0


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
    dirty_code, dirty_out = await _git(repo, "status", "--porcelain")
    if dirty_code != 0:
        # Не отработало — значит состояние неизвестно, а не «чисто»: пустой
        # вывод при ошибке молча сошёл бы за отсутствие изменений, и sync()
        # тронул бы репозиторий, чьё реальное состояние мы не увидели.
        return None

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
        code, out = await _git(repo, "fetch", "--quiet", timeout_s=_NETWORK_TIMEOUT_S)
        if code != 0:
            return SyncResult(repo, Outcome.failed, _short(out), current.branch)
        current = await status(repo) or current

    if branch and branch != current.branch:
        # fetch идёт секунды, а человек вполне мог за это время начать писать
        # в тот же репозиторий — dirty проверяли до сети, а рабочее дерево с
        # тех пор могло измениться. Перепроверяем прямо перед переключением.
        if current.dirty:
            return SyncResult(repo, Outcome.skipped, "незакоммиченные изменения", current.branch)
        # Живая RC-сессия работает в этом каталоге прямо сейчас: переключить
        # под ней ветку — значит подменить рабочее дерево так, что сессия
        # этого не заметит (mtime спасает не от всего, см. CLAUDE.md). Обычная
        # перемотка без смены ветки сюда не попадает — ветка не меняется.
        if await _find_session(str(repo)) is not None:
            return SyncResult(
                repo, Outcome.skipped, "живая RC-сессия — ветку не трогаю", current.branch
            )
        # Worktree — независимо от того, работает там кто-то сейчас или нет:
        # переключение ветки обнулит счётчик неотправленных коммитов и снимет
        # защиту `wtrm` от удаления неотправленной работы (см. CLAUDE.md, «Грабли»).
        if await _is_worktree(repo):
            return SyncResult(
                repo,
                Outcome.skipped,
                "worktree — переключение снимет защиту от удаления",
                current.branch,
            )
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

    if current.dirty:
        # То же окно, что перед переключением: fetch (и, если было, switch)
        # заняли время, за которое рабочее дерево могло стать грязным.
        return SyncResult(repo, Outcome.skipped, "незакоммиченные изменения", current.branch)

    code, out = await _git(repo, "pull", "--ff-only", "--quiet", timeout_s=_NETWORK_TIMEOUT_S)
    if code != 0:
        return SyncResult(
            repo, Outcome.failed, f"остался на {current.branch}: {_short(out)}", current.branch
        )

    return SyncResult(
        repo, Outcome.updated, f"подтянуто коммитов: {current.behind}", current.branch
    )


async def _is_worktree(repo: Path) -> bool:
    """Worktree, а не обычный репозиторий.

    У обычного репозитория `git rev-parse --git-common-dir` печатает ровно
    `.git`; у worktree — путь до `.git` основного репозитория, физически
    другого места (git сам отдаёт его абсолютным, раз он вне каталога).
    """
    code, out = await _git(repo, "rev-parse", "--git-common-dir")
    return code == 0 and out.strip() != ".git"


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


async def sync_one(repo: Path, *, branch: str | None = None, fetch: bool = True) -> SyncResult:
    """Оборачивает `sync` одного репозитория так, чтобы неожиданное исключение
    стало `failed` только для него — иначе `asyncio.gather` уронил бы весь
    обход, и ни бот, ни CLI не показали бы результат по уже отработавшим репо.
    """
    try:
        return await sync(repo, branch=branch, fetch=fetch)
    except Exception as exc:
        return SyncResult(repo, Outcome.failed, str(exc)[:120], "?")


async def sync_all(
    targets: list[Path], *, branch: str | None = None, fetch: bool = True
) -> list[SyncResult]:
    # Параллельно: каждый репозиторий — несколько вызовов git, последовательно
    # десяток штук ждать заметно дольше.
    return list(await asyncio.gather(*(sync_one(t, branch=branch, fetch=fetch) for t in targets)))


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def list_repos(cwd: Path) -> list[Path]:
    """Репозитории в каталоге: он сам (если репозиторий) и его прямые дети.

    Резолвится и дедуплицируется по физическому пути тем же способом, что
    `resolve_targets` — иначе бот и CLI показали бы разные списки для одного
    и того же дерева (символическая ссылка на уже перечисленный репозиторий
    дала бы второй `Path` на ту же цель).
    """
    candidates = [child.resolve() for child in sorted(cwd.iterdir()) if is_repo(child)]
    if is_repo(cwd):
        candidates.insert(0, cwd.resolve())
    return _dedupe(candidates)


def resolve_targets(paths: list[Path]) -> list[Path]:
    """Что синхронизировать: явные пути или репозитории в текущем каталоге процесса."""
    if paths:
        # is_repo — после expanduser: иначе `sync ~/repo` проверял бы буквальный
        # путь `~/repo` (без `.git` в нём) и молча выбрасывал бы цель из списка.
        expanded = [p.expanduser() for p in paths]
        return _dedupe([p.resolve() for p in expanded if is_repo(p)])
    return list_repos(Path.cwd())


def rejected_paths(paths: list[Path]) -> list[Path]:
    """Из явно перечисленных путей — те, что `resolve_targets` отбросит.

    `resolve_targets` их просто не включает в список; здесь — чтобы можно
    было назвать путь и причину, а не только развести руками «Репозиториев
    не нашлось», когда на деле в пути опечатка.
    """
    return [p for p in paths if not is_repo(p.expanduser())]


def display_names(paths: list[Path]) -> dict[Path, str]:
    """Метка на строку отчёта: имя каталога, а при совпадении с другим — плюс
    столько родительских каталогов, сколько нужно для однозначности. Имя
    репозитория в дереве не уникально (два клона одного репо, см. CLAUDE.md) —
    голое `path.name` дало бы две неотличимые строки.
    """
    labels: dict[Path, str] = {}
    for path in paths:
        depth = 1
        while True:
            suffix = path.parts[-depth:]
            unique = (
                depth >= len(path.parts)
                or sum(1 for other in paths if other.parts[-depth:] == suffix) == 1
            )
            if unique:
                labels[path] = str(Path(*suffix))
                break
            depth += 1
    return labels


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
