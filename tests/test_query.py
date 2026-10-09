import pytest

from bot.query import split_artist_title, track_from_query


@pytest.mark.parametrize('text, artists, title', [
    ('Daft Punk - Get Lucky', ('Daft Punk',), 'Get Lucky'),
    ('Daft Punk — Get Lucky', ('Daft Punk',), 'Get Lucky'),
    ('Daft Punk—Get Lucky', ('Daft Punk',), 'Get Lucky'),
    ('Король и Шут – Кукла колдуна', ('Король и Шут',), 'Кукла колдуна'),
    ('  Avicii   -  Levels - Skrillex Remix ', ('Avicii',), 'Levels - Skrillex Remix'),
    ('Daft Punk, Pharrell Williams - Get Lucky', ('Daft Punk', 'Pharrell Williams'), 'Get Lucky'),
    ('Jay-Z - 99 Problems', ('Jay-Z',), '99 Problems'),
    # без разделителя вся строка — название, исполнителя возьмём из найденного трека
    ('daft punk get lucky', (), 'daft punk get lucky'),
    ('Jay-Z 99 Problems', (), 'Jay-Z 99 Problems'),
    ('- Get Lucky', (), '- Get Lucky'),
])
def test_track_from_query(text, artists, title):
    track = track_from_query(text)
    assert (track.artists, track.title) == (artists, title)
    assert track.duration_ms == 0
    assert track.id.startswith('_') and len(track.id) == 22


def test_same_query_same_id():
    assert track_from_query('Daft Punk - Get Lucky').id == track_from_query('daft punk  -  get lucky').id


def test_empty_query():
    assert track_from_query('   ') is None


def test_display_name_without_artist():
    assert track_from_query('daft punk get lucky').display_name == 'daft punk get lucky'


def test_split_artist_title():
    assert split_artist_title('Daft Punk - Get Lucky (Official Audio)') == ('Daft Punk', 'Get Lucky (Official Audio)')
    assert split_artist_title('Get Lucky') is None
