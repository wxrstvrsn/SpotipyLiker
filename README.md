# 🎧 Spotify Auto-Liker

Простой Python-скрипт для **автоматического лайка** текущего трека, проигрываемого в Spotify — по горячей клавише или
автозапуску при старте системы.

---

## 📚 Используемые библиотеки

- [Spotipy](https://spotipy.readthedocs.io/en/2.22.1/) — официальная Python-обёртка для Spotify Web API

---

## 🔥 Что делает этот скрипт?

- Получает информацию о **текущем проигрываемом треке**
- Добавляет его в "Любимые треки" на Spotify (ставит ❤️)
- Работает **в фоне**, без лишних окон
- Можно запускать по хоткею (`Ctrl + Alt + L`) (см. далее)

---

## 🚀 Как установить

### 1. Склонируй репозиторий

```
git clone https://github.com/yourusername/spotify-auto-liker.git
cd spotify-auto-liker
```

---

### 3. Создай приложение на Spotify Developer

![man1](https://github.com/user-attachments/assets/3f5b60e1-bc33-4855-a6ab-35581aee0afc)

- Перейди на https://developer.spotify.com/dashboard

- Создай новое приложение

- Получи:

-
    - Client ID

-
    - Client Secret

- В настройках добавь Redirect URI скриншота (http://127.0.0.1:8888/callback)
- 
![man2](https://github.com/user-attachments/assets/fcf392da-198d-4ca9-ad8d-a6e24a2b3fbb)

---

### 4. Вставь свои ключи в ```.env```

```commandline
SPOTIPY_CLIENT_ID = 'ваш_CLIENT_ID'
SPOTIPY_CLIENT_SECRET = 'ваш_CLIENT_SECRET'
SPOTIPY_REDIRECT_URI = 'http://127.0.0.1:8888/callback'
```
---
### ✅ Первый запуск

```commandline
python like_track.py
```

- откроется окно браузера → авторизуйтесь
- далее авторизация запрашиваться не будет, стораджим токен в ```%APPDATA%/SpotipyToken'```

## 🧙‍♂️ (Опционально) Как сделать .exe + горячую клавишу

## Сборка в .exe (Windows)

```commandline
pip install pyinstaller
pyinstaller --noconsole --onefile track_liker.py

```

- Файл появится в ```dist/like_track.exe```

## Добавление горячей клавиши (костыль но работает, все так делаю)

- Создайте ярлык на ```like_track.exe```

- Правый клик → Свойства

- В поле "Быстрая клавиша" укажите:

```commandline
Ctrl + Alt + L (либо кастом комбинацию)
```
---
# Gimme a Star if it helps <3
