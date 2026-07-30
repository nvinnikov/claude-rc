from aiogram.types import User
from clauderc.bot import _is_authorized


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="test")


def test_authorized_owner() -> None:
    assert _is_authorized(_user(42), 42) is True


def test_rejects_other_user() -> None:
    assert _is_authorized(_user(7), 42) is False


def test_rejects_none_user() -> None:
    # пост из привязанного канала / анонимный админ → from_user is None → отказ
    assert _is_authorized(None, 42) is False
