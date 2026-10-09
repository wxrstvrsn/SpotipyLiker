import io
import json
import zipfile

import pytest

from bot.importer import ImportFormatError, parse_library_file

EXPORTIFY_CSV = '''﻿"Track URI","Track Name","Artist URI(s)","Artist Name(s)","Album URI","Album Name","Album Artist URI(s)","Album Artist Name(s)","Album Release Date","Album Image URL","Disc Number","Track Number","Track Duration (ms)","Track Preview URL","Explicit","Popularity","ISRC","Added By","Added At"
"spotify:track:0DiWol3AO6WpXZgp0goxAV","One More Time","spotify:artist:4tZwfgrHOc3mvqYlEYSvVi","Daft Punk","spotify:album:2noRn2Aes5aoNVsU6iWThc","Discovery","spotify:artist:4tZwfgrHOc3mvqYlEYSvVi","Daft Punk","2001-03-12","https://i.scdn.co/image/discovery","1","1","320357","","false","80","GBDUW0000053","","2021-01-05T10:00:00Z"
"spotify:track:69kOkLUCkxIZYexIgSG8rq","Get Lucky (feat. Pharrell Williams and Nile Rodgers)","x,y,z","Daft Punk,Pharrell Williams,Nile Rodgers","spotify:album:4m2880jivSbbyEGAKfITCa","Random Access Memories","x","Daft Punk","2013-05-17","https://i.scdn.co/image/ram","1","8","369626","","false","85","USQX91300108","","2023-07-01T08:30:00Z"
"spotify:local:Artist:Album:Demo:200","Demo","","Local Artist","","","","","","","0","0","200000","","false","0","","","2022-02-02T00:00:00Z"
'''

# Формат форка Exportify с exportify.net, разделитель «;» — как после пересохранения в Excel
EXPORTIFY_NET_CSV = '''Track URI;Track Name;Album Name;Artist Name(s);Release Date;Duration (ms);Popularity;Added By;Added At;Genres
spotify:track:0DiWol3AO6WpXZgp0goxAV;One More Time;Discovery;Daft Punk;2001-03-12;320357;80;;2021-01-05T10:00:00Z;house
'''

TUNEMYMUSIC_CSV = '''Track name,Artist name,Album,Playlist name,Type,ISRC,Spotify - id
Кукла колдуна,Король и Шут,Акустический альбом,Liked Songs,Playlist,,https://open.spotify.com/track/1xJ0xHsYHdRdjJ0f4JbZJx
Без ссылки,Кто-то,,Liked Songs,Playlist,,
'''

YOUR_LIBRARY = {
    'tracks': [
        {'artist': 'Daft Punk', 'album': 'Discovery', 'track': 'One More Time',
         'uri': 'spotify:track:0DiWol3AO6WpXZgp0goxAV'},
        {'artist': 'Король и Шут', 'album': 'Акустический альбом', 'track': 'Кукла колдуна',
         'uri': 'spotify:track:1xJ0xHsYHdRdjJ0f4JbZJx'},
        {'artist': 'Daft Punk', 'album': 'Discovery', 'track': 'One More Time',
         'uri': 'spotify:track:0DiWol3AO6WpXZgp0goxAV'},  # дубль
    ],
    'albums': [{'artist': 'Daft Punk', 'album': 'Discovery', 'uri': 'spotify:album:x'}],
    'shows': [],
}


def test_exportify_csv_sorted_newest_first_with_full_metadata():
    result = parse_library_file('liked.csv', EXPORTIFY_CSV.encode())

    assert result.sorted_by_date
    assert [t.title for t in result.tracks] == [
        'Get Lucky (feat. Pharrell Williams and Nile Rodgers)', 'Demo', 'One More Time',
    ]
    lucky = result.tracks[0]
    assert lucky.id == '69kOkLUCkxIZYexIgSG8rq'
    assert lucky.artists == ('Daft Punk', 'Pharrell Williams', 'Nile Rodgers')
    assert lucky.album == 'Random Access Memories'
    assert lucky.album_artists == ('Daft Punk',)
    assert lucky.duration_ms == 369626
    assert lucky.track_number == 8
    assert lucky.disc_number == 1
    assert lucky.release_date == '2013-05-17'
    assert lucky.cover_url == 'https://i.scdn.co/image/ram'
    assert lucky.thumb_url is None
    # Локальный файл без id Spotify получает синтетический id
    demo = result.tracks[1]
    assert demo.id.startswith('_') and len(demo.id) == 22


def test_exportify_net_semicolon_csv():
    (track,) = parse_library_file('liked.csv', EXPORTIFY_NET_CSV.encode()).tracks
    assert track.id == '0DiWol3AO6WpXZgp0goxAV'
    assert track.album == 'Discovery'
    assert track.duration_ms == 320357
    assert track.release_date == '2001-03-12'


def test_csv_in_cp1251_without_dates_keeps_file_order():
    result = parse_library_file('library.csv', TUNEMYMUSIC_CSV.encode('cp1251'))
    assert not result.sorted_by_date
    first, second = result.tracks
    assert (first.id, first.title, first.artists) == ('1xJ0xHsYHdRdjJ0f4JbZJx', 'Кукла колдуна', ('Король и Шут',))
    assert first.duration_ms == 0
    assert second.id.startswith('_')


def test_your_library_json_dedupes_and_keeps_order():
    result = parse_library_file('YourLibrary.json', json.dumps(YOUR_LIBRARY).encode())
    assert not result.sorted_by_date
    assert [t.id for t in result.tracks] == ['0DiWol3AO6WpXZgp0goxAV', '1xJ0xHsYHdRdjJ0f4JbZJx']
    assert result.tracks[0].album == 'Discovery'


def test_spotify_data_export_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('Spotify Account Data/ReadMeFirst_YourAccountData.pdf', b'%PDF')
        archive.writestr('Spotify Account Data/StreamingHistory_music_0.json', b'[]')
        archive.writestr('Spotify Account Data/YourLibrary.json', json.dumps(YOUR_LIBRARY))
    result = parse_library_file('my_spotify_data.zip', buffer.getvalue())
    assert len(result.tracks) == 2


def test_json_without_extension_is_detected():
    result = parse_library_file('', json.dumps(YOUR_LIBRARY).encode())
    assert len(result.tracks) == 2


@pytest.mark.parametrize('filename, data, message', [
    ('photo.jpg', b'\xff\xd8\xff\xe0 binary', 'колонок'),
    ('list.csv', b'a,b\n1,2\n', 'колонок'),
    ('list.csv', b'Track Name,Artist Name(s)\n', 'ни одного трека'),
    ('YourLibrary.json', b'{"albums": []}', 'tracks'),
    ('YourLibrary.json', b'{oops', 'JSON'),
    ('data.zip', b'PK\x03\x04broken', 'zip'),
    ('noise.bin', bytes(range(256)) * 20, 'CSV|колонок'),
])
def test_bad_files(filename, data, message):
    with pytest.raises(ImportFormatError, match=message):
        parse_library_file(filename, data)


def test_zip_without_library():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('readme.txt', 'hi')
    with pytest.raises(ImportFormatError, match='YourLibrary.json'):
        parse_library_file('x.zip', buffer.getvalue())
