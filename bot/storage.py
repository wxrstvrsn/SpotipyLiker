import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .spotify import Track


class Storage:
    """SQLite: метаданные треков (для кнопок), file_id уже отправленных mp3 и
    импортированные библиотеки пользователей (режим без Spotify API).

    file_id привязан к боту, а не к чату, поэтому трек, однажды загруженный в Telegram,
    повторно отправляется мгновенно и без скачивания.
    """

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path)
        self._conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS tracks (
                id   TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                track_id TEXT PRIMARY KEY,
                file_id  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS library (
                user_id  INTEGER NOT NULL,
                position INTEGER NOT NULL,
                track_id TEXT NOT NULL,
                PRIMARY KEY (user_id, position)
            );
            '''
        )

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

    def get_file_id(self, track_id: str) -> str | None:
        row = self._conn.execute('SELECT file_id FROM files WHERE track_id = ?', (track_id,)).fetchone()
        return row[0] if row else None

    def set_file_id(self, track_id: str, file_id: str) -> None:
        with self._conn:
            self._conn.execute(
                'INSERT OR REPLACE INTO files (track_id, file_id) VALUES (?, ?)', (track_id, file_id)
            )

    def delete_file_id(self, track_id: str) -> None:
        with self._conn:
            self._conn.execute('DELETE FROM files WHERE track_id = ?', (track_id,))

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
