import json
import os
from pathlib import Path

import pytest
from clauderc import history


def _write(directory: Path, session_id: str, cwd: str, first_user: str, mtime: float) -> Path:
    """Кладёт правдоподобный jsonl: служебные записи, потом первое сообщение."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    records = [
        {"type": "last-prompt", "sessionId": session_id},
        {"type": "mode", "sessionId": session_id},
        {"type": "user", "cwd": cwd, "message": {"role": "user", "content": first_user}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setattr(history.paths, "claude_projects", lambda: root)
    return root


def test_slug_replaces_each_non_alnum_separately() -> None:
    # Проверено на реальном каталоге: `/` и `.` дают два дефиса подряд.
    # Это НЕ то же самое, что _UNSAFE в remote.py, который серии схлопывает.
    assert history.slug("/Users/n/.tg-claude-worktrees") == "-Users-n--tg-claude-worktrees"
    assert history.slug("/a/b") == "-a-b"


def test_missing_directory_gives_empty_list(projects: Path) -> None:
    assert history.conversations("/no/such/place") == []


def test_reads_conversation_with_preview(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(projects / history.slug(str(cwd)), "aaa", str(cwd), "Работает сейчас бот?", 1000.0)

    (found,) = history.conversations(str(cwd))
    assert found.session_id == "aaa"
    assert found.cwd == str(cwd)
    assert found.preview == "Работает сейчас бот?"
    assert found.updated_at == 1000.0


def test_sorted_by_mtime_desc_and_limited(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "old", str(cwd), "старый", 1000.0)
    _write(directory, "mid", str(cwd), "средний", 2000.0)
    _write(directory, "new", str(cwd), "новый", 3000.0)

    assert [c.session_id for c in history.conversations(str(cwd))] == ["new", "mid", "old"]
    assert [c.session_id for c in history.conversations(str(cwd), limit=2)] == ["new", "mid"]


def test_skips_file_from_another_cwd(projects: Path, tmp_path: Path) -> None:
    # Слаг неоднозначен: `/a/b` и `/a.b` дают одно имя каталога. Поле cwd внутри
    # файла — единственная надёжная проверка.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "mine", str(cwd), "моё", 2000.0)
    _write(directory, "alien", "/somewhere/else", "чужое", 3000.0)

    assert [c.session_id for c in history.conversations(str(cwd))] == ["mine"]


def test_skips_broken_json(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "good", str(cwd), "живой", 1000.0)
    (directory / "broken.jsonl").write_text("{не json\n")

    assert [c.session_id for c in history.conversations(str(cwd))] == ["good"]


def test_file_without_cwd_is_skipped(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    (directory / "empty.jsonl").write_text(json.dumps({"type": "mode"}) + "\n")

    assert history.conversations(str(cwd)) == []


def test_preview_from_content_blocks(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    record = {
        "type": "user",
        "cwd": str(cwd),
        "message": {"content": [{"type": "text", "text": "  собери\n  релиз  "}]},
    }
    (directory / "blocks.jsonl").write_text(json.dumps(record) + "\n")

    (found,) = history.conversations(str(cwd))
    assert found.preview == "собери релиз"


def test_preview_is_trimmed(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(projects / history.slug(str(cwd)), "long", str(cwd), "я" * 200, 1000.0)

    (found,) = history.conversations(str(cwd))
    assert len(found.preview) == history.PREVIEW_CHARS + 1
    assert found.preview.endswith("…")


def test_conversation_without_user_message_gets_placeholder(projects: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    directory.mkdir(parents=True)
    record = {"type": "assistant", "cwd": str(cwd)}
    (directory / "quiet.jsonl").write_text(json.dumps(record) + "\n")

    (found,) = history.conversations(str(cwd))
    assert found.preview == "без названия"


def test_subdirectories_are_ignored(projects: Path, tmp_path: Path) -> None:
    # Рядом с jsonl лежат каталоги, названные тем же uuid.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = projects / history.slug(str(cwd))
    _write(directory, "real", str(cwd), "настоящий", 1000.0)
    (directory / "decoy.jsonl").mkdir()

    assert [c.session_id for c in history.conversations(str(cwd))] == ["real"]
