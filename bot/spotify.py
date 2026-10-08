import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

log = logging.getLogger(__name__)

SCOPE = 'user-library-read'
# Максимальный limit для GET /me/tracks
API_PAGE_LIMIT = 50


class NotAuthorizedError(Exception):
    """У пользователя нет (валидного) токена Spotify."""


class AuthorizationError(Exception):
    """Не удалось завершить OAuth-авторизацию по присланной ссылке."""


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: tuple[str, ...]
    album: str
    album_artists: tuple[str, ...]
    duration_ms: int
    track_number: int | None = None
    disc_number: int | None = None
    release_date: str | None = None
    cover_url: str | None = None
    thumb_url: str | None = None

    @property
    def artist(self) -> str:
        return ', '.join(self.artists)

    @property
    def display_name(self) -> str:
        return f'{self.artist} — {self.title}'

    @classmethod
    def from_api(cls, data: dict) -> 'Track':
        album = data.get('album') or {}
        # Spotify отдаёт обложки от большей к меньшей: 640, 300, 64
        images = sorted(
            (img for img in album.get('images') or [] if img.get('url')),
            key=lambda img: img.get('width') or 0,
            reverse=True,
        )
        thumb = next((img for img in images if (img.get('width') or 0) <= 320), None)
        return cls(
            id=data['id'],
            title=data.get('name') or 'Unknown',
            artists=tuple(a['name'] for a in data.get('artists') or [] if a.get('name')) or ('Unknown',),
            album=album.get('name') or '',
            album_artists=tuple(a['name'] for a in album.get('artists') or [] if a.get('name')),
            duration_ms=int(data.get('duration_ms') or 0),
            track_number=data.get('track_number'),
            disc_number=data.get('disc_number'),
            release_date=album.get('release_date'),
            cover_url=images[0]['url'] if images else None,
            thumb_url=thumb['url'] if thumb else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Track':
        data = dict(data)
        data['artists'] = tuple(data.get('artists') or ())
        data['album_artists'] = tuple(data.get('album_artists') or ())
        return cls(**data)


def _parse_saved_items(items: list[dict]) -> list[Track]:
    tracks = []
    for item in items:
        track = (item or {}).get('track')
        # Локальные файлы и недоступные треки приходят без id
        if not track or not track.get('id') or track.get('is_local'):
            continue
        tracks.append(Track.from_api(track))
    return tracks


class SpotifyService:
    """Доступ к Spotify Web API от имени конкретного пользователя Telegram.

    Токены каждого пользователя хранятся в отдельном файле tokens/<telegram_id>.json.
    Все методы блокирующие — вызывать через asyncio.to_thread.
    """

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, tokens_dir: Path):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._tokens_dir = tokens_dir

    def _token_path(self, user_id: int) -> Path:
        return self._tokens_dir / f'{user_id}.json'

    def _oauth(self, user_id: int, state: str | None = None) -> SpotifyOAuth:
        return SpotifyOAuth(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            scope=SCOPE,
            state=state,
            cache_handler=CacheFileHandler(cache_path=str(self._token_path(user_id))),
            open_browser=False,
            requests_timeout=15,
        )

    def authorize_url(self, user_id: int, state: str) -> str:
        return self._oauth(user_id, state).get_authorize_url()

    def complete_authorization(self, user_id: int, response_url: str, expected_state: str) -> None:
        try:
            state, code = SpotifyOAuth.parse_auth_response_url(response_url.strip())
        except SpotifyOauthError as e:
            # Например, error=access_denied, если пользователь нажал «Отмена»
            raise AuthorizationError(f'Spotify вернул ошибку: {e.error}') from e
        if not code:
            raise AuthorizationError('В ссылке нет параметра code')
        if state != expected_state:
            raise AuthorizationError('Ссылка не от последнего запроса /login — запросите новую')
        try:
            self._oauth(user_id).get_access_token(code, as_dict=False, check_cache=False)
        except SpotifyOauthError as e:
            raise AuthorizationError(e.error_description or str(e)) from e

    def logout(self, user_id: int) -> None:
        self._token_path(user_id).unlink(missing_ok=True)

    def is_authorized(self, user_id: int) -> bool:
        try:
            self._client(user_id)
        except NotAuthorizedError:
            return False
        return True

    def _client(self, user_id: int) -> spotipy.Spotify:
        # Клиент создаётся с готовым access token, а не с auth_manager: иначе при
        # отсутствии токена spotipy попытается спросить ссылку через input() и
        # заблокирует бота.
        oauth = self._oauth(user_id)
        try:
            token_info = oauth.validate_token(oauth.cache_handler.get_cached_token())
        except SpotifyOauthError as e:
            log.warning('Не удалось обновить токен пользователя %s: %s', user_id, e)
            token_info = None
        if not token_info:
            raise NotAuthorizedError
        return spotipy.Spotify(auth=token_info['access_token'], requests_timeout=15)

    def saved_tracks(self, user_id: int, limit: int, offset: int) -> tuple[list[Track], int]:
        """Страница любимых треков (новые первыми) и общее их количество."""
        page = self._client(user_id).current_user_saved_tracks(limit=limit, offset=offset)
        return _parse_saved_items(page.get('items') or []), int(page.get('total') or 0)

    def latest_tracks(self, user_id: int, count: int) -> list[Track]:
        """count последних добавленных в «Любимые» треков (новые первыми)."""
        client = self._client(user_id)
        tracks: list[Track] = []
        offset = 0
        while len(tracks) < count:
            page = client.current_user_saved_tracks(limit=API_PAGE_LIMIT, offset=offset)
            items = page.get('items') or []
            tracks.extend(_parse_saved_items(items))
            offset += len(items)
            if not items or not page.get('next'):
                break
        return tracks[:count]
