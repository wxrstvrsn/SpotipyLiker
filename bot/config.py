import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REDIRECT_URI = 'http://127.0.0.1:8888/callback'
KNOWN_SOURCES = ('soundcloud', 'youtube')


class ConfigError(Exception):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise ConfigError(f'Не задана переменная окружения {name} (см. .env.example)')
    return value


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f'{name} должно быть целым числом, получено: {raw!r}') from None
    if value < minimum:
        raise ConfigError(f'{name} должно быть >= {minimum}, получено: {value}')
    return value


def _user_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError(f'ALLOWED_USER_IDS: некорректный id {part!r}') from None
    return frozenset(ids)


def _sources(raw: str) -> tuple[str, ...]:
    sources = tuple(s.strip().lower() for s in raw.split(',') if s.strip())
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown or not sources:
        raise ConfigError(
            f'SEARCH_SOURCES: неизвестные источники {unknown}, доступны: {", ".join(KNOWN_SOURCES)}'
        )
    return sources


@dataclass(frozen=True)
class Settings:
    bot_token: str
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    allowed_user_ids: frozenset[int]
    data_dir: Path
    last_tracks_default: int
    last_tracks_max: int
    search_sources: tuple[str, ...]
    audio_quality: int
    max_concurrent_downloads: int

    @property
    def tokens_dir(self) -> Path:
        return self.data_dir / 'tokens'

    @property
    def download_dir(self) -> Path:
        return self.data_dir / 'downloads'

    @property
    def db_path(self) -> Path:
        return self.data_dir / 'bot.sqlite3'

    @classmethod
    def from_env(cls) -> 'Settings':
        last_max = _int('LAST_TRACKS_MAX', 50)
        return cls(
            bot_token=_require('TELEGRAM_BOT_TOKEN'),
            spotify_client_id=_require('SPOTIFY_CLIENT_ID'),
            spotify_client_secret=_require('SPOTIFY_CLIENT_SECRET'),
            spotify_redirect_uri=os.getenv('SPOTIFY_REDIRECT_URI', '').strip() or DEFAULT_REDIRECT_URI,
            allowed_user_ids=_user_ids(os.getenv('ALLOWED_USER_IDS', '')),
            data_dir=Path(os.getenv('DATA_DIR', '').strip() or 'data').resolve(),
            last_tracks_default=min(_int('LAST_TRACKS_DEFAULT', 10), last_max),
            last_tracks_max=last_max,
            search_sources=_sources(os.getenv('SEARCH_SOURCES', '') or 'soundcloud'),
            audio_quality=_int('AUDIO_QUALITY', 192, minimum=32),
            max_concurrent_downloads=_int('MAX_CONCURRENT_DOWNLOADS', 2),
        )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.tokens_dir, self.download_dir):
            path.mkdir(parents=True, exist_ok=True)
