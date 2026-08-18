import re
from pathlib import Path

from clauderc import bot as bot_module
from clauderc.bot import _died_text, _resume_keyboard
from clauderc.watch import Died

# Гашение обязано идти через Watcher — иначе намеренно погашенная сессия
# попадает в отчёт как упавшая (см. CLAUDE.md, «Точки гашения»). Ловим прямой
# вызов remote.kill_* мимо `watcher.`, чтобы регрессия не держалась на ручном
# грепе при следующей правке bot.py.
_DIRECT_KILL = re.compile(r"(?<!watcher\.)\b(?:kill_tmux|kill_all|kill_session)\(")


def test_resume_keyboard_lists_new_continue_and_conversations() -> None:
    markup = _resume_keyboard(
        [
            ("t0", "New session"),
            ("t1", "Continue last"),
            ("t2", "сделай релиз"),
        ]
    )
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["New session", "Continue last", "сделай релиз"]


def test_resume_keyboard_callback_data_fits_telegram_limit() -> None:
    # В callback_data влезает 64 байта; id диалога туда не кладём — только токен.
    markup = _resume_keyboard([("deadbeef", "и" * 200)])
    (button,) = markup.inline_keyboard[0]
    assert button.callback_data == "res:deadbeef"
    assert len((button.callback_data or "").encode()) <= 64


def test_died_text_names_the_directory() -> None:
    text = _died_text(Died(name="oms", tmux_name="rc-oms", cwd="/repos/oms"))
    assert "oms" in text
    assert "/repos/oms" in text


def test_died_text_escapes_html() -> None:
    text = _died_text(Died(name="a&b", tmux_name="rc-a-b", cwd="/repos/<x>"))
    assert "&amp;" in text
    assert "<x>" not in text


def test_no_direct_kill_calls_bypass_watcher() -> None:
    """Прямой вызов kill_tmux/kill_all/kill_session мимо watcher — тихий баг.

    Такая сессия гаснет, но Watcher о ней не узнаёт и на следующем опросе
    доложит о ней как об упавшей — пользователь получит карточку «сессия
    завершилась» сразу после того, как сам её погасил.
    """
    source = Path(bot_module.__file__).read_text(encoding="utf-8")
    offenders = _DIRECT_KILL.findall(source)
    assert not offenders, f"нашёл гашение мимо watcher: {offenders}"
