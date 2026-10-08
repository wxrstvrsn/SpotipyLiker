import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .spotify import Track


class Storage:
    """SQLite-кэш: метаданные треков (для кнопок) и file_id уже отправленных mp3.

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
            '''
        )

    def close(self) -> None:
        self._conn.close()

    def save_tracks(self, tracks: Iterable[Track]) -> None:
        with self._conn:
            self._conn.executemany(
                'INSERT OR REPLACE INTO tracks (id, data) VALUES (?, ?)',
                [(t.id, json.dumps(t.to_dict(), ensure_ascii=False)) for t in tracks],
            )

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
