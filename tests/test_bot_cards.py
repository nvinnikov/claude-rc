from clauderc.bot import _died_text, _resume_keyboard
from clauderc.watch import Died


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
