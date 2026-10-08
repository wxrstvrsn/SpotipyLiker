# 🎧 Spotify Auto-Liker

Простой Python-скрипт для **автоматического лайка** текущего трека, проигрываемого в Spotify — по горячей клавише или
автозапуску при старте системы.

> 🤖 Также в репозитории есть **Telegram-бот**, который присылает ваши любимые треки из Spotify в виде mp3 с SoundCloud —
> см. раздел [Telegram-бот](#-telegram-бот-любимые-треки--mp3).

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

### 4. Вставь свои ключи в ```track_liker.py```

```commandline
SPOTIPY_CLIENT_ID = 'ваш_CLIENT_ID'
SPOTIPY_CLIENT_SECRET = 'ваш_CLIENT_SECRET'
SPOTIPY_REDIRECT_URI = 'http://127.0.0.1:8888/callback'

```
---
### ✅ Первый запуск

```commandline
python track_liker.py
```

- откроется окно браузера → авторизуйтесь
- далее авторизация запрашиваться не будет, стораджим токен в ```%APPDATA%/SpotipyToken'```

## 🧙‍♂️ (Опционально) Как сделать .exe + горячую клавишу

## Сборка в .exe (Windows)

```commandline
pip install pyinstaller
pyinstaller --noconsole --onefile track_liker.py

```

- Файл появится в ```dist/track_liker.exe```

## Добавление горячей клавиши (костыль но работает, все так делаю)

- Создайте ярлык на ```track_liker.exe```

- Правый клик → Свойства

- В поле "**Быстрый вызов**" укажите:

 ```commandline
Ctrl + Alt + L (либо кастом комбинацию)
```

![Screenshot 2025-04-15 160843](https://github.com/user-attachments/assets/efc7e99a-2bcf-4fab-aaee-31c2423e29a3)

---

# 🤖 Telegram-бот: любимые треки → mp3

Бот берёт ваши «Любимые треки» из Spotify через [Spotipy](https://spotipy.readthedocs.io/), находит каждый трек
на SoundCloud с помощью [yt-dlp](https://github.com/yt-dlp/yt-dlp), скачивает его в mp3 и прописывает теги
из Spotify: название, исполнители, альбом, исполнитель альбома, номер трека, год и обложка.

## Что умеет

- `/tracks` — листать любимые треки: по **10 треков на страницу**, каждый трек — кнопка, внизу ◀️ / ▶️.
  Нажали на трек — бот скачивает его и присылает в диалог.
- `/last N` — скачать **N последних добавленных** треков (без числа — `LAST_TRACKS_DEFAULT`, по умолчанию 10).
  Треки приходят от старых к новым, в конце — сводка, что не удалось найти.
- `/login`, `/logout` — подключить/отключить свой аккаунт Spotify (у каждого пользователя бота свой токен).
- Повторная отправка трека мгновенная: бот запоминает `file_id` уже загруженного в Telegram файла.

## Как бот ищет трек

1. Запрос на SoundCloud: `<основной исполнитель> <название>` (без `feat.` и хвостов вроде `- Remastered 2011`).
2. Из результатов выбирается лучший кандидат:
   - длительность совпадает со Spotify (допуск ±6%, минимум ±7 с);
   - в названии есть слова из названия трека, плюс бонус за совпадение исполнителя;
   - отбрасываются ремиксы, каверы, live, sped up / slowed, nightcore и т.п., если это не та версия, что в Spotify.
3. 30-секундные превью (треки SoundCloud Go+) пропускаются — бот пробует следующего кандидата.
4. Если в `SEARCH_SOURCES` указано несколько источников (например, `soundcloud,youtube`), следующий используется,
   когда в предыдущем ничего не нашлось.

## Установка

Нужны **Python 3.10+** и **ffmpeg** (yt-dlp конвертирует звук в mp3 через него).

### 1. Создай бота

Напиши [@BotFather](https://t.me/BotFather) → `/newbot` → получи токен.

### 2. Настрой приложение Spotify

Как в разделе выше: https://developer.spotify.com/dashboard → приложение → `Client ID`, `Client Secret`,
Redirect URI `http://127.0.0.1:8888/callback`.

Пока приложение в **Development mode**, Spotify пускает только пользователей из вкладки **User Management** —
добавь туда e-mail своего Spotify-аккаунта (и аккаунтов тех, кто ещё будет пользоваться ботом).
По правилам Spotify с февраля 2026 года у владельца приложения в Development mode должен быть Premium.

### 3. Заполни `.env`

```commandline
cp .env.example .env
```

Обязательные переменные: `TELEGRAM_BOT_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`.
Советую задать и `ALLOWED_USER_IDS` — иначе бот будет скачивать треки для любого, кто его найдёт.
Остальные настройки описаны в [`.env.example`](.env.example).

### 4. Запусти

```commandline
pip install -r requirements.txt
python -m bot
```

или в Docker (ffmpeg уже внутри, данные — в `./data`):

```commandline
docker compose up -d --build
```

### 5. Подключи Spotify в чате с ботом

1. Отправь боту `/login` и нажми кнопку «Войти через Spotify».
2. Разреши доступ. Spotify перенаправит на `http://127.0.0.1:8888/callback?code=...` —
   страница не откроется, **это нормально**.
3. Скопируй адрес из адресной строки целиком и отправь боту. Бот обменяет код на токен
   (хранится в `data/tokens/<telegram_id>.json` и дальше обновляется сам) и удалит сообщение с кодом.

Готово: `/tracks` или `/last 20`.

## Ограничения

- Telegram не даёт ботам отправлять файлы больше **50 МБ** (для mp3 192 кбит/с это ~35 минут).
- Треки, которые на SoundCloud есть только по подписке Go+, скачать нельзя — бот напишет, что не нашёл трек.
- Сайты меняются, поэтому yt-dlp стоит регулярно обновлять: `pip install -U yt-dlp`.
  Для YouTube свежим версиям yt-dlp может понадобиться JS-рантайм (например, deno) — см. вики yt-dlp.
- Скачивай только то, что тебе разрешено скачивать.

## Структура

```
bot/
├── __main__.py     # запуск: python -m bot
├── config.py       # настройки из .env
├── spotify.py      # OAuth для каждого пользователя, любимые треки (Spotipy)
├── downloader.py   # поиск на SoundCloud, выбор совпадения, mp3 + теги (yt-dlp, mutagen)
├── sender.py       # отправка в Telegram, кэш file_id, защита от параллельных загрузок
├── storage.py      # SQLite: метаданные треков для кнопок и file_id
├── keyboards.py    # кнопки: 10 треков на страницу + навигация
├── handlers.py     # команды и нажатия кнопок (aiogram 3)
└── middlewares.py  # доступ только для ALLOWED_USER_IDS
```

Тесты (сеть не нужна, ffmpeg нужен для тестов тегирования):

```commandline
pip install -r requirements-dev.txt
pytest
```

---
# Gimme a Star if it helps <3
