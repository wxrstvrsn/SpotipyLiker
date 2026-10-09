"""Админка: /admin (настройки и доступ), /allow, /deny, запросы доступа и запросы на смену настроек."""

import contextlib
import logging
from collections.abc import Callable
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramObject,
    User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .runtime import OPTIONS, AccessControl, RuntimeSettings

log = logging.getLogger(__name__)

ADMIN_COMMANDS = [
    BotCommand(command='admin', description='Настройки бота и доступ'),
    BotCommand(command='allow', description='Открыть доступ: /allow ID'),
    BotCommand(command='deny', description='Закрыть доступ: /deny ID'),
]
PICK_USERS_REQUEST_ID = 1


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, access: AccessControl, event_from_user: User | None = None) -> bool:
        return event_from_user is not None and access.is_admin(event_from_user.id)


router = Router(name='admin')
router.message.filter(F.chat.type == 'private', IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminCallback(CallbackData, prefix='adm'):
    # menu | option | set | users | remove | add
    action: str
    key: str = ''
    value: str = ''


class AccessRequestCallback(CallbackData, prefix='req'):
    allow: bool
    user_id: int


class SettingCallback(CallbackData, prefix='set'):
    """Меню /settings для всех пользователей. action: menu | option | pick."""

    action: str
    key: str = ''
    value: str = ''


class SettingRequestCallback(CallbackData, prefix='sreq'):
    """Ответ админа на запрос пользователя изменить настройку."""

    allow: bool
    user_id: int
    key: str
    value: str


def _user_label(user_id: int, name: str | None) -> str:
    return f'{escape(name)} (<code>{user_id}</code>)' if name else f'<code>{user_id}</code>'


def _full_name(user: User) -> str:
    return user.full_name + (f' @{user.username}' if user.username else '')


# --- Экраны панели ---

def panel_view(runtime: RuntimeSettings, access: AccessControl) -> tuple[str, InlineKeyboardMarkup]:
    lines = ['⚙️ <b>Настройки бота</b>', '']
    builder = InlineKeyboardBuilder()
    for option in OPTIONS.values():
        value = option.label(runtime.get(option.key))
        lines.append(f'{option.title}: <b>{value}</b>')
        builder.row(InlineKeyboardButton(
            text=f'{option.title}: {value}',
            callback_data=AdminCallback(action='option', key=option.key).pack(),
        ))
    users = access.managed_users()
    lines.append(f'👥 Добавлено через бота: <b>{len(users)}</b>')
    builder.row(InlineKeyboardButton(text='👥 Пользователи', callback_data=AdminCallback(action='users').pack()))
    return '\n'.join(lines), builder.as_markup()


def _choices_view(
    runtime: RuntimeSettings, key: str, pick: Callable[[str], CallbackData], back: CallbackData, note: str = ''
) -> tuple[str, InlineKeyboardMarkup]:
    option = OPTIONS[key]
    current = runtime.get(key)
    text = f'{option.title}\nСейчас: <b>{option.label(current)}</b>'
    if option.hint:
        text += f'\n\n<i>{escape(option.hint)}</i>'
    if note:
        text += f'\n\n{note}'
    builder = InlineKeyboardBuilder()
    builder.row(*(
        InlineKeyboardButton(
            text=('✓ ' if choice == current else '') + option.label(choice),
            callback_data=pick(choice).pack(),
        )
        for choice in option.choices
    ))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data=back.pack()))
    return text, builder.as_markup()


def option_view(runtime: RuntimeSettings, key: str) -> tuple[str, InlineKeyboardMarkup]:
    return _choices_view(
        runtime, key,
        pick=lambda choice: AdminCallback(action='set', key=key, value=choice),
        back=AdminCallback(action='menu'),
    )


# --- /settings: настройки для всех, изменение — через одобрение админа ---

def settings_view(runtime: RuntimeSettings, is_admin: bool) -> tuple[str, InlineKeyboardMarkup]:
    lines = ['⚙️ <b>Настройки бота</b>', '']
    builder = InlineKeyboardBuilder()
    for option in OPTIONS.values():
        value = option.label(runtime.get(option.key))
        lines.append(f'{option.title}: <b>{value}</b>')
        builder.row(InlineKeyboardButton(
            text=f'{option.title}: {value}',
            callback_data=SettingCallback(action='option', key=option.key).pack(),
        ))
    if not is_admin:
        lines.append('\nНастройки общие для всех. Выберите новое значение — я отправлю запрос администратору.')
    return '\n'.join(lines), builder.as_markup()


def setting_option_view(runtime: RuntimeSettings, key: str, is_admin: bool) -> tuple[str, InlineKeyboardMarkup]:
    return _choices_view(
        runtime, key,
        pick=lambda choice: SettingCallback(action='pick', key=key, value=choice),
        back=SettingCallback(action='menu'),
        note='' if is_admin else 'Изменение вступит в силу после одобрения администратором.',
    )


async def notify_setting_request(
    bot: Bot, admins: frozenset[int], user: User, key: str, current: str, value: str
) -> None:
    option = OPTIONS[key]
    text = (
        '🔔 <b>Запрос на изменение настройки</b>\n'
        f'От: {escape(_full_name(user))} (<code>{user.id}</code>)\n'
        f'{option.title}: {option.label(current)} → <b>{option.label(value)}</b>'
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text='✅ Применить',
            callback_data=SettingRequestCallback(allow=True, user_id=user.id, key=key, value=value).pack(),
        ),
        InlineKeyboardButton(
            text='🚫 Отклонить',
            callback_data=SettingRequestCallback(allow=False, user_id=user.id, key=key, value=value).pack(),
        ),
    ]])
    for admin_id in admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except TelegramAPIError as e:
            log.warning('Не удалось отправить админу %s запрос на изменение настройки: %s', admin_id, e)


def users_view(access: AccessControl) -> tuple[str, InlineKeyboardMarkup]:
    lines = ['👥 <b>Доступ к боту</b>', '']
    for user_id in sorted(access.admins):
        lines.append(f'👑 <code>{user_id}</code> — админ')
    for user_id in sorted(access.env_users - access.admins):
        lines.append(f'🔒 <code>{user_id}</code> — задан в .env')
    builder = InlineKeyboardBuilder()
    users = access.managed_users()
    for user in users:
        lines.append(f'• {_user_label(user.user_id, user.name)}')
        builder.row(InlineKeyboardButton(
            text=f'❌ {user.name or user.user_id}',
            callback_data=AdminCallback(action='remove', value=str(user.user_id)).pack(),
        ))
    if not users:
        lines.append('Через бота пока никто не добавлен.')
    lines.append('\nДобавить — кнопкой ниже или <code>/allow ID</code>. Убрать — ❌ или <code>/deny ID</code>.')
    builder.row(InlineKeyboardButton(text='➕ Добавить', callback_data=AdminCallback(action='add').pack()))
    builder.row(InlineKeyboardButton(text='⬅️ Назад', callback_data=AdminCallback(action='menu').pack()))
    return '\n'.join(lines), builder.as_markup()


async def show_view(callback: CallbackQuery, view: tuple[str, InlineKeyboardMarkup]) -> None:
    text, markup = view
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if 'message is not modified' not in str(e):
                raise
    else:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


# --- Команды ---

@router.message(Command('admin'))
async def cmd_admin(message: Message, runtime: RuntimeSettings, access: AccessControl) -> None:
    text, markup = panel_view(runtime, access)
    await message.answer(text, reply_markup=markup)


async def _user_name(bot: Bot, user_id: int) -> str | None:
    # Имя известно, только если пользователь уже писал боту
    with contextlib.suppress(TelegramAPIError):
        chat = await bot.get_chat(user_id)
        return ' '.join(filter(None, (chat.first_name, chat.last_name))) or chat.username
    return None


async def _notify_granted(bot: Bot, user_id: int) -> None:
    # Пользователь, который ещё не писал боту, сообщение не получит — это нормально
    with contextlib.suppress(TelegramAPIError):
        await bot.send_message(user_id, '✅ Администратор открыл вам доступ к боту. Отправьте /start')


def _parse_ids(args: str | None) -> list[int] | None:
    parts = (args or '').replace(',', ' ').split()
    if not parts:
        return None
    try:
        return [int(part) for part in parts]
    except ValueError:
        return None


@router.message(Command('allow'))
async def cmd_allow(message: Message, command: CommandObject, bot: Bot, access: AccessControl) -> None:
    user_ids = _parse_ids(command.args)
    if user_ids is None:
        await message.answer(
            'Использование: <code>/allow 123456789</code> (можно несколько ID через пробел).\n'
            'ID человек увидит, если напишет боту, или выберите его из контактов: /admin → 👥 → ➕'
        )
        return
    lines = []
    for user_id in user_ids:
        name = await _user_name(bot, user_id)
        if access.is_admin(user_id) or user_id in access.env_users:
            lines.append(f'• {_user_label(user_id, name)} — уже есть доступ (админ или .env)')
        elif access.allow(user_id, name, message.from_user.id):
            lines.append(f'✅ {_user_label(user_id, name)} — доступ открыт')
            await _notify_granted(bot, user_id)
        else:
            lines.append(f'• {_user_label(user_id, name)} — доступ уже был')
    await message.answer('\n'.join(lines))


@router.message(Command('deny'))
async def cmd_deny(message: Message, command: CommandObject, access: AccessControl) -> None:
    user_ids = _parse_ids(command.args)
    if user_ids is None:
        await message.answer('Использование: <code>/deny 123456789</code>')
        return
    lines = []
    for user_id in user_ids:
        if access.is_admin(user_id):
            lines.append(f'• <code>{user_id}</code> — админ, его доступ задан в ADMIN_USER_IDS')
        elif access.revoke(user_id):
            lines.append(f'🚫 <code>{user_id}</code> — доступ закрыт')
        elif user_id in access.env_users:
            lines.append(f'• <code>{user_id}</code> — задан в .env (ALLOWED_USER_IDS), уберите его там')
        else:
            lines.append(f'• <code>{user_id}</code> — доступа и так не было')
    await message.answer('\n'.join(lines))


@router.message(F.users_shared)
async def on_users_shared(message: Message, bot: Bot, access: AccessControl) -> None:
    lines = []
    for shared in message.users_shared.users:
        name = ' '.join(filter(None, (shared.first_name, shared.last_name))) or shared.username
        if access.is_admin(shared.user_id) or shared.user_id in access.env_users:
            lines.append(f'• {_user_label(shared.user_id, name)} — уже есть доступ')
        elif access.allow(shared.user_id, name, message.from_user.id):
            lines.append(f'✅ {_user_label(shared.user_id, name)} — доступ открыт')
            await _notify_granted(bot, shared.user_id)
        else:
            lines.append(f'• {_user_label(shared.user_id, name)} — доступ уже был')
    await message.answer('\n'.join(lines) + '\n\nСписок: /admin', reply_markup=ReplyKeyboardRemove())


# --- Кнопки панели ---

@router.callback_query(AdminCallback.filter())
async def on_admin_button(
    callback: CallbackQuery, callback_data: AdminCallback, runtime: RuntimeSettings, access: AccessControl
) -> None:
    action = callback_data.action
    if action == 'option' and callback_data.key in OPTIONS:
        await show_view(callback, option_view(runtime, callback_data.key))
    elif action == 'set' and callback_data.key in OPTIONS:
        try:
            runtime.set(callback_data.key, callback_data.value)
        except ValueError:
            await callback.answer('Недопустимое значение', show_alert=True)
            return
        option = OPTIONS[callback_data.key]
        await callback.answer(f'✅ {option.title}: {option.label(callback_data.value)}')
        await show_view(callback, panel_view(runtime, access))
        return
    elif action == 'users':
        await show_view(callback, users_view(access))
    elif action == 'remove':
        removed = access.revoke(int(callback_data.value))
        await callback.answer('🚫 Доступ закрыт' if removed else 'Уже удалён')
        await show_view(callback, users_view(access))
        return
    elif action == 'add':
        await callback.bot.send_message(
            callback.from_user.id,
            'Нажмите «👤 Выбрать пользователей» внизу — откроется список контактов.\n'
            'Или пришлите <code>/allow ID</code>.',
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(
                    text='👤 Выбрать пользователей',
                    request_users=KeyboardButtonRequestUsers(
                        request_id=PICK_USERS_REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=10,
                        request_name=True,
                        request_username=True,
                    ),
                )]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
    else:
        await show_view(callback, panel_view(runtime, access))
    await callback.answer()


# --- Запросы доступа ---

async def notify_access_request(bot: Bot, admins: frozenset[int], user: User) -> None:
    text = (
        '🔔 <b>Запрос доступа к боту</b>\n'
        f'{escape(_full_name(user))}\n'
        f'ID: <code>{user.id}</code>'
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Разрешить', callback_data=AccessRequestCallback(allow=True, user_id=user.id).pack()),
        InlineKeyboardButton(text='🚫 Отклонить', callback_data=AccessRequestCallback(allow=False, user_id=user.id).pack()),
    ]])
    for admin_id in admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except TelegramAPIError as e:
            # Админ ещё ни разу не писал боту — Telegram не даст отправить ему сообщение
            log.warning('Не удалось уведомить админа %s о запросе доступа: %s', admin_id, e)


@router.callback_query(AccessRequestCallback.filter())
async def on_access_request(
    callback: CallbackQuery, callback_data: AccessRequestCallback, bot: Bot, access: AccessControl
) -> None:
    user_id = callback_data.user_id
    name = await _user_name(bot, user_id)
    if callback_data.allow:
        access.allow(user_id, name, callback.from_user.id)
        await _notify_granted(bot, user_id)
        result = f'✅ Доступ открыт: {_user_label(user_id, name)}'
    else:
        access.decline_request(user_id)
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(user_id, '🚫 Администратор отклонил запрос на доступ к боту.')
        result = f'🚫 Запрос отклонён: {_user_label(user_id, name)}'
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(result)
    await callback.answer()


@router.callback_query(SettingRequestCallback.filter())
async def on_setting_request(
    callback: CallbackQuery, callback_data: SettingRequestCallback, bot: Bot, runtime: RuntimeSettings
) -> None:
    option = OPTIONS.get(callback_data.key)
    if option is None or callback_data.value not in option.choices:
        await callback.answer('Такой настройки больше нет', show_alert=True)
        return
    runtime.resolve_request(callback_data.user_id, callback_data.key)
    change = f'{option.title} → {option.label(callback_data.value)}'
    if callback_data.allow:
        runtime.set(callback_data.key, callback_data.value)
        result, to_user = f'✅ Применено: {change}', f'✅ Администратор одобрил запрос: {change}'
    else:
        result, to_user = f'🚫 Отклонено: {change}', f'🚫 Администратор отклонил запрос: {change}'
    with contextlib.suppress(TelegramAPIError):
        await bot.send_message(callback_data.user_id, to_user)
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(f'{callback.message.html_text}\n\n{result}')
    await callback.answer(result)
