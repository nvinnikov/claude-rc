import subprocess
from pathlib import Path

from clauderc import sync


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
