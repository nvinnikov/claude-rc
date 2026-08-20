import subprocess
from pathlib import Path

import pytest
from clauderc import sync, worktrees
from clauderc.remote import RemoteSession


def _run(cwd: Path, *args: str) -> str:
    """git в тестах зовём синхронно: так проще читать сценарий подготовки."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "test")
    (path / "file.txt").write_text("one\n")
    _run(path, "add", ".")
    _run(path, "commit", "-qm", "first")
    return path


def _make_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Локальный «удалённый» репозиторий и клон: сеть не нужна."""
    source = _init_repo(tmp_path / "source")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    _run(clone, "config", "user.email", "test@example.com")
    _run(clone, "config", "user.name", "test")
    return bare, clone


def _commit_to_origin(tmp_path: Path, bare: Path, text: str) -> None:
    """Добавляет коммит в «удалённый» репозиторий через отдельный клон."""
    helper = tmp_path / f"helper-{text}"
    subprocess.run(["git", "clone", "-q", str(bare), str(helper)], check=True)
    _run(helper, "config", "user.email", "test@example.com")
    _run(helper, "config", "user.name", "test")
    (helper / "file.txt").write_text(text + "\n")
    _run(helper, "commit", "-qam", text)
    _run(helper, "push", "-q", "origin", "HEAD")


async def test_status_of_plain_directory_is_none(tmp_path: Path) -> None:
    assert await sync.status(tmp_path) is None


async def test_status_reports_branch_and_clean_state(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    got = await sync.status(repo)
    assert got is not None
    assert got.branch == "main"
    assert got.dirty is False
    assert got.detached is False


async def test_status_sees_uncommitted_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("changed\n")
    got = await sync.status(repo)
    assert got is not None and got.dirty is True


async def test_status_sees_untracked_files(tmp_path: Path) -> None:
    # Неотслеживаемый файл — тоже незакоммиченная работа: `git switch` его унесёт.
    repo = _init_repo(tmp_path / "repo")
    (repo / "new.txt").write_text("hello\n")
    got = await sync.status(repo)
    assert got is not None and got.dirty is True


async def test_status_counts_behind(tmp_path: Path) -> None:
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "second")
    _run(clone, "fetch", "-q")
    got = await sync.status(clone)
    assert got is not None
    assert got.behind == 1
    assert got.ahead == 0


async def test_status_counts_ahead(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    (clone / "file.txt").write_text("mine\n")
    _run(clone, "commit", "-qam", "mine")
    got = await sync.status(clone)
    assert got is not None
    assert got.ahead == 1
    assert got.behind == 0


async def test_status_without_upstream(tmp_path: Path) -> None:
    # Локальная ветка без upstream — не ошибка, а нормальное состояние.
    repo = _init_repo(tmp_path / "repo")
    got = await sync.status(repo)
    assert got is not None
    assert got.upstream is None
    assert got.ahead == 0 and got.behind == 0


async def test_status_detects_detached_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    head = _run(repo, "rev-parse", "HEAD").strip()
    _run(repo, "checkout", "-q", head)
    got = await sync.status(repo)
    assert got is not None and got.detached is True


async def test_sync_fast_forwards_behind_repo(tmp_path: Path) -> None:
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "second")

    result = await sync.sync(clone)
    assert result.outcome is sync.Outcome.updated
    assert (clone / "file.txt").read_text() == "second\n"


async def test_sync_reports_already_up_to_date(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    result = await sync.sync(clone)
    assert result.outcome is sync.Outcome.already


async def test_sync_never_touches_dirty_repo(tmp_path: Path) -> None:
    # Главное свойство модуля: незакоммиченная работа остаётся ровно как была.
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "second")
    (clone / "file.txt").write_text("моя работа\n")
    before = _run(clone, "status", "--porcelain")

    result = await sync.sync(clone)

    assert result.outcome is sync.Outcome.skipped
    assert (clone / "file.txt").read_text() == "моя работа\n"
    assert _run(clone, "status", "--porcelain") == before


async def test_sync_refuses_when_branches_diverged(tmp_path: Path) -> None:
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "theirs")
    (clone / "other.txt").write_text("mine\n")
    _run(clone, "add", ".")
    _run(clone, "commit", "-qm", "mine")
    head_before = _run(clone, "rev-parse", "HEAD").strip()

    result = await sync.sync(clone)

    assert result.outcome is sync.Outcome.failed
    # Перемотка невозможна — HEAD обязан остаться на месте.
    assert _run(clone, "rev-parse", "HEAD").strip() == head_before


async def test_sync_switches_to_existing_local_branch(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    _run(clone, "branch", "dev")

    result = await sync.sync(clone, branch="dev")

    assert result.branch == "dev"
    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"


async def test_sync_skips_branch_switch_when_live_session_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Живая RC-сессия могла бы не заметить подмену рабочего дерева под собой —
    # branch-switch там тихо переключал бы репозиторий мимо панели tmux, и
    # (см. И-2 в ревью) через worktree это ещё и снимало бы блокировку wtrm.
    _, clone = _make_origin_and_clone(tmp_path)
    _run(clone, "branch", "dev")

    async def fake_find(cwd: str) -> RemoteSession | None:
        return RemoteSession(name="x", tmux_name="rc-x", cwd=cwd, url="", created_at=0)

    monkeypatch.setattr(sync, "_find_session", fake_find)

    result = await sync.sync(clone, branch="dev")

    assert result.outcome is sync.Outcome.skipped
    assert "сесси" in result.detail.lower()
    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


async def test_sync_switches_branch_when_no_live_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Обычный случай (без живой сессии) не должен пострадать от нового чека.
    _, clone = _make_origin_and_clone(tmp_path)
    _run(clone, "branch", "dev")

    async def fake_find(cwd: str) -> RemoteSession | None:
        return None

    monkeypatch.setattr(sync, "_find_session", fake_find)

    await sync.sync(clone, branch="dev")

    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"


async def test_is_worktree_distinguishes_worktree_from_plain_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert await sync._is_worktree(repo) is False

    wt = await worktrees.ensure(repo, "feature", tmp_path / "wt")
    assert await sync._is_worktree(wt) is True


async def test_sync_skips_branch_switch_in_worktree_even_without_live_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Живая сессия — не единственная причина не трогать ветку в worktree:
    # переключение само по себе обнуляет unpushed и снимает защиту wtrm
    # (см. CLAUDE.md, «Грабли»), сессия тут ни при чём.
    _, clone = _make_origin_and_clone(tmp_path)
    _run(clone, "branch", "dev")
    wt = await worktrees.ensure(clone, "feature", tmp_path / "wt")
    (wt / "scratch.txt").write_text("работа\n")
    _run(wt, "add", "-A")
    _run(wt, "commit", "-qm", "работа")

    async def fake_find(cwd: str) -> RemoteSession | None:
        return None

    monkeypatch.setattr(sync, "_find_session", fake_find)

    before = await worktrees.inspect(wt)
    assert before is not None and before.blockers  # неотправленный коммит на feature

    result = await sync.sync(wt, branch="dev")

    assert result.outcome is sync.Outcome.skipped
    assert "worktree" in result.detail.lower()
    assert _run(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"
    after = await worktrees.inspect(wt)
    assert after is not None and after.blockers


async def test_sync_fetch_and_pull_use_their_own_longer_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # fetch/pull ходят в сеть и не обязаны укладываться в общий локальный
    # таймаут — тот же приём, что для `worktree add`. Шпионим за _git, а не
    # укорачиваем _GIT_TIMEOUT_S глобально: status() тоже зовёт _git без
    # переопределения, и общий таймаут обрушил бы его раньше, чем fetch/pull.
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "second")

    seen: list[tuple[str, float | None]] = []
    real_git = worktrees._git

    async def spy_git(cwd: Path, *args: str, timeout_s: float | None = None) -> tuple[int, str]:
        if args:
            seen.append((args[0], timeout_s))
        return await real_git(cwd, *args, timeout_s=timeout_s)

    monkeypatch.setattr(sync, "_git", spy_git)

    result = await sync.sync(clone)

    assert result.outcome is sync.Outcome.updated
    network_calls = [t for cmd, t in seen if cmd in ("fetch", "pull")]
    assert network_calls and all(t == sync._NETWORK_TIMEOUT_S for t in network_calls)


def test_rejected_paths_flags_non_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    typo = tmp_path / "typo"
    typo.mkdir()

    assert sync.rejected_paths([repo, typo]) == [typo]
    assert sync.rejected_paths([repo]) == []


async def test_sync_creates_branch_tracking_origin(tmp_path: Path) -> None:
    bare, clone = _make_origin_and_clone(tmp_path)
    helper = tmp_path / "helper-dev"
    subprocess.run(["git", "clone", "-q", str(bare), str(helper)], check=True)
    _run(helper, "config", "user.email", "t@e.com")
    _run(helper, "config", "user.name", "t")
    _run(helper, "checkout", "-qb", "dev")
    (helper / "dev.txt").write_text("dev\n")
    _run(helper, "add", ".")
    _run(helper, "commit", "-qm", "dev")
    _run(helper, "push", "-q", "origin", "dev")

    result = await sync.sync(clone, branch="dev")

    assert result.outcome in {sync.Outcome.updated, sync.Outcome.already}
    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"
    assert (clone / "dev.txt").exists()


async def test_sync_skips_when_branch_exists_nowhere(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    before = _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip()

    result = await sync.sync(clone, branch="no-such-branch")

    assert result.outcome is sync.Outcome.skipped
    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == before


async def test_sync_does_not_switch_dirty_repo(tmp_path: Path) -> None:
    # `git switch` перенёс бы незакоммиченное на другую ветку — молча и не туда.
    _, clone = _make_origin_and_clone(tmp_path)
    _run(clone, "branch", "dev")
    (clone / "file.txt").write_text("моя работа\n")

    result = await sync.sync(clone, branch="dev")

    assert result.outcome is sync.Outcome.skipped
    assert _run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (clone / "file.txt").read_text() == "моя работа\n"


async def test_sync_skips_detached_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    head = _run(repo, "rev-parse", "HEAD").strip()
    _run(repo, "checkout", "-q", head)

    result = await sync.sync(repo)
    assert result.outcome is sync.Outcome.skipped


async def test_sync_skips_branch_without_upstream(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    result = await sync.sync(repo)
    assert result.outcome is sync.Outcome.skipped
    assert "upstream" in result.detail.lower() or "origin" in result.detail.lower()


async def test_sync_without_fetch_does_not_reach_network(tmp_path: Path) -> None:
    # --no-fetch обязан не трогать сеть вообще, даже когда есть что перематывать.
    bare, clone = _make_origin_and_clone(tmp_path)
    _commit_to_origin(tmp_path, bare, "second")
    _run(clone, "fetch", "-q")  # клон узнаёт о новом коммите обычным способом, не через sync
    _run(clone, "remote", "set-url", "origin", str(tmp_path / "no-such-origin"))

    result = await sync.sync(clone, fetch=False)

    # Полезь sync в сеть (например, через `pull --ff-only`), origin оказался бы
    # недоступен и результат был бы failed, а не skipped.
    assert result.outcome is sync.Outcome.skipped
    assert "fetch" in result.detail.lower()


def test_list_repos_includes_cwd_itself_when_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "child" / ".git").mkdir(parents=True)

    result = sync.list_repos(tmp_path)

    assert result[0] == tmp_path.resolve()
    assert tmp_path.resolve() / "child" in result


def test_list_repos_dedupes_symlink_to_already_listed_repo(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / ".git").mkdir()
    (tmp_path / "link").symlink_to(real)

    result = sync.list_repos(tmp_path)

    assert result == [real.resolve()]


def test_resolve_targets_falls_back_to_process_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "alpha" / ".git").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert sync.resolve_targets([]) == [tmp_path.resolve() / "alpha"]


def test_resolve_targets_filters_and_dedupes_explicit_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)

    result = sync.resolve_targets([repo, not_repo, link])

    assert result == [repo.resolve()]


def test_display_names_disambiguates_same_named_repos() -> None:
    paths = [Path("/x/dirA/repo"), Path("/x/dirB/repo")]
    labels = sync.display_names(paths)
    assert labels[paths[0]] != labels[paths[1]]
    assert "repo" in labels[paths[0]] and "repo" in labels[paths[1]]


def test_display_names_keeps_bare_name_when_unique() -> None:
    labels = sync.display_names([Path("/x/alpha"), Path("/y/beta")])
    assert labels[Path("/x/alpha")] == "alpha"
    assert labels[Path("/y/beta")] == "beta"


async def test_sync_one_turns_exception_into_failed_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def boom(repo: Path, **kwargs: object) -> sync.SyncResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(sync, "sync", boom)

    result = await sync.sync_one(tmp_path)

    assert result.outcome is sync.Outcome.failed
    assert "boom" in result.detail


async def test_sync_all_keeps_other_results_when_one_repo_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flaky(repo: Path, **kwargs: object) -> sync.SyncResult:
        if repo.name == "beta":
            raise RuntimeError("boom")
        return sync.SyncResult(repo, sync.Outcome.already, "ok", "main")

    monkeypatch.setattr(sync, "sync", flaky)
    targets = [Path("/repos/alpha"), Path("/repos/beta"), Path("/repos/gamma")]

    results = await sync.sync_all(targets)

    outcomes = {r.path.name: r.outcome for r in results}
    assert outcomes["alpha"] is sync.Outcome.already
    assert outcomes["beta"] is sync.Outcome.failed
    assert outcomes["gamma"] is sync.Outcome.already


def test_resolve_targets_expands_tilde_before_checking_is_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Раньше is_repo проверялся до expanduser() — `sync ~/repo` не находил цель,
    # хотя `~/repo/.git` реально существовал.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert sync.resolve_targets([Path("~/repo")]) == [repo.resolve()]
