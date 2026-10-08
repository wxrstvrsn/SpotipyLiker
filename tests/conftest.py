import shutil
import subprocess
from pathlib import Path

import pytest

from bot.spotify import Track


def make_track(**overrides) -> Track:
    data = dict(
        id='4uLU6hMCjMI75M1A2tKUQC',
        title='Get Lucky (feat. Pharrell Williams and Nile Rodgers)',
        artists=('Daft Punk', 'Pharrell Williams', 'Nile Rodgers'),
        album='Random Access Memories',
        album_artists=('Daft Punk',),
        duration_ms=369_000,
        track_number=8,
        disc_number=1,
        release_date='2013-05-17',
        cover_url='https://i.scdn.co/image/cover640',
        thumb_url='https://i.scdn.co/image/cover300',
    )
    data.update(overrides)
    return Track(**data)


@pytest.fixture
def track() -> Track:
    return make_track()


def write_mp3(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
         '-metadata', 'title=SoundCloud title', '-c:a', 'libmp3lame', '-b:a', '64k', str(path)],
        check=True,
    )
    return path


requires_ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg не установлен')
