import sqlite3
from pathlib import Path

import pytest

from bot.config import Settings
from bot.runtime import AccessControl, RuntimeSettings
from bot.storage import Storage


def make_settings(tmp_path: Path, **overrides) -> Settings:
    data = dict(
        bot_token='42:TEST', spotify_client_id=None, spotify_client_secret=None,
        spotify_redirect_uri='http://127.0.0.1:8888/callback',
        allowed_user_ids=frozenset(), admin_user_ids=frozenset(),
        data_dir=tmp_path, last_tracks_default=10, last_tracks_max=50,
        search_sources=('soundcloud',), audio_quality=192, max_concurrent_downloads=1,
    )
    data.update(overrides)
    return Settings(**data)


@pytest.fixture
def db(tmp_path):
    storage = Storage(tmp_path / 'db.sqlite3')
    yield storage
    storage.close()


def test_runtime_quality_default_and_override(tmp_path, db):
    runtime = RuntimeSettings(make_settings(tmp_path, audio_quality=256), db)
    assert runtime.audio_quality == 256
    runtime.set('audio_quality', '320')
    assert runtime.audio_quality == 320
    with pytest.raises(ValueError):
        runtime.set('audio_quality', '999')
    # значение из старой версии, которого больше нет в списке, — берём значение по умолчанию
    db.set_setting('audio_quality', '96')
    assert runtime.audio_quality == 256


def test_access_open_when_nothing_configured(tmp_path, db):
    access = AccessControl(make_settings(tmp_path), db)
    assert access.is_open() and access.is_allowed(12345)


def test_access_rules(tmp_path, db):
    access = AccessControl(make_settings(tmp_path, admin_user_ids=frozenset({1}), allowed_user_ids=frozenset({2})), db)
    assert not access.is_open()
    assert access.is_allowed(1) and access.is_admin(1)
    assert access.is_allowed(2) and not access.is_admin(2)
    assert not access.is_allowed(3)

    assert access.allow(3, 'Ann', added_by=1)
    assert not access.allow(3, 'Ann', added_by=1)
    assert access.is_allowed(3)
    assert [(u.user_id, u.name) for u in access.managed_users()] == [(3, 'Ann')]
    assert access.revoke(3) and not access.revoke(3)
    assert not access.is_allowed(3)


def test_old_file_cache_is_migrated(tmp_path):
    path = tmp_path / 'old.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE files (track_id TEXT PRIMARY KEY, file_id TEXT NOT NULL)')
    conn.execute("INSERT INTO files VALUES ('abc', 'FILE')")
    conn.commit()
    conn.close()

    storage = Storage(path, legacy_quality=192)
    assert storage.get_file_id('abc', 192) == 'FILE'
    assert storage.get_file_id('abc', 320) is None
    storage.close()
    # повторное открытие ничего не ломает
    Storage(path).close()


def test_settings_admin_ids(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '1:abc')
    monkeypatch.setenv('ADMIN_USER_IDS', '883905237')
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    monkeypatch.delenv('SPOTIFY_CLIENT_SECRET', raising=False)
    assert Settings.from_env().admin_user_ids == {883905237}
