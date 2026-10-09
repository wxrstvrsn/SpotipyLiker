import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .spotify import Track


@dataclass(frozen=True)
class AllowedUser:
    user_id: int
    name: str | None


class Storage:
    """SQLite: метаданные треков (для кнопок), file_id уже отправленных mp3,
    импортированные библиотеки (режим без Spotify API), настройки и пользователи,
    которым админ открыл доступ.

    file_id привязан к боту, а не к чату, поэтому трек, однажды загруженный в Telegram,
    повторно отправляется мгновенно и без скачивания.
    """

    def __init__(self, path: Path, legacy_quality: int = 192):
        self._conn = sqlite3.connect(path)
        self._conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS tracks (
                id   TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audio_files (
                track_id TEXT NOT NULL,
                quality  INTEGER NOT NULL,
                file_id  TEXT NOT NULL,
                PRIMARY KEY (track_id, quality)
            );
            CREATE TABLE IF NOT EXISTS library (
                user_id  INTEGER NOT NULL,
                position INTEGER NOT NULL,
                track_id TEXT NOT NULL,
                PRIMARY KEY (user_id, position)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id  INTEGER PRIMARY KEY,
                name     TEXT,
                added_by INTEGER,
                added_at TEXT NOT NULL
            );
            '''
        )
        self._migrate_files_table(legacy_quality)

    def _migrate_files_table(self, quality: int) -> None:
        # До появления настройки качества кэш file_id не знал битрейт: переносим его
        # с битрейтом, который тогда стоял в AUDIO_QUALITY
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'files'"
        ).fetchone()
        if not exists:
            return
        with self._conn:
            self._conn.execute(
                'INSERT OR IGNORE INTO audio_files (track_id, quality, file_id) '
                'SELECT track_id, ?, file_id FROM files',
                (quality,),
            )
            self._conn.execute('DROP TABLE files')

    def close(self) -> None:
        self._conn.close()

    def _upsert_tracks(self, tracks: Iterable[Track]) -> None:
        self._conn.executemany(
            'INSERT OR REPLACE INTO tracks (id, data) VALUES (?, ?)',
            [(t.id, json.dumps(t.to_dict(), ensure_ascii=False)) for t in tracks],
        )

    def save_tracks(self, tracks: Iterable[Track]) -> None:
        with self._conn:
            self._upsert_tracks(tracks)

    def get_track(self, track_id: str) -> Track | None:
        row = self._conn.execute('SELECT data FROM tracks WHERE id = ?', (track_id,)).fetchone()
        return Track.from_dict(json.loads(row[0])) if row else None

    # --- file_id отправленных mp3, отдельно для каждого битрейта ---

    def get_file_id(self, track_id: str, quality: int) -> str | None:
        row = self._conn.execute(
            'SELECT file_id FROM audio_files WHERE track_id = ? AND quality = ?', (track_id, quality)
        ).fetchone()
        return row[0] if row else None

    def set_file_id(self, track_id: str, quality: int, file_id: str) -> None:
        with self._conn:
            self._conn.execute(
                'INSERT OR REPLACE INTO audio_files (track_id, quality, file_id) VALUES (?, ?, ?)',
                (track_id, quality, file_id),
            )

    def delete_file_id(self, track_id: str, quality: int) -> None:
        with self._conn:
            self._conn.execute(
                'DELETE FROM audio_files WHERE track_id = ? AND quality = ?', (track_id, quality)
            )

    # --- Настройки, которые админ меняет из бота ---

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))

    # --- Пользователи, которым админ открыл доступ ---

    def add_allowed_user(self, user_id: int, name: str | None, added_by: int | None) -> bool:
        """True, если пользователь добавлен впервые."""
        with self._conn:
            cursor = self._conn.execute(
                'INSERT OR IGNORE INTO allowed_users (user_id, name, added_by, added_at) VALUES (?, ?, ?, ?)',
                (user_id, name, added_by, datetime.now(timezone.utc).isoformat(timespec='seconds')),
            )
            if not cursor.rowcount and name:
                self._conn.execute('UPDATE allowed_users SET name = ? WHERE user_id = ?', (name, user_id))
        return bool(cursor.rowcount)

    def remove_allowed_user(self, user_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute('DELETE FROM allowed_users WHERE user_id = ?', (user_id,))
        return bool(cursor.rowcount)

    def is_allowed_user(self, user_id: int) -> bool:
        return self._conn.execute('SELECT 1 FROM allowed_users WHERE user_id = ?', (user_id,)).fetchone() is not None

    def allowed_users(self) -> list[AllowedUser]:
        rows = self._conn.execute('SELECT user_id, name FROM allowed_users ORDER BY added_at, user_id').fetchall()
        return [AllowedUser(user_id, name) for user_id, name in rows]

    # --- Импортированная библиотека (новые треки первыми) ---

    def replace_library(self, user_id: int, tracks: list[Track]) -> None:
        with self._conn:
            self._upsert_tracks(tracks)
            self._conn.execute('DELETE FROM library WHERE user_id = ?', (user_id,))
            self._conn.executemany(
                'INSERT INTO library (user_id, position, track_id) VALUES (?, ?, ?)',
                [(user_id, position, t.id) for position, t in enumerate(tracks)],
            )

    def clear_library(self, user_id: int) -> None:
        with self._conn:
            self._conn.execute('DELETE FROM library WHERE user_id = ?', (user_id,))

    def library_size(self, user_id: int) -> int:
        return self._conn.execute('SELECT COUNT(*) FROM library WHERE user_id = ?', (user_id,)).fetchone()[0]

    def library_page(self, user_id: int, limit: int, offset: int) -> list[Track]:
        rows = self._conn.execute(
            '''
            SELECT t.data FROM library l JOIN tracks t ON t.id = l.track_id
            WHERE l.user_id = ? ORDER BY l.position LIMIT ? OFFSET ?
            ''',
            (user_id, limit, offset),
        ).fetchall()
        return [Track.from_dict(json.loads(row[0])) for row in rows]
