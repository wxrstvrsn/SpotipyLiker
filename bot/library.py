import asyncio
import random

from .spotify import NotAuthorizedError, SpotifyService, Track
from .storage import Storage


class NoLibraryError(Exception):
    """У пользователя нет ни импортированной библиотеки, ни подключённого Spotify."""


class Library:
    """Источник любимых треков пользователя.

    Если пользователь загрузил выгрузку (/import), треки берутся из неё — так бот работает
    без ключей Spotify API. Иначе — из Spotify API, если ключи заданы и пользователь
    выполнил /login. Списки всегда упорядочены от новых к старым.
    """

    def __init__(self, spotify: SpotifyService | None, db: Storage):
        self._spotify = spotify
        self._db = db

    @property
    def spotify_enabled(self) -> bool:
        return self._spotify is not None

    def imported_count(self, user_id: int) -> int:
        return self._db.library_size(user_id)

    def _require_spotify(self) -> SpotifyService:
        if self._spotify is None:
            raise NoLibraryError
        return self._spotify

    async def page(self, user_id: int, limit: int, offset: int) -> tuple[list[Track], int]:
        total = self._db.library_size(user_id)
        if total:
            return self._db.library_page(user_id, limit, offset), total
        spotify = self._require_spotify()
        try:
            tracks, total = await asyncio.to_thread(spotify.saved_tracks, user_id, limit, offset)
        except NotAuthorizedError:
            raise NoLibraryError from None
        self._db.save_tracks(tracks)
        return tracks, total

    async def random_tracks(self, user_id: int, count: int) -> list[Track]:
        """count случайных треков без повторов."""
        total = self._db.library_size(user_id)
        if total:
            return self._db.library_tracks_at(user_id, random.sample(range(total), min(count, total)))
        spotify = self._require_spotify()
        try:
            tracks = await asyncio.to_thread(spotify.random_tracks, user_id, count)
        except NotAuthorizedError:
            raise NoLibraryError from None
        self._db.save_tracks(tracks)
        return tracks

    async def latest(self, user_id: int, count: int) -> list[Track]:
        if self._db.library_size(user_id):
            return self._db.library_page(user_id, count, 0)
        spotify = self._require_spotify()
        try:
            tracks = await asyncio.to_thread(spotify.latest_tracks, user_id, count)
        except NotAuthorizedError:
            raise NoLibraryError from None
        self._db.save_tracks(tracks)
        return tracks
