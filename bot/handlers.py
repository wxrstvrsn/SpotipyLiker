import asyncio
import contextlib
import logging
import secrets
from html import escape

import requests
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from spotipy import SpotifyException

from .config import Settings
from .downloader import TrackNotFoundError
from .importer import ImportFormatError, parse_library_file
from .keyboards import PAGE_SIZE, PageCallback, TrackCallback, total_pages, tracks_keyboard
from .library import Library, NoLibraryError
from .query import track_from_query
from .sender import TrackSender, TrackTooLargeError
from .spotify import AuthorizationError, SpotifyService, Track
from .storage import Storage

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')


def bot_commands(spotify_enabled: bool) -> list[BotCommand]:
    commands = [
        BotCommand(command='tracks', description='Любимые треки (по 10 на страницу)'),
        BotCommand(command='last', description='Скачать N последних добавленных треков'),
        BotCommand(command='import', description='Загрузить выгрузку библиотеки без Spotify API'),
        BotCommand(command='forget', description='Удалить загруженную выгрузку'),
    ]
    if spotify_enabled:
        commands += [
            BotCommand(command='login', description='Подключить Spotify'),
            BotCommand(command='logout', description='Отключить Spotify'),
        ]
    return commands + [BotCommand(command='help', description='Помощь')]


LIBRARY_ERRORS = (NoLibraryError, SpotifyException, requests.RequestException)
MAX_MESSAGE_LENGTH = 4096
# Bot API не даёт ботам скачивать файлы больше 20 МБ
MAX_IMPORT_FILE_SIZE = 20 * 1024 * 1024
SPOTIFY_DISABLED_TEXT = (
    'Ключи Spotify API не заданы, поэтому вход через Spotify недоступен.\n'
    'Загрузите выгрузку библиотеки: /import'
)
IMPORT_HELP = (
    '📥 <b>Библиотека без Spotify API</b>\n\n'
    'Пришлите мне файл одним из способов.\n\n'
    '<b>1. CSV из Exportify</b> — быстро, с длительностью и датами добавления\n'
    '• откройте https://exportify.app и войдите через Spotify\n'
    '• у «Liked Songs» нажмите Export — скачается CSV\n'
    '• отправьте этот файл сюда\n\n'
    '<b>2. Официальная выгрузка данных Spotify</b> — может идти несколько дней\n'
    '• https://www.spotify.com/account/privacy/ → «Скачать данные» → данные аккаунта\n'
    '• когда придёт письмо, отправьте сюда весь zip-архив или файл YourLibrary.json\n\n'
    'Новая выгрузка заменяет предыдущую. Удалить выгрузку: /forget'
)

# telegram_id -> state последнего запроса /login
_pending_logins: dict[int, str] = {}
# Пользователи, у которых сейчас идёт /last
_active_batches: set[int] = set()


def _library_error_text(error: Exception, library: Library) -> str:
    if isinstance(error, NoLibraryError):
        if library.spotify_enabled:
            return '📭 Библиотека не подключена: войдите в Spotify (/login) или загрузите выгрузку (/import).'
        return '📭 Сначала загрузите выгрузку своей библиотеки Spotify: /import'
    if isinstance(error, SpotifyException):
        if error.http_status == 401:
            return '🔑 Токен Spotify недействителен. Подключитесь заново: /login'
        if error.http_status == 403:
            return (
                '⛔ Spotify отказал в доступе. Если приложение в Development mode, добавьте свой '
                'e-mail Spotify в User Management на developer.spotify.com. '
                'Или работайте без API через выгрузку: /import\n'
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


def _help_text(settings: Settings, status: str) -> str:
    login = '/login, /logout — подключить или отключить Spotify\n' if settings.spotify_enabled else ''
    return (
        '🎧 <b>Spotify → SoundCloud</b>\n\n'
        'Беру ваши любимые треки из Spotify, нахожу их на SoundCloud и присылаю mp3 '
        'с тегами из Spotify: название, исполнитель, альбом, обложка.\n\n'
        '/tracks — листать любимые треки, нажмите на трек, чтобы получить его\n'
        f'/last [N] — скачать N последних добавленных треков '
        f'(по умолчанию {settings.last_tracks_default}, максимум {settings.last_tracks_max})\n'
        '/import — загрузить выгрузку библиотеки (работает без Spotify API)\n'
        '/forget — удалить загруженную выгрузку\n'
        f'{login}\n'
        '🔎 Или просто напишите <code>Исполнитель - Название</code> — найду и пришлю трек. '
        'Можно несколько строк, по треку на строку.\n\n'
        f'{status}'
    )


# --- Справка и авторизация ---

@router.message(CommandStart())
@router.message(Command('help'))
async def cmd_help(
    message: Message, settings: Settings, spotify: SpotifyService | None, library: Library
) -> None:
    user_id = message.from_user.id
    imported = library.imported_count(user_id)
    if imported:
        status = f'📥 Используется загруженная выгрузка: {imported} треков'
    elif spotify and await asyncio.to_thread(spotify.is_authorized, user_id):
        status = '✅ Spotify подключён'
    elif spotify:
        status = '🔑 Библиотека не подключена — начните с /login или /import'
    else:
        status = '📭 Библиотека не загружена — начните с /import'
    await message.answer(_help_text(settings, status))


@router.message(Command('login'))
async def cmd_login(message: Message, settings: Settings, spotify: SpotifyService | None) -> None:
    if spotify is None:
        await message.answer(SPOTIFY_DISABLED_TEXT)
        return
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
async def on_auth_response(message: Message, spotify: SpotifyService | None, db: Storage) -> None:
    user_id = message.from_user.id
    state = _pending_logins.get(user_id)
    if spotify is None:
        await message.answer(SPOTIFY_DISABLED_TEXT)
        return
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
    text = '✅ Spotify подключён! Откройте /tracks или скачайте последние треки: /last'
    if db.library_size(user_id):
        # Вход в Spotify — явный выбор живой библиотеки вместо старой выгрузки
        db.clear_library(user_id)
        text += '\nЗагруженная ранее выгрузка удалена, теперь треки берутся из Spotify.'
    await message.answer(text)


@router.message(Command('logout'))
async def cmd_logout(message: Message, spotify: SpotifyService | None) -> None:
    if spotify is None:
        await message.answer(SPOTIFY_DISABLED_TEXT)
        return
    _pending_logins.pop(message.from_user.id, None)
    await asyncio.to_thread(spotify.logout, message.from_user.id)
    await message.answer('👋 Spotify отключён. Подключить снова: /login')


# --- Список треков ---

async def _load_page(library: Library, user_id: int, page: int) -> tuple[list[Track], int, int, int]:
    page = max(page, 0)
    tracks, total = await library.page(user_id, PAGE_SIZE, page * PAGE_SIZE)
    pages = total_pages(total)
    if page >= pages and total:
        # Библиотека уменьшилась, пока список был открыт
        page = pages - 1
        tracks, total = await library.page(user_id, PAGE_SIZE, page * PAGE_SIZE)
        pages = total_pages(total)
    return tracks, page, pages, total


def _page_text(total: int, page: int, pages: int) -> str:
    if not total:
        return 'В «Любимых треках» пока пусто.'
    return (
        f'❤️ <b>Любимые треки</b>: {total}\n'
        f'Страница {page + 1} из {pages}. Нажмите на трек, чтобы получить mp3.'
    )


@router.message(Command('tracks'))
async def cmd_tracks(message: Message, library: Library) -> None:
    try:
        tracks, page, pages, total = await _load_page(library, message.from_user.id, 0)
    except LIBRARY_ERRORS as e:
        await message.answer(_library_error_text(e, library))
        return
    await message.answer(
        _page_text(total, page, pages),
        reply_markup=tracks_keyboard(tracks, page, pages) if total else None,
    )


@router.callback_query(PageCallback.filter())
async def on_page(
    callback: CallbackQuery, callback_data: PageCallback, bot: Bot, library: Library
) -> None:
    try:
        tracks, page, pages, total = await _load_page(library, callback.from_user.id, callback_data.page)
    except LIBRARY_ERRORS as e:
        await callback.answer(_library_error_text(e, library).split('\n')[0], show_alert=True)
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


async def _deliver_one(bot: Bot, sender: TrackSender, chat_id: int, track: Track) -> None:
    """Один трек со статусом «Ищу и скачиваю…» и сообщением об ошибке."""
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


async def _deliver_many(message: Message, bot: Bot, sender: TrackSender, tracks: list[Track], title: str) -> None:
    """Треки по очереди, с прогрессом в одном сообщении и сводкой в конце."""
    progress = await message.answer(title)
    failed: list[str] = []
    for index, track in enumerate(tracks, 1):
        with contextlib.suppress(TelegramBadRequest):
            await progress.edit_text(f'⏳ {index}/{len(tracks)}: <b>{escape(track.display_name)}</b>')
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


@router.callback_query(TrackCallback.filter())
async def on_track(
    callback: CallbackQuery, callback_data: TrackCallback, bot: Bot, sender: TrackSender, db: Storage
) -> None:
    track = db.get_track(callback_data.track_id)
    if track is None:
        await callback.answer('Трек не найден, откройте список заново: /tracks', show_alert=True)
        return
    await callback.answer(f'⏳ {track.display_name}'[:200])
    await _deliver_one(bot, sender, callback.from_user.id, track)


@router.message(Command('last'))
async def cmd_last(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
    library: Library,
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
            tracks = await library.latest(user_id, count)
        except LIBRARY_ERRORS as e:
            await message.answer(_library_error_text(e, library))
            return
        if not tracks:
            await message.answer('В «Любимых треках» пока пусто.')
            return
        # От старых к новым: самый свежий трек окажется внизу переписки
        await _deliver_many(
            message, bot, sender, list(reversed(tracks)), f'⏳ Скачиваю {len(tracks)} последних треков…'
        )
    finally:
        _active_batches.discard(user_id)


# --- Импорт выгрузки (режим без Spotify API) ---

@router.message(Command('import'))
async def cmd_import(message: Message, bot: Bot, db: Storage) -> None:
    if message.document:
        # Файл с подписью «/import»
        await on_document(message, bot, db)
        return
    await message.answer(IMPORT_HELP, link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(F.document)
async def on_document(message: Message, bot: Bot, db: Storage) -> None:
    document = message.document
    if document.file_size and document.file_size > MAX_IMPORT_FILE_SIZE:
        await message.answer(
            'Файл больше 20 МБ — Telegram не даст боту его скачать. '
            'Достаньте из архива и пришлите только YourLibrary.json.'
        )
        return
    try:
        buffer = await bot.download(document)
        result = await asyncio.to_thread(parse_library_file, document.file_name or '', buffer.read())
    except ImportFormatError as e:
        await message.answer(f'❌ {escape(str(e))}\nКак подготовить файл: /import')
        return
    except Exception:
        log.exception('Не удалось импортировать %s', document.file_name)
        await message.answer('❌ Не получилось прочитать файл. Как подготовить выгрузку: /import')
        return

    db.replace_library(message.from_user.id, result.tracks)
    order = (
        'Порядок — по дате добавления, /last возьмёт самые свежие.'
        if result.sorted_by_date else
        'В файле нет дат добавления, поэтому порядок — как в файле.'
    )
    await message.answer(
        f'✅ Загружено треков: {len(result.tracks)}. {order}\n\n'
        'Листать: /tracks · скачать последние: /last\n'
        'Пока выгрузка загружена, треки берутся из неё. Удалить: /forget'
    )


@router.message(Command('forget'))
async def cmd_forget(message: Message, db: Storage, library: Library) -> None:
    user_id = message.from_user.id
    if not db.library_size(user_id):
        await message.answer('Загруженной выгрузки нет. Загрузить: /import')
        return
    db.clear_library(user_id)
    next_step = (
        'Теперь треки берутся из Spotify (если не подключён — /login).'
        if library.spotify_enabled else
        'Загрузить новую: /import'
    )
    await message.answer(f'🗑 Выгрузка удалена. {next_step}')


# --- Поиск по тексту: «Исполнитель - Название» ---

@router.message(F.text, ~F.text.startswith('/'))
async def on_text_query(message: Message, bot: Bot, settings: Settings, sender: TrackSender) -> None:
    lines = [line.strip() for line in message.text.splitlines() if line.strip()]
    if any(line.lower().startswith(('http://', 'https://')) for line in lines):
        await message.answer('Ссылки я не открываю. Напишите <code>Исполнитель - Название</code>.')
        return
    tracks = [track for track in map(track_from_query, lines) if track]
    if not tracks:
        return
    if len(tracks) > settings.last_tracks_max:
        await message.answer(f'За раз — не больше {settings.last_tracks_max} треков.')
        return
    if len(tracks) == 1:
        await _deliver_one(bot, sender, message.chat.id, tracks[0])
        return

    user_id = message.from_user.id
    if user_id in _active_batches:
        await message.answer('⏳ Предыдущая подборка ещё скачивается, дождитесь её окончания.')
        return
    _active_batches.add(user_id)
    try:
        await _deliver_many(message, bot, sender, tracks, f'⏳ Ищу {len(tracks)} треков…')
    finally:
        _active_batches.discard(user_id)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer('Не понял 🤔 Список команд: /help')
