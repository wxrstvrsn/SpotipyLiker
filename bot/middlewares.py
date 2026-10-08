import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = logging.getLogger(__name__)

DENIED_TEXT = '⛔ Этот бот приватный.'


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей из ALLOWED_USER_IDS (если список задан)."""

    def __init__(self, allowed_user_ids: frozenset[int]):
        self._allowed = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get('event_from_user')
        if not self._allowed or (user is not None and user.id in self._allowed):
            return await handler(event, data)

        log.info('Отклонён запрос от пользователя %s', user.id if user else None)
        if isinstance(event, Message):
            await event.answer(f'{DENIED_TEXT}\nВаш Telegram ID: <code>{user.id if user else "?"}</code>')
        elif isinstance(event, CallbackQuery):
            await event.answer(DENIED_TEXT, show_alert=True)
        return None
