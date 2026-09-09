import asyncio
import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe
from aiogram.types import Chat, Message, Update, User
from clauderc.bot import SessionGuard, install_session_guard

# Токен ненастоящий, но нужного формата: aiogram проверяет его при создании Bot.
# Сети ни один тест здесь не касается — сессия не делает запросов, пока её не
# позовут, а мы только подменяем и закрываем её.
TOKEN = "42:AAHtestTOKENtestTOKENtestTOKENtestTOK"


def _closed_connector() -> TelegramNetworkError:
    """Та самая ошибка из issue #15: коннектор закрыли под живым поллингом."""
    return TelegramNetworkError(
        method=GetMe(), message="ClientConnectionError: Connector is closed."
    )


def _message() -> Message:
    return Message(
        message_id=1,
        date=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=42, is_bot=False, first_name="x"),
        text="/pwd",
    )


async def test_network_error_replaces_broken_session() -> None:
    bot = Bot(token=TOKEN)
    broken: BaseSession = bot.session
    stops: list[int] = []

    async def stop() -> None:
        stops.append(1)

    guard = SessionGuard(bot, stop)
    await guard.on_error(_closed_connector())

    assert bot.session is not broken, "ответы уходили бы в ту же закрытую сессию"
    assert stops == [], "разовый обрыв связи не повод ронять процесс"
    assert guard.gave_up is False


async def test_repeated_errors_stop_polling() -> None:
    bot = Bot(token=TOKEN)
    stops: list[int] = []

    async def stop() -> None:
        stops.append(1)

    guard = SessionGuard(bot, stop, limit=3)
    for _ in range(3):
        await guard.on_error(_closed_connector())

    # Полумёртвым процесс не остаётся: поллинг остановлен, а main после этого
    # выходит с ненулевым кодом — дальше дело BotSupervisor/KeepAlive.
    assert stops == [1], "поллинг обязан остановиться ровно один раз"
    assert guard.gave_up is True


async def test_errors_outside_window_do_not_count() -> None:
    bot = Bot(token=TOKEN)
    stops: list[int] = []

    async def stop() -> None:
        stops.append(1)

    guard = SessionGuard(bot, stop, limit=3, window_s=0.02)
    for _ in range(3):
        await guard.on_error(_closed_connector())
        await asyncio.sleep(0.03)

    # Редкие обрывы за сутки не должны складываться в отказ: считаются только
    # ошибки, попавшие в одно окно.
    assert stops == []
    assert guard.gave_up is False


async def test_dispatcher_routes_network_error_to_guard() -> None:
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    guard = install_session_guard(bot, dp)
    broken: BaseSession = bot.session

    @dp.message()
    async def boom(message: Message) -> None:
        raise _closed_connector()

    await dp.feed_update(bot, Update(update_id=1, message=_message()))

    assert bot.session is not broken, "сетевая ошибка хендлера должна дойти до guard"
    assert guard.gave_up is False


async def test_dispatcher_leaves_other_errors_alone() -> None:
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    guard = install_session_guard(bot, dp)
    broken: BaseSession = bot.session

    @dp.message()
    async def boom(message: Message) -> None:
        raise ValueError("не про сеть")

    with pytest.raises(ValueError):
        await dp.feed_update(bot, Update(update_id=1, message=_message()))

    assert bot.session is broken, "сессию пересоздаём только из-за сетевой ошибки"
    assert guard.gave_up is False
