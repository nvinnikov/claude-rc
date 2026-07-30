from pathlib import Path

from clauderc.repos import discover, resolve


def _mkrepo(base: Path, rel: str) -> Path:
    path = base / rel
    (path / ".git").mkdir(parents=True)
    return path


def test_discover_finds_repo_three_levels_deep(tmp_path: Path) -> None:
    # так лежат сервисы: <зонт>/<монорепо>/services/<сервис>
    target = _mkrepo(tmp_path, "arch/services/oms")

    assert target in discover([tmp_path], depth=3)


def test_discover_stops_at_depth_limit(tmp_path: Path) -> None:
    target = _mkrepo(tmp_path, "a/b/c/deep")

    assert target not in discover([tmp_path], depth=3)


def test_discover_includes_root_without_git(tmp_path: Path) -> None:
    # рабочий зонт-каталог своего .git не имеет, но цель валидная
    assert tmp_path in discover([tmp_path])


def test_discover_skips_hidden_and_heavy_dirs(tmp_path: Path) -> None:
    hidden = _mkrepo(tmp_path, ".cache/thing")
    heavy = _mkrepo(tmp_path, "node_modules/pkg")

    found = discover([tmp_path])

    assert hidden not in found
    assert heavy not in found


def test_discover_ignores_missing_root(tmp_path: Path) -> None:
    assert discover([tmp_path / "no-such-dir"]) == []


def test_discover_deduplicates_overlapping_roots(tmp_path: Path) -> None:
    repo = _mkrepo(tmp_path, "oms")

    found = discover([tmp_path, tmp_path])

    assert found.count(repo) == 1


def test_resolve_prefers_exact_name(tmp_path: Path) -> None:
    exact = _mkrepo(tmp_path, "oms")
    _mkrepo(tmp_path, "oms-legacy")

    assert resolve("oms", discover([tmp_path])) == [exact]


def test_resolve_returns_all_partial_matches(tmp_path: Path) -> None:
    admin = _mkrepo(tmp_path, "city-manager-admin")
    gateway = _mkrepo(tmp_path, "city-manager-gateway")

    assert set(resolve("city-manager", discover([tmp_path]))) == {admin, gateway}


def test_resolve_is_case_insensitive(tmp_path: Path) -> None:
    repo = _mkrepo(tmp_path, "Mobile-App")

    assert resolve("mobile-app", discover([tmp_path])) == [repo]


def test_resolve_empty_query_matches_nothing(tmp_path: Path) -> None:
    _mkrepo(tmp_path, "oms")

    assert resolve("   ", discover([tmp_path])) == []
