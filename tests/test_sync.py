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
