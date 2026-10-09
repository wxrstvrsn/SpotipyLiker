import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .admin import notify_access_request
from .runtime import AccessControl

log = logging.getLogger(__name__)

DENIED_TEXT = '⛔ Этот бот приватный.'


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей с доступом (см. AccessControl).

    Остальным отвечает отказом, а админам присылает запрос доступа с кнопками — один раз,
    пока админ на него не ответит.
    """

    def __init__(self, access: AccessControl):
        self._access = access

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get('event_from_user')
        if user is not None and self._access.is_allowed(user.id):
            return await handler(event, data)

        log.info('Отклонён запрос от пользователя %s', user.id if user else None)
        if isinstance(event, CallbackQuery):
            await event.answer(DENIED_TEXT, show_alert=True)
        elif isinstance(event, Message) and user is not None:
            await event.answer(await self._denied_text(data['bot'], user))
        return None

    async def _denied_text(self, bot: Bot, user: User) -> str:
        if not self._access.admins:
            return f'{DENIED_TEXT}\nВаш Telegram ID: <code>{user.id}</code>'
        state = self._access.register_request(user.id)
        if state == 'pending':
            return f'{DENIED_TEXT}\nЗапрос на доступ уже отправлен администратору.'
        if state == 'declined':
            return f'{DENIED_TEXT}\nАдминистратор отклонил запрос на доступ.'
        await notify_access_request(bot, self._access.admins, user)
        return f'{DENIED_TEXT}\nЯ отправил администратору запрос на доступ — когда он его одобрит, я напишу.'
