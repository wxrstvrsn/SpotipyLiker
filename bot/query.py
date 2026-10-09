"""Трек из текстового сообщения: «Исполнитель - Название»."""

import re

from .importer import make_track_id
from .spotify import Track

# « - », « — », « – » с пробелами или длинное тире без пробелов. Дефис без пробелов
# не разделитель, чтобы не резать имена вроде «Jay-Z».
_SEPARATOR_RE = re.compile(r'\s+[-–—]+\s+|\s*[–—]\s*')


def split_artist_title(text: str) -> tuple[str, str] | None:
    parts = _SEPARATOR_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return artist, title
    return None


def track_from_query(text: str) -> Track | None:
    """Строка «Исполнитель - Название» превращается в трек, как строка CSV без длительности.

    Без разделителя вся строка считается названием, а исполнителя downloader возьмёт
    из найденного трека.
    """
    text = ' '.join(text.split())
    if not text:
        return None
    split = split_artist_title(text)
    if split:
        artists = tuple(a.strip() for a in split[0].split(',') if a.strip())
        title = split[1]
    else:
        artists, title = (), text
    return Track(
        id=make_track_id('', artists, title, ''),
        title=title,
        artists=artists,
        album='',
        album_artists=(),
        duration_ms=0,
    )
