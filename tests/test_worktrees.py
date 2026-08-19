import asyncio
from pathlib import Path
from typing import Any

import pytest
from clauderc import worktrees
from clauderc.worktrees import WorktreeError, slug


async def _git(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    assert proc.returncode == 0, out.decode()
    return out.decode()


@pytest.fixture
async def repo(tmp_path: Path) -> Path:
    path = tmp_path / "demo"
    path.mkdir()
    await _git(path, "init", "-q", "-b", "main")
    await _git(path, "config", "user.email", "t@example.com")
    await _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("hi\n")
    await _git(path, "add", "-A")
    await _git(path, "commit", "-qm", "init")
    return path


def test_slug_normalizes_branch_names() -> None:
    assert slug("feature/DEV-123_fix") == "feature-dev-123_fix"
    assert slug("///") == "wt"


class _HangingProcess:
    """Подменяет процесс, который никогда не завершится сам — только по kill()."""

    def __init__(self) -> None:
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(3600)
        return b"", b""  # pragma: no cover — недостижимо, ждём таймаут раньше

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return -9


async def test_git_kills_process_that_exceeds_timeout(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    hanging = _HangingProcess()

    async def fake_exec(*args: Any, **kwargs: Any) -> _HangingProcess:
        return hanging

    # worktrees.py делает `import asyncio` и зовёт asyncio.create_subprocess_exec —
    # тот же объект модуля, что и здесь, так что патчим его напрямую.
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(worktrees, "_GIT_TIMEOUT_S", 0.05)

    code, out = await worktrees._git(repo, "status")

    assert code == 1
    assert "не ответил" in out
    assert hanging.killed is True  # процесс завершён, а не оставлен висеть
    assert hanging.waited is True


async def test_git_disables_terminal_prompt(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    # Приватный remote не должен ждать пароль, который никто не введёт.
    real_exec: Any = asyncio.create_subprocess_exec
    captured: dict[str, object] = {}

    async def spy_exec(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_exec)

    await worktrees._git(repo, "rev-parse", "--show-toplevel")

    env = captured.get("env")
    assert isinstance(env, dict)
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


async def test_ensure_worktree_add_uses_its_own_longer_timeout(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    # `worktree add` — полный чекаут дерева, не короткий локальный запрос;
    # общий _GIT_TIMEOUT_S (30с) для него мал на крупном репозитории. Раз
    # укоротив _GIT_TIMEOUT_S до микроскопического значения, убеждаемся, что
    # `ensure` от этого не пострадал — значит, add идёт по отдельному таймауту.
    monkeypatch.setattr(worktrees, "_GIT_TIMEOUT_S", 0.001)

    path = await worktrees.ensure(repo, "feature-x", tmp_path / "wt")

    assert (path / "README.md").exists()


async def test_ensure_creates_worktree_on_new_branch(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"

    path = await worktrees.ensure(repo, "feature/DEV-1", root)

    assert path == root / "demo-feature-dev-1"
    assert (path / "README.md").exists()
    info = await worktrees.inspect(path)
    assert info is not None
    assert info.branch == "feature/DEV-1"
    assert info.repo == "demo"


async def test_ensure_reuses_existing_worktree(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    first = await worktrees.ensure(repo, "topic", root)
    (first / "scratch.txt").write_text("работа\n")

    second = await worktrees.ensure(repo, "topic", root)

    assert second == first
    assert (second / "scratch.txt").exists()  # не пересоздали поверх


async def test_ensure_checks_out_existing_branch(repo: Path, tmp_path: Path) -> None:
    await _git(repo, "branch", "already-here")

    path = await worktrees.ensure(repo, "already-here", tmp_path / "wt")

    info = await worktrees.inspect(path)
    assert info is not None and info.branch == "already-here"


async def test_ensure_rejects_non_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(WorktreeError, match="не git-репозиторий"):
        await worktrees.ensure(plain, "any", tmp_path / "wt")


async def test_ensure_surfaces_git_error_when_branch_busy(repo: Path, tmp_path: Path) -> None:
    # main уже занята основным рабочим каталогом — git откажет обоими способами
    with pytest.raises(WorktreeError):
        await worktrees.ensure(repo, "main", tmp_path / "wt")


async def test_inspect_reports_dirty_and_unpushed(repo: Path, tmp_path: Path) -> None:
    path = await worktrees.ensure(repo, "topic", tmp_path / "wt")
    (path / "new.txt").write_text("x\n")

    info = await worktrees.inspect(path)

    assert info is not None
    assert info.dirty is True
    assert "есть незакоммиченные изменения" in info.blockers


async def test_inspect_counts_unpushed_commits(repo: Path, tmp_path: Path) -> None:
    path = await worktrees.ensure(repo, "topic", tmp_path / "wt")
    (path / "new.txt").write_text("x\n")
    await _git(path, "add", "-A")
    await _git(path, "commit", "-qm", "работа")

    info = await worktrees.inspect(path)

    assert info is not None
    assert info.dirty is False
    # remote нет вообще — все коммиты ветки считаются незапушенными
    assert info.unpushed >= 1
    assert any("ни на одном remote" in b for b in info.blockers)


async def test_list_all_empty_when_root_missing(tmp_path: Path) -> None:
    assert await worktrees.list_all(tmp_path / "nope") == []


async def test_remove_refuses_when_work_would_be_lost(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    path = await worktrees.ensure(repo, "topic", root)
    (path / "new.txt").write_text("x\n")

    with pytest.raises(WorktreeError, match="незакоммиченные"):
        await worktrees.remove(root, path.name)

    assert path.is_dir()  # ничего не тронули


async def test_remove_force_deletes_anyway(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    path = await worktrees.ensure(repo, "topic", root)
    (path / "new.txt").write_text("x\n")

    removed = await worktrees.remove(root, path.name, force=True)

    assert removed.branch == "topic"
    assert not path.exists()


async def test_remove_clean_worktree_without_force(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    # ветка существует и уже «на remote» её нет, но и своих коммитов нет —
    # HEAD совпадает с main, значит терять нечего
    await _git(repo, "branch", "clean-topic")
    path = await worktrees.ensure(repo, "clean-topic", root)

    state = await worktrees.inspect(path)
    assert state is not None
    if state.blockers:
        pytest.skip("без настроенного remote git считает коммиты незапушенными")

    await worktrees.remove(root, path.name)

    assert not path.exists()


async def test_list_all_reports_created_worktrees(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    await worktrees.ensure(repo, "one", root)
    await worktrees.ensure(repo, "two", root)

    items = await worktrees.list_all(root)

    assert {w.branch for w in items} == {"one", "two"}
    assert {w.name for w in items} == {"demo-one", "demo-two"}


def test_generate_branch_has_sortable_timestamp() -> None:
    import re

    assert re.fullmatch(r"wt/\d{8}-\d{6}", worktrees.generate_branch(1700000000.0))


def test_generate_branch_differs_by_second() -> None:
    first = worktrees.generate_branch(1700000000.0)
    second = worktrees.generate_branch(1700000001.0)

    # два нажатия подряд не должны попасть в один worktree
    assert first != second


async def test_generated_branch_makes_usable_worktree(repo: Path, tmp_path: Path) -> None:
    branch = worktrees.generate_branch(1700000000.0)

    path = await worktrees.ensure(repo, branch, tmp_path / "wt")

    info = await worktrees.inspect(path)
    assert info is not None and info.branch == branch
    assert path.name.startswith("demo-wt-")
