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
git clone https://github.com/wxrstvrsn/SpotipyLiker.git
cd SpotipyLiker
```

---

### 3. Создай приложение на Spotify Developer

- Перейди на https://developer.spotify.com/dashboard

- Создай новое приложение

- Получи:

-
    - Client ID

-
    - Client Secret

- В настройках добавь Redirect URI скриншота (http://127.0.0.1:8888/callback)
![man1](https://github.com/user-attachments/assets/967f8b50-4cc1-47bc-a2ef-c70ef62c3620)
![man2](https://github.com/user-attachments/assets/7726c79a-67c4-4f88-8848-14c95f483189)

---

### 4. Вставь свои ключи в ```like_track.py```

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
pyinstaller --noconsole --onefile like_track.py

```

- Файл появится в ```dist/like_track.exe```

## Добавление горячей клавиши (костыль но работает, все так делаю)

- Создайте ярлык на ```like_track.exe```

- Правый клик → Свойства

- В поле "**Быстрый вызов**" укажите:

 ```commandline
Ctrl + Alt + L (либо кастом комбинацию)
```

![Screenshot 2025-04-15 160843](https://github.com/user-attachments/assets/efc7e99a-2bcf-4fab-aaee-31c2423e29a3)

---
# Gimme a Star if it helps <3
