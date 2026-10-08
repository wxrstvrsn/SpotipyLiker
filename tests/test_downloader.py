from pathlib import Path
from unittest.mock import patch

import pytest
import yt_dlp
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from bot import downloader as dl
from bot.downloader import Candidate, Downloader, TrackNotFoundError, build_query, clean_title, rank_candidates

from .conftest import make_track, requires_ffmpeg, write_mp3


@pytest.mark.parametrize('title, expected', [
    ('Get Lucky (feat. Pharrell Williams)', 'Get Lucky'),
    ('Song [ft. Someone]', 'Song'),
    ('Yesterday - Remastered 2009', 'Yesterday'),
    ('Paint It Black - 2002 Remaster', 'Paint It Black'),
    ('Strobe - Radio Edit', 'Strobe'),
    ('Levels - Skrillex Remix', 'Levels - Skrillex Remix'),
    ('Кукла колдуна', 'Кукла колдуна'),
])
def test_clean_title(title, expected):
    assert clean_title(title) == expected


def test_build_query_uses_main_artist_and_clean_title(track):
    assert build_query(track) == 'Daft Punk Get Lucky'


def cand(title, uploader='', duration=369.0, url=None):
    return Candidate(url=url or f'https://soundcloud.com/x/{title}', title=title, uploader=uploader,
                     duration=duration, source='soundcloud')


def test_rank_prefers_original_over_remix_and_rejects_wrong_duration(track):
    original = cand('Daft Punk - Get Lucky (feat. Pharrell Williams)', 'Daft Punk', 367.0)
    remix = cand('Get Lucky (Remix)', 'Daft Punk', 369.0)
    radio_edit = cand('Daft Punk - Get Lucky', 'daftpunk', 248.0)  # радио-версия короче
    preview = cand('Daft Punk - Get Lucky', 'Daft Punk', 30.0)
    other = cand('Instant Crush', 'Daft Punk', 369.0)

    ranked = rank_candidates(track, [remix, radio_edit, preview, other, original])

    assert ranked[0] == original
    assert radio_edit not in ranked
    assert preview not in ranked
    assert other not in ranked


def test_remix_allowed_when_spotify_track_is_remix():
    track = make_track(title='Levels - Skrillex Remix', artists=('Avicii', 'Skrillex'), duration_ms=290_000)
    ranked = rank_candidates(track, [cand('Avicii - Levels (Skrillex Remix)', 'Skrillex', 291.0)])
    assert len(ranked) == 1


def test_cyrillic_and_unknown_duration():
    track = make_track(title='Кукла колдуна', artists=('Король и Шут',), album='Акустический альбом',
                       duration_ms=205_000)
    good = cand('Король и Шут - Кукла Колдуна', 'kish_fan', None)
    cover = cand('Кукла колдуна (кавер)', 'someone', 205.0)
    assert rank_candidates(track, [cover, good]) == [good]


@requires_ffmpeg
def test_tag_mp3_overwrites_source_tags(tmp_path, track):
    path = write_mp3(tmp_path / 'a.mp3')
    dl.tag_mp3(path, track, b'\xff\xd8\xff fake jpeg')

    tags = ID3(path)
    assert tags.version[:2] == (2, 3)
    assert str(tags['TIT2']) == track.title
    assert str(tags['TPE1']) == 'Daft Punk, Pharrell Williams, Nile Rodgers'
    assert str(tags['TALB']) == 'Random Access Memories'
    assert str(tags['TPE2']) == 'Daft Punk'
    assert str(tags['TRCK']) == '8'
    assert str(tags['TDRC']).startswith('2013')
    assert tags.getall('APIC')[0].data == b'\xff\xd8\xff fake jpeg'
    assert 'SoundCloud title' not in str(tags.get('TIT2'))


class FakeYoutubeDL:
    """Подмена yt_dlp.YoutubeDL: поиск отдаёт entries, скачивание создаёт mp3 или падает."""

    entries: dict[str, list[dict]] = {}
    broken_urls: set[str] = set()
    downloaded: list[str] = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        assert self.opts.get('extract_flat')
        prefix = url.split(':', 1)[0]
        return {'entries': self.entries.get(prefix, [])}

    def download(self, urls):
        (url,) = urls
        self.downloaded.append(url)
        assert 'preview' in self.opts['format']
        if url in self.broken_urls:
            raise yt_dlp.utils.DownloadError('Requested format is not available')
        write_mp3(Path(self.opts['outtmpl'].replace('%(ext)s', 'mp3')))


@pytest.fixture
def fake_ytdl(monkeypatch):
    FakeYoutubeDL.entries = {}
    FakeYoutubeDL.broken_urls = set()
    FakeYoutubeDL.downloaded = []
    monkeypatch.setattr(dl.yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr(dl, '_fetch_image', lambda url: b'jpeg:' + url.encode() if url else None)
    return FakeYoutubeDL


def entry(title, uploader, duration, url):
    return {'title': title, 'uploader': uploader, 'duration': duration, 'url': url}


@requires_ffmpeg
def test_download_skips_broken_candidate_and_tags_file(tmp_path, track, fake_ytdl):
    fake_ytdl.entries['scsearch8'] = [
        entry('Daft Punk - Get Lucky', 'Daft Punk', 369.0, 'https://sc/best'),
        entry('Get Lucky', 'daft punk fan', 368.0, 'https://sc/second'),
    ]
    fake_ytdl.broken_urls = {'https://sc/best'}

    result = Downloader(tmp_path, ('soundcloud',), 192).download(track)

    assert fake_ytdl.downloaded == ['https://sc/best', 'https://sc/second']
    assert result.source_url == 'https://sc/second'
    assert result.path == tmp_path / f'{track.id}.mp3'
    assert result.duration == round(MP3(result.path).info.length)
    assert result.thumbnail == b'jpeg:' + track.thumb_url.encode()
    assert str(ID3(result.path)['TIT2']) == track.title


@requires_ffmpeg
def test_download_falls_back_to_next_source(tmp_path, track, fake_ytdl):
    fake_ytdl.entries['scsearch8'] = [entry('Totally different song', 'x', 369.0, 'https://sc/1')]
    fake_ytdl.entries['ytsearch8'] = [entry('Daft Punk - Get Lucky (Official Audio)', 'Daft Punk', 369.0, 'https://yt/1')]

    result = Downloader(tmp_path, ('soundcloud', 'youtube'), 192).download(track)

    assert result.source_url == 'https://yt/1'
    assert fake_ytdl.downloaded == ['https://yt/1']


def test_download_raises_when_nothing_found(tmp_path, track, fake_ytdl):
    fake_ytdl.entries['scsearch8'] = [entry('Get Lucky (Nightcore)', 'x', 369.0, 'https://sc/1')]
    with pytest.raises(TrackNotFoundError):
        Downloader(tmp_path, ('soundcloud',), 192).download(track)
    assert fake_ytdl.downloaded == []
    assert list(tmp_path.iterdir()) == []


@requires_ffmpeg
def test_search_error_in_one_source_is_not_fatal(tmp_path, track, fake_ytdl):
    fake_ytdl.entries['ytsearch8'] = [entry('Daft Punk - Get Lucky', 'Daft Punk', 369.0, 'https://yt/1')]
    original = FakeYoutubeDL.extract_info

    def flaky(self, url, download=False):
        if url.startswith('scsearch'):
            raise yt_dlp.utils.DownloadError('SoundCloud: HTTP 503')
        return original(self, url, download)

    with patch.object(FakeYoutubeDL, 'extract_info', flaky):
        result = Downloader(tmp_path, ('soundcloud', 'youtube'), 192).download(track)
    assert result.source_url == 'https://yt/1'


def test_all_sources_unavailable_is_an_error_not_not_found(tmp_path, track, fake_ytdl):
    with patch.object(FakeYoutubeDL, 'extract_info', side_effect=yt_dlp.utils.DownloadError('network')):
        with pytest.raises(yt_dlp.utils.DownloadError):
            Downloader(tmp_path, ('soundcloud',), 192).download(track)
