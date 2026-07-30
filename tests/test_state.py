from pathlib import Path

from tgclaude.state import State


def test_defaults_when_file_absent(tmp_path: Path):
    state = State(tmp_path / "state.json", tmp_path)

    assert state.cwd == tmp_path


def test_remembers_across_instances(tmp_path: Path):
    target = tmp_path / "services"
    target.mkdir()
    path = tmp_path / "nested" / "state.json"

    State(path, tmp_path).set_cwd(target)

    assert State(path, tmp_path).cwd == target


def test_falls_back_when_saved_dir_vanished(tmp_path: Path):
    gone = tmp_path / "gone"
    gone.mkdir()
    path = tmp_path / "state.json"
    State(path, tmp_path).set_cwd(gone)
    gone.rmdir()

    assert State(path, tmp_path).cwd == tmp_path


def test_falls_back_on_broken_file(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("не json")

    assert State(path, tmp_path).cwd == tmp_path


def test_cwd_recovers_if_dir_disappears_at_runtime(tmp_path: Path):
    gone = tmp_path / "gone"
    gone.mkdir()
    state = State(tmp_path / "state.json", tmp_path)
    state.set_cwd(gone)
    gone.rmdir()

    assert state.cwd == tmp_path


def test_unwritable_path_does_not_raise(tmp_path: Path):
    target = tmp_path / "services"
    target.mkdir()
    blocker = tmp_path / "blocked"
    blocker.write_text("это файл, а не каталог")
    state = State(blocker / "state.json", tmp_path)

    state.set_cwd(target)  # не сохранится, но и не упадёт

    assert state.cwd == target
