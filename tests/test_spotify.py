import pytest

from bot.spotify import AuthorizationError, NotAuthorizedError, SpotifyService, Track

from .conftest import make_track

API_TRACK = {
    'id': 'abc',
    'name': 'Song',
    'artists': [{'name': 'A'}, {'name': 'B'}],
    'duration_ms': 200_000,
    'track_number': 3,
    'disc_number': 1,
    'album': {
        'name': 'Album',
        'artists': [{'name': 'A'}],
        'release_date': '2020',
        'images': [
            {'url': 'small', 'width': 64, 'height': 64},
            {'url': 'big', 'width': 640, 'height': 640},
            {'url': 'mid', 'width': 300, 'height': 300},
        ],
    },
}


def test_track_from_api():
    track = Track.from_api(API_TRACK)
    assert track == Track(
        id='abc', title='Song', artists=('A', 'B'), album='Album', album_artists=('A',),
        duration_ms=200_000, track_number=3, disc_number=1, release_date='2020',
        cover_url='big', thumb_url='mid',
    )
    assert track.display_name == 'A, B — Song'


def test_track_dict_roundtrip(track):
    assert Track.from_dict(track.to_dict()) == track


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def current_user_saved_tracks(self, limit, offset):
        self.calls.append((limit, offset))
        chunk = self.items[offset:offset + limit]
        return {
            'items': chunk,
            'total': len(self.items),
            'next': 'more' if offset + limit < len(self.items) else None,
        }


def saved_item(i):
    return {'added_at': '2024-01-01T00:00:00Z', 'track': {**API_TRACK, 'id': f'id{i}', 'name': f'Song {i}'}}


@pytest.fixture
def service(tmp_path):
    return SpotifyService('cid', 'secret', 'http://127.0.0.1:8888/callback', tmp_path)


def test_latest_tracks_paginates_and_skips_local_files(service, monkeypatch):
    items = [saved_item(i) for i in range(120)]
    items[1] = {'track': {**API_TRACK, 'id': None, 'is_local': True}}
    client = FakeClient(items)
    monkeypatch.setattr(service, '_client', lambda user_id: client)

    tracks = service.latest_tracks(1, 60)

    assert len(tracks) == 60
    assert tracks[0].id == 'id0'
    assert 'id1' not in [t.id for t in tracks]
    assert client.calls == [(50, 0), (50, 50)]


def test_latest_tracks_stops_at_library_end(service, monkeypatch):
    client = FakeClient([saved_item(i) for i in range(7)])
    monkeypatch.setattr(service, '_client', lambda user_id: client)
    assert len(service.latest_tracks(1, 50)) == 7


def test_saved_tracks_page(service, monkeypatch):
    client = FakeClient([saved_item(i) for i in range(25)])
    monkeypatch.setattr(service, '_client', lambda user_id: client)
    tracks, total = service.saved_tracks(1, 10, 20)
    assert total == 25
    assert [t.id for t in tracks] == [f'id{i}' for i in range(20, 25)]


def test_not_authorized_without_token(service):
    assert service.is_authorized(42) is False
    with pytest.raises(NotAuthorizedError):
        service.saved_tracks(42, 10, 0)


def test_authorize_url_contains_state(service):
    url = service.authorize_url(1, 'st4te')
    assert 'state=st4te' in url
    assert 'scope=user-library-read' in url
    assert 'client_id=cid' in url


@pytest.mark.parametrize('url, message', [
    ('http://127.0.0.1:8888/callback?code=XYZ&state=other', 'последнего запроса'),
    ('http://127.0.0.1:8888/callback?state=st4te', 'code'),
    ('http://127.0.0.1:8888/callback?error=access_denied&state=st4te', 'access_denied'),
])
def test_complete_authorization_validates_response(service, url, message):
    with pytest.raises(AuthorizationError, match=message):
        service.complete_authorization(1, url, 'st4te')


def test_complete_authorization_exchanges_code(service, monkeypatch):
    calls = []

    def fake_get_access_token(self, code, as_dict, check_cache):
        calls.append(code)
        self.cache_handler.save_token_to_cache({'access_token': 't', 'refresh_token': 'r',
                                                'expires_at': 9_999_999_999, 'scope': 'user-library-read'})

    monkeypatch.setattr('bot.spotify.SpotifyOAuth.get_access_token', fake_get_access_token)
    service.complete_authorization(7, ' http://127.0.0.1:8888/callback?code=XYZ&state=st4te ', 'st4te')

    assert calls == ['XYZ']
    assert service.is_authorized(7)
    service.logout(7)
    assert not service.is_authorized(7)


def test_make_track_helper_is_valid():
    assert make_track().artist == 'Daft Punk, Pharrell Williams, Nile Rodgers'


def test_random_tracks_are_distinct_and_fetched_by_pages(service, monkeypatch):
    client = FakeClient([saved_item(i) for i in range(120)])
    monkeypatch.setattr(service, '_client', lambda user_id: client)

    tracks = service.random_tracks(1, 10)

    ids = [t.id for t in tracks]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert set(ids) <= {f'id{i}' for i in range(120)}
    # 1 запрос за количеством + не больше 3 страниц по 50
    assert len(client.calls) <= 4
    assert all(offset % 50 == 0 for _, offset in client.calls[1:])


def test_random_tracks_small_library(service, monkeypatch):
    client = FakeClient([saved_item(i) for i in range(7)])
    monkeypatch.setattr(service, '_client', lambda user_id: client)
    assert sorted(t.id for t in service.random_tracks(1, 50)) == sorted(f'id{i}' for i in range(7))
