import pytest

from bot.config import ConfigError, Settings
from bot.keyboards import PAGE_SIZE, PageCallback, TrackCallback, total_pages, tracks_keyboard
from bot.storage import Storage

from .conftest import make_track


def test_storage_tracks_and_file_ids(tmp_path, track):
    storage = Storage(tmp_path / 'db.sqlite3')
    storage.save_tracks([track])
    assert storage.get_track(track.id) == track
    assert storage.get_track('missing') is None

    assert storage.get_file_id(track.id, 192) is None
    storage.set_file_id(track.id, 192, 'FILE')
    assert storage.get_file_id(track.id, 192) == 'FILE'
    assert storage.get_file_id(track.id, 320) is None  # кэш свой для каждого битрейта
    storage.delete_file_id(track.id, 192)
    assert storage.get_file_id(track.id, 192) is None
    storage.close()

    # данные переживают перезапуск
    reopened = Storage(tmp_path / 'db.sqlite3')
    assert reopened.get_track(track.id) == track
    reopened.close()


def test_imported_library(tmp_path):
    storage = Storage(tmp_path / 'db.sqlite3')
    tracks = [make_track(id=f'id{i:020d}', title=f'Song {i}') for i in range(25)]
    storage.replace_library(1, tracks)
    storage.replace_library(2, tracks[:3])

    assert storage.library_size(1) == 25
    assert storage.library_page(1, 10, 20) == tracks[20:]
    assert storage.get_track(tracks[5].id) == tracks[5]

    storage.replace_library(1, tracks[:2])  # новая выгрузка заменяет старую
    assert storage.library_page(1, 10, 0) == tracks[:2]

    assert storage.library_tracks_at(2, [2, 0, 99]) == [tracks[2], tracks[0]]
    assert storage.library_tracks_at(2, []) == []

    storage.clear_library(1)
    assert storage.library_size(1) == 0
    assert storage.library_size(2) == 3
    storage.close()


@pytest.mark.parametrize('total, pages', [(0, 1), (1, 1), (10, 1), (11, 2), (95, 10)])
def test_total_pages(total, pages):
    assert total_pages(total) == pages


def _tracks(n):
    return [make_track(id=f'{i:022d}', title=f'Song {i}' + ' very long' * 10) for i in range(n)]


def test_keyboard_first_page():
    markup = tracks_keyboard(_tracks(PAGE_SIZE), page=0, pages=3)
    rows = markup.inline_keyboard
    assert len(rows) == PAGE_SIZE + 1
    for row in rows[:-1]:
        (button,) = row
        assert len(button.text) <= 60
        assert TrackCallback.unpack(button.callback_data).track_id.startswith('0')
        assert len(button.callback_data.encode()) <= 64
    nav = rows[-1]
    assert [b.text for b in nav] == ['🔄 1/3', '▶️']
    assert PageCallback.unpack(nav[1].callback_data).page == 1


def test_keyboard_last_page():
    nav = tracks_keyboard(_tracks(3), page=2, pages=3).inline_keyboard[-1]
    assert [b.text for b in nav] == ['◀️', '🔄 3/3']
    assert PageCallback.unpack(nav[0].callback_data).page == 1


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '1:abc')
    monkeypatch.setenv('SPOTIFY_CLIENT_ID', 'id')
    monkeypatch.setenv('SPOTIFY_CLIENT_SECRET', 'secret')
    monkeypatch.setenv('ALLOWED_USER_IDS', '1, 2;3')
    monkeypatch.setenv('SEARCH_SOURCES', 'soundcloud, youtube')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('LAST_TRACKS_DEFAULT', '100')
    monkeypatch.delenv('SPOTIFY_REDIRECT_URI', raising=False)
    monkeypatch.delenv('LAST_TRACKS_MAX', raising=False)

    settings = Settings.from_env()

    assert settings.allowed_user_ids == {1, 2, 3}
    assert settings.search_sources == ('soundcloud', 'youtube')
    assert settings.spotify_redirect_uri == 'http://127.0.0.1:8888/callback'
    assert settings.last_tracks_default == 50  # не больше LAST_TRACKS_MAX
    assert settings.tokens_dir == tmp_path / 'tokens'


def test_settings_without_spotify_keys(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '1:abc')
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    monkeypatch.delenv('SPOTIFY_CLIENT_SECRET', raising=False)
    settings = Settings.from_env()
    assert not settings.spotify_enabled
    assert settings.spotify_client_id is None


@pytest.mark.parametrize('name, value', [
    ('TELEGRAM_BOT_TOKEN', ''),
    ('SPOTIFY_CLIENT_SECRET', ''),
    ('SEARCH_SOURCES', 'spotify'),
    ('ALLOWED_USER_IDS', 'me'),
    ('AUDIO_QUALITY', 'high'),
])
def test_settings_errors(monkeypatch, name, value):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '1:abc')
    monkeypatch.setenv('SPOTIFY_CLIENT_ID', 'id')
    monkeypatch.setenv('SPOTIFY_CLIENT_SECRET', 'secret')
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Settings.from_env()
