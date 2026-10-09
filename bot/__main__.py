import asyncio
import contextlib
import logging
import os
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommandScopeChat
from dotenv import load_dotenv

from .admin import ADMIN_COMMANDS
from .admin import router as admin_router
from .config import ConfigError, Settings
from .downloader import Downloader
from .handlers import bot_commands, router
from .library import Library
from .middlewares import AccessMiddleware
from .runtime import AccessControl, RuntimeSettings
from .sender import TrackSender
from .spotify import SpotifyService
from .storage import Storage

log = logging.getLogger('bot')


def build_dispatcher(
    settings: Settings,
    spotify: SpotifyService | None,
    storage: Storage,
    sender: TrackSender,
    runtime: RuntimeSettings,
) -> Dispatcher:
    access = AccessControl(settings, storage)
    # `storage` в aiogram зарезервирован под FSM, поэтому кэш передаётся как `db`
    dp = Dispatcher(
        settings=settings,
        spotify=spotify,
        db=storage,
        library=Library(spotify, storage),
        sender=sender,
        runtime=runtime,
        access=access,
    )
    middleware = AccessMiddleware(access)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    # Админские команды проверяются раньше обычных, иначе их перехватит поиск по тексту
    dp.include_router(admin_router)
    dp.include_router(router)
    return dp


async def run(settings: Settings) -> None:
    settings.ensure_dirs()
    # Хвосты от прерванных загрузок
    for leftover in settings.download_dir.iterdir():
        if leftover.is_file():
            leftover.unlink()

    storage = Storage(settings.db_path, legacy_quality=settings.audio_quality)
    runtime = RuntimeSettings(settings, storage)
    spotify = None
    if settings.spotify_enabled:
        spotify = SpotifyService(
            settings.spotify_client_id,
            settings.spotify_client_secret,
            settings.spotify_redirect_uri,
            settings.tokens_dir,
        )
    downloader = Downloader(settings.download_dir, settings.search_sources, settings.audio_quality)
    sender = TrackSender(downloader, storage, settings.max_concurrent_downloads, runtime)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, spotify, storage, sender, runtime)

    try:
        commands = bot_commands(settings.spotify_enabled)
        await bot.set_my_commands(commands)
        for admin_id in settings.admin_user_ids:
            # Не получится, если админ ещё ни разу не писал боту — не страшно
            with contextlib.suppress(TelegramAPIError):
                await bot.set_my_commands(commands + ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        log.info(
            'Бот запущен. Библиотека: %s; поиск: %s',
            'Spotify API + /import' if spotify else 'только /import (ключи Spotify не заданы)',
            ', '.join(settings.search_sources),
        )
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        storage.close()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        sys.exit(f'Ошибка конфигурации: {e}')
    if not shutil.which('ffmpeg'):
        sys.exit('Не найден ffmpeg — он нужен yt-dlp для конвертации в mp3. Установите его и добавьте в PATH.')
    if not settings.admin_user_ids:
        log.warning('ADMIN_USER_IDS не задан — настройки и управление доступом из бота (/admin) недоступны')
    if not settings.admin_user_ids and not settings.allowed_user_ids:
        log.warning('Не заданы ни ADMIN_USER_IDS, ни ALLOWED_USER_IDS — ботом сможет пользоваться любой')
    asyncio.run(run(settings))


if __name__ == '__main__':
    main()
