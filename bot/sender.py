import asyncio
import logging
import re
import weakref

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, FSInputFile, Message

from .downloader import Downloader
from .runtime import RuntimeSettings
from .spotify import Track
from .storage import Storage

log = logging.getLogger(__name__)

# Лимит Bot API на загрузку файлов
TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024
UPLOAD_TIMEOUT = 300
SEND_ATTEMPTS = 3


class TrackTooLargeError(Exception):
    pass


def audio_filename(track: Track) -> str:
    name = track.display_name.replace('—', '-')
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()[:120]
    return f'{name or track.id}.mp3'


class TrackSender:
    """Доставляет трек в чат: по сохранённому file_id или скачав и загрузив mp3."""

    def __init__(
        self, downloader: Downloader, storage: Storage, max_concurrent_downloads: int, runtime: RuntimeSettings
    ):
        self._downloader = downloader
        self._storage = storage
        self._runtime = runtime
        self._semaphore = asyncio.Semaphore(max_concurrent_downloads)
        # Один трек не скачивается параллельно: второй запрос дождётся первого и возьмёт file_id
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def is_cached(self, track_id: str) -> bool:
        return self._storage.get_file_id(track_id, self._runtime.audio_quality) is not None

    def _lock(self, track_id: str) -> asyncio.Lock:
        lock = self._locks.get(track_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[track_id] = lock
        return lock

    async def send(self, bot: Bot, chat_id: int, track: Track) -> Message:
        lock = self._lock(track.id)
        async with lock:
            # Кэш file_id отдельный для каждого битрейта: после смены качества в /admin
            # трек перекачается в новом качестве
            quality = self._runtime.audio_quality
            file_id = self._storage.get_file_id(track.id, quality)
            if file_id:
                try:
                    return await self._send_audio(bot, chat_id, audio=file_id)
                except TelegramBadRequest as e:
                    log.warning('file_id для %s не подошёл (%s), скачиваю заново', track.id, e)
                    self._storage.delete_file_id(track.id, quality)

            async with self._semaphore:
                result = await asyncio.to_thread(self._downloader.download, track, quality)
            try:
                if result.path.stat().st_size > TELEGRAM_UPLOAD_LIMIT:
                    raise TrackTooLargeError(track.display_name)
                message = await self._send_audio(
                    bot,
                    chat_id,
                    audio=FSInputFile(result.path, filename=audio_filename(result.track)),
                    title=result.track.title,
                    performer=result.track.artist,
                    duration=result.duration,
                    thumbnail=BufferedInputFile(result.thumbnail, 'cover.jpg') if result.thumbnail else None,
                    request_timeout=UPLOAD_TIMEOUT,
                )
            finally:
                result.path.unlink(missing_ok=True)

            media = message.audio or message.document
            if media:
                self._storage.set_file_id(track.id, quality, media.file_id)
            return message

    @staticmethod
    async def _send_audio(bot: Bot, chat_id: int, **kwargs) -> Message:
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                return await bot.send_audio(chat_id, **kwargs)
            except TelegramRetryAfter as e:
                if attempt == SEND_ATTEMPTS:
                    raise
                log.info('Flood control, жду %s с', e.retry_after)
                await asyncio.sleep(e.retry_after)
        raise AssertionError('unreachable')
