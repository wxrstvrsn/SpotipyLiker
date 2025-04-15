import spotipy
import os
from spotipy.oauth2 import SpotifyOAuth

SPOTIPY_CLIENT_ID = 'REDACTED_SECRET'
SPOTIPY_CLIENT_SECRET = 'REDACTED_SECRET'
SPOTIPY_REDIRECT_URI = 'http://127.0.0.1:8888/callback'


def main():
    scope = 'user-read-playback-state user-library-modify'

    token_dir = os.path.join(os.getenv('APPDATA'), 'SpotipyToken')
    os.makedirs(token_dir, exist_ok=True)
    cache_path = os.path.join(token_dir, 'token_cache.json')

    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=scope
    ))

    current = sp.current_playback()
    if current and current.get('item'):
        track = current['item']
        track_id = track['id']

        sp.current_user_saved_tracks_add([track_id])


if __name__ == '__main__':
    main()
