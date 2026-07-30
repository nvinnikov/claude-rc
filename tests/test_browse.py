from pathlib import Path

import pytest
from clauderc.browse import BrowseError, change_dir, entries, is_repo


def test_entries_lists_subdirs_sorted(tmp_path: Path) -> None:
    for name in ("beta", "alpha", "gamma"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("x")

    found, total = entries(tmp_path)

    assert [p.name for p in found] == ["alpha", "beta", "gamma"]
    assert total == 3


def test_entries_hides_dotdirs_and_heavy_dirs(tmp_path: Path) -> None:
    for name in (".cache", "node_modules", "src"):
        (tmp_path / name).mkdir()

    found, total = entries(tmp_path)

    assert [p.name for p in found] == ["src"]
    assert total == 1


def test_entries_reports_total_when_truncated(tmp_path: Path) -> None:
    for i in range(30):
        (tmp_path / f"dir{i:02d}").mkdir()

    found, total = entries(tmp_path, limit=5)

    # обрезаем показ, но не врём про размер каталога
    assert len(found) == 5
    assert total == 30


def test_entries_on_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(BrowseError):
        entries(tmp_path / "nope")


def test_is_repo_detects_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert is_repo(tmp_path) is True
    assert is_repo(tmp_path.parent) is False


def test_change_dir_relative(tmp_path: Path) -> None:
    (tmp_path / "services").mkdir()

    assert change_dir(tmp_path, "services") == tmp_path / "services"


def test_change_dir_parent(tmp_path: Path) -> None:
    child = tmp_path / "services"
    child.mkdir()

    assert change_dir(child, "..") == tmp_path


def test_change_dir_absolute(tmp_path: Path) -> None:
    (tmp_path / "abs").mkdir()

    assert change_dir(Path("/"), str(tmp_path / "abs")) == tmp_path / "abs"


def test_change_dir_expands_home() -> None:
    assert change_dir(Path("/"), "~") == Path.home()


def test_change_dir_empty_keeps_current(tmp_path: Path) -> None:
    assert change_dir(tmp_path, "   ") == tmp_path


def test_change_dir_multi_segment(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)

    assert change_dir(tmp_path, "a/b") == tmp_path / "a" / "b"


def test_change_dir_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(BrowseError, match="не найден"):
        change_dir(tmp_path, "nope")


def test_change_dir_rejects_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")

    with pytest.raises(BrowseError):
        change_dir(tmp_path, "f.txt")
