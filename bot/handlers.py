import asyncio
import contextlib
import logging
import secrets
from html import escape

import requests
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from spotipy import SpotifyException

from .config import Settings
from .downloader import TrackNotFoundError
from .keyboards import PAGE_SIZE, PageCallback, TrackCallback, total_pages, tracks_keyboard
from .sender import TrackSender, TrackTooLargeError
from .spotify import AuthorizationError, NotAuthorizedError, SpotifyService, Track
from .storage import Storage

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')

COMMANDS = [
    BotCommand(command='tracks', description='Любимые треки Spotify (по 10 на страницу)'),
    BotCommand(command='last', description='Скачать N последних добавленных треков'),
    BotCommand(command='login', description='Подключить Spotify'),
    BotCommand(command='logout', description='Отключить Spotify'),
    BotCommand(command='help', description='Помощь'),
]

SPOTIFY_ERRORS = (NotAuthorizedError, SpotifyException, requests.RequestException)
MAX_MESSAGE_LENGTH = 4096

# telegram_id -> state последнего запроса /login
_pending_logins: dict[int, str] = {}
# Пользователи, у которых сейчас идёт /last
_active_batches: set[int] = set()


def _spotify_error_text(error: Exception) -> str:
    if isinstance(error, NotAuthorizedError):
        return '🔑 Spotify не подключён. Отправьте /login'
    if isinstance(error, SpotifyException):
        if error.http_status == 401:
            return '🔑 Токен Spotify недействителен. Подключитесь заново: /login'
        if error.http_status == 403:
            return (
                '⛔ Spotify отказал в доступе. Если приложение в Development mode, добавьте свой '
                'e-mail Spotify в User Management на developer.spotify.com.\n'
                f'<i>{escape(str(error.msg))}</i>'
            )
        if error.http_status == 429:
            return '⏳ Слишком много запросов к Spotify, попробуйте чуть позже.'
    log.warning('Ошибка Spotify: %s', error)
    return '❌ Spotify сейчас недоступен, попробуйте позже.'


def _failure_text(error: Exception) -> str:
    if isinstance(error, TrackNotFoundError):
        return 'не нашёл подходящий трек'
    if isinstance(error, TrackTooLargeError):
        return 'файл больше 50 МБ, Telegram не даст его отправить'
    return 'ошибка при скачивании'


def _help_text(settings: Settings, authorized: bool) -> str:
    status = '✅ Spotify подключён' if authorized else '🔑 Spotify не подключён — начните с /login'
    return (
        '🎧 <b>Spotify → SoundCloud</b>\n\n'
        'Беру ваши любимые треки из Spotify, нахожу их на SoundCloud и присылаю mp3 '
        'с тегами из Spotify: название, исполнитель, альбом, обложка.\n\n'
        '/tracks — листать любимые треки, нажмите на трек, чтобы получить его\n'
        f'/last [N] — скачать N последних добавленных треков '
        f'(по умолчанию {settings.last_tracks_default}, максимум {settings.last_tracks_max})\n'
        '/login — подключить Spotify\n'
        '/logout — отключить Spotify\n\n'
        f'{status}'
    )


# --- Справка и авторизация ---

@router.message(CommandStart())
@router.message(Command('help'))
async def cmd_help(message: Message, settings: Settings, spotify: SpotifyService) -> None:
    authorized = await asyncio.to_thread(spotify.is_authorized, message.from_user.id)
    await message.answer(_help_text(settings, authorized))


@router.message(Command('login'))
async def cmd_login(message: Message, settings: Settings, spotify: SpotifyService) -> None:
    user_id = message.from_user.id
    state = secrets.token_urlsafe(16)
    _pending_logins[user_id] = state
    url = spotify.authorize_url(user_id, state)
    await message.answer(
        '1. Нажмите кнопку ниже и разрешите доступ к библиотеке.\n'
        f'2. Spotify перенаправит на <code>{escape(settings.spotify_redirect_uri)}</code> — '
        'страница не откроется, это нормально.\n'
        '3. Скопируйте адрес из адресной строки браузера <b>целиком</b> и пришлите его сюда.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔐 Войти через Spotify', url=url)],
        ]),
    )


@router.message(F.text.regexp(r'[?&](?:code|error)=', mode='search'))
async def on_auth_response(message: Message, spotify: SpotifyService) -> None:
    user_id = message.from_user.id
    state = _pending_logins.get(user_id)
    if state is None:
        await message.answer('Сначала отправьте /login, а затем пришлите ссылку.')
        return
    try:
        await asyncio.to_thread(spotify.complete_authorization, user_id, message.text, state)
    except (AuthorizationError, requests.RequestException) as e:
        await message.answer(f'❌ Не удалось подключить Spotify: {escape(str(e))}\nПопробуйте ещё раз: /login')
        return
    _pending_logins.pop(user_id, None)
    # В сообщении одноразовый код авторизации — убираем его из переписки
    with contextlib.suppress(TelegramBadRequest):
        await message.delete()
    await message.answer('✅ Spotify подключён! Откройте /tracks или скачайте последние треки: /last')


@router.message(Command('logout'))
async def cmd_logout(message: Message, spotify: SpotifyService) -> None:
    _pending_logins.pop(message.from_user.id, None)
    await asyncio.to_thread(spotify.logout, message.from_user.id)
    await message.answer('👋 Spotify отключён. Подключить снова: /login')


# --- Список треков ---

async def _load_page(
    spotify: SpotifyService, db: Storage, user_id: int, page: int
) -> tuple[list[Track], int, int, int]:
    page = max(page, 0)
    tracks, total = await asyncio.to_thread(spotify.saved_tracks, user_id, PAGE_SIZE, page * PAGE_SIZE)
    pages = total_pages(total)
    if page >= pages and total:
        # Библиотека уменьшилась, пока список был открыт
        page = pages - 1
        tracks, total = await asyncio.to_thread(spotify.saved_tracks, user_id, PAGE_SIZE, page * PAGE_SIZE)
        pages = total_pages(total)
    db.save_tracks(tracks)
    return tracks, page, pages, total


def _page_text(total: int, page: int, pages: int) -> str:
    if not total:
        return 'В «Любимых треках» Spotify пока пусто.'
    return (
        f'❤️ <b>Любимые треки</b>: {total}\n'
        f'Страница {page + 1} из {pages}. Нажмите на трек, чтобы получить mp3.'
    )


@router.message(Command('tracks'))
async def cmd_tracks(message: Message, spotify: SpotifyService, db: Storage) -> None:
    try:
        tracks, page, pages, total = await _load_page(spotify, db, message.from_user.id, 0)
    except SPOTIFY_ERRORS as e:
        await message.answer(_spotify_error_text(e))
        return
    await message.answer(
        _page_text(total, page, pages),
        reply_markup=tracks_keyboard(tracks, page, pages) if total else None,
    )


@router.callback_query(PageCallback.filter())
async def on_page(
    callback: CallbackQuery, callback_data: PageCallback, bot: Bot, spotify: SpotifyService, db: Storage
) -> None:
    try:
        tracks, page, pages, total = await _load_page(spotify, db, callback.from_user.id, callback_data.page)
    except SPOTIFY_ERRORS as e:
        await callback.answer(_spotify_error_text(e).split('\n')[0], show_alert=True)
        return
    text = _page_text(total, page, pages)
    markup = tracks_keyboard(tracks, page, pages) if total else None
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if 'message is not modified' not in str(e):
                raise
    else:
        # Слишком старое сообщение — редактировать нельзя, присылаем новое
        await bot.send_message(callback.from_user.id, text, reply_markup=markup)
    await callback.answer()


# --- Скачивание ---

async def _send_track(bot: Bot, sender: TrackSender, chat_id: int, track: Track) -> None:
    async with ChatActionSender.upload_document(bot=bot, chat_id=chat_id):
        await sender.send(bot, chat_id, track)


@router.callback_query(TrackCallback.filter())
async def on_track(
    callback: CallbackQuery, callback_data: TrackCallback, bot: Bot, sender: TrackSender, db: Storage
) -> None:
    track = db.get_track(callback_data.track_id)
    if track is None:
        await callback.answer('Трек не найден, откройте список заново: /tracks', show_alert=True)
        return
    await callback.answer(f'⏳ {track.display_name}'[:200])

    chat_id = callback.from_user.id
    status = None
    if not sender.is_cached(track.id):
        status = await bot.send_message(chat_id, f'🔎 Ищу и скачиваю: <b>{escape(track.display_name)}</b>…')
    try:
        await _send_track(bot, sender, chat_id, track)
    except Exception as e:
        if not isinstance(e, (TrackNotFoundError, TrackTooLargeError)):
            log.exception('Не удалось отправить %s', track.display_name)
        text = f'😔 <b>{escape(track.display_name)}</b>: {_failure_text(e)}'
        if status:
            await status.edit_text(text)
        else:
            await bot.send_message(chat_id, text)
        return
    if status:
        with contextlib.suppress(TelegramBadRequest):
            await status.delete()


@router.message(Command('last'))
async def cmd_last(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
    spotify: SpotifyService,
    db: Storage,
    sender: TrackSender,
) -> None:
    count = settings.last_tracks_default
    if command.args:
        try:
            count = int(command.args.split()[0])
        except ValueError:
            await message.answer('Использование: <code>/last 10</code>')
            return
        if not 1 <= count <= settings.last_tracks_max:
            await message.answer(f'Количество должно быть от 1 до {settings.last_tracks_max}.')
            return

    user_id = message.from_user.id
    if user_id in _active_batches:
        await message.answer('⏳ Предыдущая подборка ещё скачивается, дождитесь её окончания.')
        return
    _active_batches.add(user_id)
    try:
        try:
            tracks = await asyncio.to_thread(spotify.latest_tracks, user_id, count)
        except SPOTIFY_ERRORS as e:
            await message.answer(_spotify_error_text(e))
            return
        if not tracks:
            await message.answer('В «Любимых треках» Spotify пока пусто.')
            return
        db.save_tracks(tracks)

        progress = await message.answer(f'⏳ Скачиваю {len(tracks)} последних треков…')
        failed: list[str] = []
        # От старых к новым: самый свежий трек окажется внизу переписки
        for index, track in enumerate(reversed(tracks), 1):
            with contextlib.suppress(TelegramBadRequest):
                await progress.edit_text(
                    f'⏳ {index}/{len(tracks)}: <b>{escape(track.display_name)}</b>'
                )
            try:
                await _send_track(bot, sender, message.chat.id, track)
            except Exception as e:
                if not isinstance(e, (TrackNotFoundError, TrackTooLargeError)):
                    log.exception('Не удалось отправить %s', track.display_name)
                failed.append(f'• {escape(track.display_name)} — {_failure_text(e)}')

        with contextlib.suppress(TelegramBadRequest):
            await progress.delete()
        summary = f'✅ Готово: {len(tracks) - len(failed)} из {len(tracks)}.'
        if failed:
            summary += '\n\nНе получилось:\n' + '\n'.join(failed)
        if len(summary) > MAX_MESSAGE_LENGTH:
            summary = summary[: MAX_MESSAGE_LENGTH - 1].rsplit('\n', 1)[0] + '\n…'
        await message.answer(summary)
    finally:
        _active_batches.discard(user_id)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer('Не понял 🤔 Список команд: /help')
