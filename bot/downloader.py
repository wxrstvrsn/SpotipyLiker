"""Поиск трека по метаданным Spotify на SoundCloud (через yt-dlp), скачивание в mp3 и тегирование."""

import dataclasses
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests
import yt_dlp
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.mp3 import MP3

from .query import split_artist_title
from .spotify import Track, is_spotify_id

log = logging.getLogger(__name__)

SEARCH_PREFIXES = {'soundcloud': 'scsearch', 'youtube': 'ytsearch'}
SEARCH_RESULTS = 8
MAX_ATTEMPTS_PER_SOURCE = 3
MIN_SCORE = 0.5
# Telegram не даёт превью-картинку больше 200 КБ
MAX_THUMBNAIL_SIZE = 200 * 1024
MAX_THUMBNAIL_SIDE = 320
# Длительность неизвестна (импорт без длительности): длиннее — скорее всего микс или подкаст
MAX_UNKNOWN_DURATION = 20 * 60
OEMBED_URL = 'https://open.spotify.com/oembed'

# Слова, которые означают другую версию трека. Если их нет в названии на Spotify,
# а у кандидата они есть — скорее всего это ремикс/кавер/sped up и т.п.
VERSION_MARKERS = frozenset({
    'remix', 'cover', 'live', 'sped', 'slowed', 'nightcore', 'instrumental', 'karaoke',
    'acoustic', 'reverb', 'bootleg', 'mashup', 'rework', 'flip', 'vip', 'edit', '8d',
    'boosted', 'ремикс', 'кавер', 'минус', 'караоке',
})

_FEAT_RE = re.compile(r'\s*[(\[](?:feat|ft|with)\.?\s[^)\]]*[)\]]', re.IGNORECASE)
_NOISE_SUFFIX_RE = re.compile(
    r'\s+-\s+(?:\d{4}\s+)?(?:remaster(?:ed)?|mono|stereo|original mix|radio edit|single version|album version)\b.*$',
    re.IGNORECASE,
)
_FEAT_WORDS = frozenset({'feat', 'ft', 'featuring'})


class TrackNotFoundError(Exception):
    """Подходящий трек не найден ни в одном источнике."""


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str
    uploader: str
    duration: float | None
    source: str


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    duration: int
    source_url: str
    thumbnail: bytes | None
    # Метаданные, записанные в теги: для запроса без исполнителя — из найденного трека
    track: Track


def clean_title(title: str) -> str:
    """Убирает из названия Spotify «(feat. X)» и хвосты вроде « - Remastered 2011»."""
    title = _FEAT_RE.sub('', title)
    title = _NOISE_SUFFIX_RE.sub('', title)
    return title.strip() or title


def tokens(text: str) -> list[str]:
    text = text.casefold().replace("'", '').replace('’', '')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return [t for t in re.findall(r'\w+', text) if t not in _FEAT_WORDS]


def _coverage(needle: list[str], haystack: set[str]) -> float:
    if not needle:
        return 1.0
    return sum(t in haystack for t in needle) / len(needle)


def build_query(track: Track) -> str:
    title = clean_title(track.title)
    return f'{track.artists[0]} {title}' if track.artists else title


def score_candidate(track: Track, candidate: Candidate) -> float | None:
    """Оценка похожести кандидата на трек Spotify от 0 до 1, None — точно не он."""
    expected = track.duration_ms / 1000
    if candidate.duration and expected:
        diff = abs(candidate.duration - expected)
        tolerance = max(7.0, expected * 0.06)
        if diff > tolerance:
            return None
        duration_score = 1 - diff / tolerance
    elif candidate.duration and candidate.duration > MAX_UNKNOWN_DURATION:
        return None
    else:
        duration_score = 0.5

    cand_tokens = tokens(f'{candidate.uploader} {candidate.title}')
    cand_set = set(cand_tokens)
    title_tokens = tokens(clean_title(track.title))
    title_score = max(
        _coverage(title_tokens, cand_set),
        SequenceMatcher(None, ' '.join(title_tokens), ' '.join(tokens(candidate.title))).ratio(),
    )
    if title_score < 0.5:
        return None
    if track.artists:
        artist_score = max(_coverage(tokens(artist), cand_set) for artist in track.artists)
    else:
        artist_score = 0.5

    spotify_tokens = set(tokens(' '.join((track.title, track.album, *track.artists))))
    extra_markers = (set(tokens(candidate.title)) & VERSION_MARKERS) - spotify_tokens

    return 0.5 * title_score + 0.2 * artist_score + 0.3 * duration_score - 0.4 * len(extra_markers)


def rank_candidates(track: Track, candidates: list[Candidate]) -> list[Candidate]:
    scored = []
    for candidate in candidates:
        score = score_candidate(track, candidate)
        log.debug('%.2f %s', score if score is not None else -1, candidate)
        if score is not None and score >= MIN_SCORE:
            scored.append((score, candidate))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _, candidate in scored]


def tag_mp3(path: Path, track: Track, cover: bytes | None) -> None:
    """Записывает в mp3 метаданные из Spotify, затирая теги источника."""
    tags = ID3()
    tags.add(TIT2(encoding=3, text=track.title))
    tags.add(TPE1(encoding=3, text=track.artist))
    if track.album:
        tags.add(TALB(encoding=3, text=track.album))
    if track.album_artists:
        tags.add(TPE2(encoding=3, text=', '.join(track.album_artists)))
    if track.track_number:
        tags.add(TRCK(encoding=3, text=str(track.track_number)))
    if track.disc_number:
        tags.add(TPOS(encoding=3, text=str(track.disc_number)))
    if track.release_date:
        tags.add(TDRC(encoding=3, text=track.release_date))
    if cover:
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover))
    tags.save(path, v2_version=3)


def _fetch_image(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        log.warning('Не удалось скачать обложку %s: %s', url, e)
        return None
    return response.content


def _oembed_cover(track_id: str) -> tuple[str | None, str | None]:
    """Обложка из публичного oEmbed Spotify (ключ API не нужен): (обложка, превью для Telegram)."""
    try:
        response = requests.get(
            OEMBED_URL, params={'url': f'https://open.spotify.com/track/{track_id}'}, timeout=15
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        log.warning('Не удалось получить обложку %s через oEmbed: %s', track_id, e)
        return None, None
    url = data.get('thumbnail_url') or None
    width = data.get('thumbnail_width') or 0
    return url, url if 0 < width <= MAX_THUMBNAIL_SIDE else None


class _YtDlpLogger:
    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        # Ошибки yt-dlp всё равно пробрасываются исключением, здесь только пишем в лог
        log.warning(msg)


class Downloader:
    """Блокирующий загрузчик, вызывать через asyncio.to_thread."""

    def __init__(self, download_dir: Path, sources: tuple[str, ...], audio_quality: int):
        self._dir = download_dir
        self._sources = sources
        self._quality = audio_quality

    def _base_opts(self) -> dict:
        return {
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'noplaylist': True,
            'socket_timeout': 30,
            'retries': 3,
            'logger': _YtDlpLogger(),
        }

    def search(self, source: str, query: str) -> list[Candidate]:
        opts = {**self._base_opts(), 'extract_flat': 'in_playlist'}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'{SEARCH_PREFIXES[source]}{SEARCH_RESULTS}:{query}', download=False)
        candidates = []
        for entry in (info or {}).get('entries') or []:
            url = entry.get('webpage_url') or entry.get('url')
            if not url or not entry.get('title'):
                continue
            candidates.append(Candidate(
                url=url,
                title=entry['title'],
                uploader=entry.get('uploader') or entry.get('channel') or '',
                duration=entry.get('duration'),
                source=source,
            ))
        return candidates

    def _download_candidate(self, track: Track, candidate: Candidate) -> Path:
        opts = {
            **self._base_opts(),
            # SoundCloud отдаёт для Go+ треков только 30-секундное превью — такие форматы пропускаем
            'format': 'bestaudio[format_id!*=preview]/best[format_id!*=preview]',
            'outtmpl': str(self._dir / f'{track.id}.%(ext)s'),
            'overwrites': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': str(self._quality),
            }],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([candidate.url])
        path = self._dir / f'{track.id}.mp3'
        if not path.exists():
            raise yt_dlp.utils.DownloadError(f'yt-dlp не создал файл {path.name}')
        return path

    def _cleanup(self, track: Track) -> None:
        for leftover in self._dir.glob(f'{track.id}.*'):
            leftover.unlink(missing_ok=True)

    def download(self, track: Track) -> DownloadResult:
        query = build_query(track)
        search_errors = []
        for source in self._sources:
            try:
                candidates = rank_candidates(track, self.search(source, query))
            except yt_dlp.utils.DownloadError as e:
                log.warning('Поиск %r в %s не удался: %s', query, source, e)
                search_errors.append(e)
                continue
            if not candidates:
                log.info('%s: ничего подходящего для %r', source, query)
            for candidate in candidates[:MAX_ATTEMPTS_PER_SOURCE]:
                log.info('Скачиваю %s -> %s', track.display_name, candidate.url)
                try:
                    path = self._download_candidate(track, candidate)
                except yt_dlp.utils.DownloadError as e:
                    log.warning('Не удалось скачать %s: %s', candidate.url, e)
                    self._cleanup(track)
                    continue
                try:
                    return self._finalize(track, candidate, path)
                except Exception:
                    self._cleanup(track)
                    raise
        if len(search_errors) == len(self._sources):
            # Все источники недоступны — это не «трек не найден», а сетевая ошибка
            raise search_errors[-1]
        raise TrackNotFoundError(track.display_name)

    @staticmethod
    def _resolve_metadata(track: Track, candidate: Candidate) -> Track:
        if track.artists:
            return track
        # Текстовый запрос без исполнителя: берём «Исполнитель - Название» из найденного трека
        split = split_artist_title(candidate.title)
        artist, title = split if split else (candidate.uploader or 'Unknown', candidate.title)
        return dataclasses.replace(track, title=title, artists=(artist,))

    def _finalize(self, track: Track, candidate: Candidate, path: Path) -> DownloadResult:
        track = self._resolve_metadata(track, candidate)
        cover_url, thumb_url = track.cover_url, track.thumb_url
        if not cover_url and is_spotify_id(track.id):
            # В выгрузках (/import) обложек обычно нет
            cover_url, thumb_url = _oembed_cover(track.id)
        cover = _fetch_image(cover_url)
        tag_mp3(path, track, cover)
        thumbnail = cover if thumb_url == cover_url else _fetch_image(thumb_url)
        if thumbnail and len(thumbnail) > MAX_THUMBNAIL_SIZE:
            thumbnail = None
        return DownloadResult(
            path=path,
            duration=round(MP3(path).info.length),
            source_url=candidate.url,
            thumbnail=thumbnail,
            track=track,
        )
