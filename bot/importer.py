"""Импорт библиотеки без Spotify API.

Поддерживаются:
- CSV из Exportify (exportify.app) и похожих сервисов (TuneMyMusic, Soundiiz и т.п.);
- YourLibrary.json из официальной выгрузки данных Spotify, в том числе весь zip-архив выгрузки.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

from .spotify import Track, is_spotify_id

MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024

_SPOTIFY_ID_RE = re.compile(r'(?:spotify:track:|open\.spotify\.com/(?:intl-[\w-]+/)?track/)([A-Za-z0-9]{22})')

# Возможные названия колонок CSV (в нижнем регистре), по убыванию приоритета
_CSV_COLUMNS = {
    'uri': ('track uri', 'spotify uri', 'uri', 'spotify - id', 'spotify id', 'spotify track id', 'track id', 'id'),
    'title': ('track name', 'track title', 'song name', 'name', 'title', 'track', 'song'),
    'artists': ('artist name(s)', 'artist names', 'artist name', 'artists', 'artist'),
    'album': ('album name', 'album title', 'album'),
    'album_artists': ('album artist name(s)', 'album artist names', 'album artist'),
    'release_date': ('album release date', 'release date'),
    'duration': ('track duration (ms)', 'duration (ms)', 'duration_ms', 'duration'),
    'cover': ('album image url', 'image url', 'cover url', 'artwork url'),
    'track_number': ('track number',),
    'disc_number': ('disc number',),
    'added_at': ('added at', 'date added', 'added_at', 'added'),
}


class ImportFormatError(Exception):
    """Файл не похож на выгрузку библиотеки."""


@dataclass(frozen=True)
class ImportResult:
    # Новые первыми, если в файле есть даты добавления; иначе — в порядке файла
    tracks: list[Track]
    sorted_by_date: bool


@dataclass(frozen=True)
class _Row:
    track: Track
    added_at: datetime | None


def make_track_id(uri: str, artists: tuple[str, ...], title: str, album: str) -> str:
    uri = (uri or '').strip()
    match = _SPOTIFY_ID_RE.search(uri)
    if match:
        return match.group(1)
    if is_spotify_id(uri):
        return uri
    # Нет ссылки на Spotify — id из метаданных. «_» не встречается в id Spotify,
    # поэтому такие id не спутать с настоящими.
    key = '|'.join((', '.join(artists), title, album)).casefold()
    return '_' + hashlib.sha1(key.encode()).hexdigest()[:21]


def _split_artists(value) -> tuple[str, ...]:
    if isinstance(value, list):
        names = [v.get('name') if isinstance(v, dict) else v for v in value]
    else:
        names = re.split(r'\s*,\s*', str(value or ''))
    return tuple(str(n).strip() for n in names if n and str(n).strip())


def _int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _duration_ms(value, in_ms: bool) -> int:
    text = str(value or '').strip()
    if not text:
        return 0
    if ':' in text:
        parts = [_int(p) for p in text.split(':')]
        if any(p is None for p in parts):
            return 0
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds * 1000
    number = _int(text) or 0
    # Колонка без единиц: короткие значения — секунды
    return number if in_ms or number >= 10_000 else number * 1000


def _parse_date(value) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _make_row(
    *, uri='', title='', artists=(), album='', album_artists=(), duration_ms=0, release_date=None,
    cover_url=None, track_number=None, disc_number=None, added_at=None,
) -> _Row | None:
    title = str(title or '').strip()
    if not title or not artists:
        return None
    album = str(album or '').strip()
    track = Track(
        id=make_track_id(uri, artists, title, album),
        title=title,
        artists=artists,
        album=album,
        album_artists=album_artists,
        duration_ms=duration_ms,
        track_number=track_number,
        disc_number=disc_number,
        release_date=(str(release_date).strip() or None) if release_date else None,
        cover_url=cover_url or None,
        thumb_url=None,
    )
    return _Row(track, _parse_date(added_at))


def _decode(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1')


def _parse_csv(data: bytes) -> list[_Row]:
    try:
        return _read_csv(_decode(data))
    except csv.Error as e:
        raise ImportFormatError('Файл не похож на CSV-таблицу.') from e


def _read_csv(text: str) -> list[_Row]:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = {(name or '').strip().lower(): name for name in reader.fieldnames or []}

    columns = {}
    for key, aliases in _CSV_COLUMNS.items():
        columns[key] = next((headers[a] for a in aliases if a in headers), None)
    if not columns['title'] or not columns['artists']:
        raise ImportFormatError(
            'В CSV не нашёл колонок с названием трека и исполнителем. '
            'Подойдёт выгрузка «Liked Songs» из Exportify.'
        )
    duration_in_ms = 'ms' in (columns['duration'] or '').lower()

    def get(row: dict, key: str):
        column = columns[key]
        return row.get(column) if column else None

    rows = []
    for row in reader:
        parsed = _make_row(
            uri=get(row, 'uri') or '',
            title=get(row, 'title'),
            artists=_split_artists(get(row, 'artists')),
            album=get(row, 'album'),
            album_artists=_split_artists(get(row, 'album_artists')),
            duration_ms=_duration_ms(get(row, 'duration'), duration_in_ms),
            release_date=get(row, 'release_date'),
            cover_url=(get(row, 'cover') or '').strip(),
            track_number=_int(get(row, 'track_number')),
            disc_number=_int(get(row, 'disc_number')),
            added_at=get(row, 'added_at'),
        )
        if parsed:
            rows.append(parsed)
    return rows


def _first(item: dict, *keys):
    for key in keys:
        if item.get(key):
            return item[key]
    return None


def _parse_json(data: bytes) -> list[_Row]:
    try:
        obj = json.loads(_decode(data))
    except json.JSONDecodeError as e:
        raise ImportFormatError(f'Не получилось прочитать JSON: {e}') from e
    items = obj.get('tracks') if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        raise ImportFormatError('В JSON нет списка tracks — нужен файл YourLibrary.json из выгрузки данных Spotify.')

    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = _make_row(
            uri=_first(item, 'uri', 'trackUri', 'track_uri', 'spotify_uri') or '',
            title=_first(item, 'track', 'trackName', 'track_name', 'name', 'title'),
            artists=_split_artists(_first(item, 'artist', 'artistName', 'artist_name', 'artists')),
            album=_first(item, 'album', 'albumName', 'album_name'),
            duration_ms=_int(_first(item, 'duration_ms', 'durationMs')) or 0,
            added_at=_first(item, 'added_at', 'addedAt', 'date_added', 'addedDate'),
        )
        if parsed:
            rows.append(parsed)
    return rows


def _pick_from_zip(data: bytes) -> tuple[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ImportFormatError('Повреждённый zip-архив.') from e
    with archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename.casefold() for info in files]

        def find(predicate):
            return next((info for info, name in zip(files, names) if predicate(name)), None)

        info = (
            find(lambda n: n.endswith('yourlibrary.json'))
            or find(lambda n: n.endswith('.csv'))
            or find(lambda n: n.endswith('.json'))
        )
        if info is None:
            raise ImportFormatError('В архиве нет YourLibrary.json или CSV.')
        if info.file_size > MAX_UNCOMPRESSED_SIZE:
            raise ImportFormatError(f'{info.filename} слишком большой.')
        return info.filename, archive.read(info)


def parse_library_file(filename: str, data: bytes) -> ImportResult:
    name = (filename or '').casefold()
    if name.endswith('.zip') or data[:4] == b'PK\x03\x04':
        filename, data = _pick_from_zip(data)
        name = filename.casefold()

    stripped = data.lstrip(b'\xef\xbb\xbf \t\r\n')
    if name.endswith('.json') or stripped[:1] in (b'{', b'['):
        rows = _parse_json(data)
    else:
        rows = _parse_csv(data)

    sorted_by_date = any(row.added_at for row in rows)
    if sorted_by_date:
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        rows.sort(key=lambda row: row.added_at or oldest, reverse=True)

    tracks, seen = [], set()
    for row in rows:
        if row.track.id not in seen:
            seen.add(row.track.id)
            tracks.append(row.track)
    if not tracks:
        raise ImportFormatError('В файле не нашлось ни одного трека.')
    return ImportResult(tracks=tracks, sorted_by_date=sorted_by_date)
