import asyncio
import logging
import os
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from .config import ConfigError, Settings
from .downloader import Downloader
from .handlers import COMMANDS, router
from .middlewares import AccessMiddleware
from .sender import TrackSender
from .spotify import SpotifyService
from .storage import Storage

log = logging.getLogger('bot')


def build_dispatcher(settings: Settings, spotify: SpotifyService, storage: Storage, sender: TrackSender) -> Dispatcher:
    # `storage` в aiogram зарезервирован под FSM, поэтому кэш передаётся как `db`
    dp = Dispatcher(settings=settings, spotify=spotify, db=storage, sender=sender)
    access = AccessMiddleware(settings.allowed_user_ids)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(router)
    return dp


async def run(settings: Settings) -> None:
    settings.ensure_dirs()
    # Хвосты от прерванных загрузок
    for leftover in settings.download_dir.iterdir():
        if leftover.is_file():
            leftover.unlink()

    storage = Storage(settings.db_path)
    spotify = SpotifyService(
        settings.spotify_client_id,
        settings.spotify_client_secret,
        settings.spotify_redirect_uri,
        settings.tokens_dir,
    )
    downloader = Downloader(settings.download_dir, settings.search_sources, settings.audio_quality)
    sender = TrackSender(downloader, storage, settings.max_concurrent_downloads)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, spotify, storage, sender)

    try:
        await bot.set_my_commands(COMMANDS)
        log.info('Бот запущен, источники: %s', ', '.join(settings.search_sources))
        await dp.start_polling(bot)
    finally:
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
    if not settings.allowed_user_ids:
        log.warning('ALLOWED_USER_IDS не задан — ботом сможет пользоваться любой пользователь Telegram')
    asyncio.run(run(settings))


if __name__ == '__main__':
    main()
