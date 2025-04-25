import os
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from plyer import notification

# --- Уведомление ---
def notify(title, message):
    notification.notify(
        title=title,
        message=message,
        timeout=5  # секунд
    )

# --- Основная логика ---
def main():
    scope = 'user-read-playback-state user-library-modify user-library-read'

    token_dir = os.path.join(os.getenv('APPDATA'), 'SpotipyToken')
    os.makedirs(token_dir, exist_ok=True)
    cache_path = os.path.join(token_dir, 'token_cache.json')

    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id='',
        client_secret='',
        redirect_uri='http://127.0.0.1:8888/callback',
        scope=scope,
        cache_path=cache_path
    ))

    time.sleep(1)  # немного подождать для надёжности

    try:
        current = sp.current_playback()
        if current and current.get('item'):
            track = current['item']
            track_id = track['id']
            track_name = track['name']
            artist_name = track['artists'][0]['name']

            sp.current_user_saved_tracks_add([track_id])
            saved = sp.current_user_saved_tracks_contains([track_id])

            if saved[0]:
                notify("Добавлено в избранное", f"{artist_name} — {track_name}")
            else:
                notify("Ошибка", f"Не удалось добавить: {artist_name} — {track_name}")
        else:
            notify("Нет воспроизведения", "Spotify сейчас ничего не проигрывает")
    except Exception as e:
        notify("Ошибка", str(e))

if __name__ == '__main__':
    main()
