import io
import urllib.error
from pathlib import Path

import pytest
from clauderc import update
from clauderc.update import Channel, Install, detect, is_newer, latest_release, plan


def test_detect_brew_reads_the_formula_from_the_cellar_path() -> None:
    install = detect(Path("/opt/homebrew/Cellar/claude-rc/0.2.0/libexec"))
    assert install == Install(Channel.brew, formula="claude-rc")


def test_detect_clone_by_the_pyproject_next_to_the_environment(tmp_path: Path) -> None:
    # Клон и `uv tool` различает не разбор пути, а файл-признак рядом.
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert detect(tmp_path / ".venv") == Install(Channel.clone, root=tmp_path)


def test_detect_uv_tool(tmp_path: Path) -> None:
    assert detect(tmp_path / "share/uv/tools/claude-rc") == Install(Channel.uv_tool)


def test_detect_unknown_environment(tmp_path: Path) -> None:
    assert detect(tmp_path / "somewhere/venv") == Install(Channel.unknown)


def test_plan_clone_targets_the_app_only_when_it_is_installed() -> None:
    install = Install(Channel.clone, root=Path("/src/claude-rc"))
    assert plan(install, app_installed=True) == [["make", "-C", "/src/claude-rc", "install"]]
    # Приложение собирается через swift — на машине без графики целиться в него незачем.
    assert plan(install, app_installed=False) == [["make", "-C", "/src/claude-rc", "install-tool"]]


def test_plan_uv_tool_reinstalls_from_git() -> None:
    (command,) = plan(Install(Channel.uv_tool), app_installed=False)
    assert command == ["uv", "tool", "install", "--force", f"git+{update.REPO_URL}"]


def test_plan_brew_upgrades_the_cask_too_when_the_app_is_there() -> None:
    install = Install(Channel.brew, formula="claude-rc")
    assert plan(install, app_installed=False) == [["brew", "upgrade", "nvinnikov/tap/claude-rc"]]
    assert plan(install, app_installed=True) == [
        ["brew", "upgrade", "nvinnikov/tap/claude-rc"],
        ["brew", "upgrade", "--cask", "nvinnikov/tap/claude-rc-app"],
    ]


def test_plan_has_nothing_for_an_unrecognised_install() -> None:
    assert plan(Install(Channel.unknown), app_installed=True) == []


def test_plan_brew_without_a_formula_has_nothing_to_upgrade() -> None:
    # Путь вида .../Cellar без имени формулы: угадывать её нельзя.
    assert plan(Install(Channel.brew), app_installed=False) == []


def test_is_newer_compares_release_numbers() -> None:
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("0.2.1", "0.2.0")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.9", "0.2.0")


def test_is_newer_ignores_the_tag_prefix() -> None:
    assert is_newer("v0.3.0", "0.2.0")


def test_is_newer_stays_silent_on_a_version_it_cannot_parse() -> None:
    # Соврать «есть обновление» хуже, чем промолчать: человек пойдёт
    # переустанавливать по несуществующему поводу.
    assert not is_newer("0.3.0", "unknown (пакет не установлен)")
    assert not is_newer("latest", "0.2.0")


def _answer(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    class _Response(io.BytesIO):
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    def fake_urlopen(url: str, timeout: float = 0.0) -> _Response:
        return _Response(body)

    monkeypatch.setattr(update.request, "urlopen", fake_urlopen)


def test_latest_release_reads_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, b'{"tag_name": "v0.3.0"}')
    assert latest_release() == "0.3.0"


def test_latest_release_is_none_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # Недоступная сеть — не ошибка обновления: обновиться можно и вслепую.
    def fail(url: str, timeout: float = 0.0) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(update.request, "urlopen", fail)
    assert latest_release() is None


def test_latest_release_is_none_on_a_body_it_did_not_expect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(monkeypatch, b"<html>rate limited</html>")
    assert latest_release() is None


def test_latest_release_is_none_when_the_tag_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, b'{"message": "Not Found"}')
    assert latest_release() is None
