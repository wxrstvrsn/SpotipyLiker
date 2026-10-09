"""Сквозные тесты обработчиков: апдейты прогоняются через Dispatcher, Telegram API подменён."""

import asyncio
import dataclasses
import itertools
from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetChat,
    GetFile,
    SendAudio,
    SendMessage,
)
from aiogram.types import (
    Audio,
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    SharedUser,
    Update,
    User,
    UsersShared,
)

from bot.__main__ import build_dispatcher
from bot.admin import AccessRequestCallback, AdminCallback
from bot.config import Settings
from bot.downloader import DownloadResult, TrackNotFoundError
from bot.keyboards import PageCallback, TrackCallback
from bot.query import track_from_query
from bot.runtime import RuntimeSettings
from bot.sender import TrackSender
from bot.spotify import NotAuthorizedError
from bot.storage import Storage

from .conftest import make_track

USER_ID = 1
ADMIN_ID = 2
_ids = itertools.count(1)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.file_content = b''

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id='f', file_path='documents/file')
        if isinstance(method, GetChat):
            raise TelegramBadRequest(method=method, message='Bad Request: chat not found')
        if isinstance(method, (SendMessage, EditMessageText, SendAudio)):
            message = Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=method.chat_id or USER_ID, type='private'),
                text=getattr(method, 'text', None),
                audio=Audio(file_id=f'FILE-{method.title}', file_unique_id='u', duration=1)
                if isinstance(method, SendAudio) else None,
            )
            return message.as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):
        yield self.file_content

    async def close(self):
        pass

    def of(self, method_type):
        return [r for r in self.requests if isinstance(r, method_type)]


class FakeSpotify:
    def __init__(self, tracks, authorized=True):
        self.tracks = tracks
        self.authorized = authorized
        self.auth_calls = []

    def _check(self):
        if not self.authorized:
            raise NotAuthorizedError

    def is_authorized(self, user_id):
        return self.authorized

    def saved_tracks(self, user_id, limit, offset):
        self._check()
        return self.tracks[offset:offset + limit], len(self.tracks)

    def latest_tracks(self, user_id, count):
        self._check()
        return self.tracks[:count]

    def authorize_url(self, user_id, state):
        return f'https://accounts.spotify.com/authorize?state={state}'

    def complete_authorization(self, user_id, url, state):
        self.auth_calls.append((url, state))
        self.authorized = True

    def logout(self, user_id):
        self.authorized = False


class FakeDownloader:
    def __init__(self, tmp_path: Path, missing=()):
        self.tmp_path = tmp_path
        self.missing = set(missing)
        self.calls = []
        self.tracks = []
        self.qualities = []

    def download(self, track, quality=None):
        self.calls.append(track.id)
        self.tracks.append(track)
        self.qualities.append(quality)
        if track.id in self.missing:
            raise TrackNotFoundError(track.display_name)
        path = self.tmp_path / f'{track.id}.mp3'
        path.write_bytes(b'ID3 fake mp3')
        return DownloadResult(path=path, duration=track.duration_ms // 1000,
                              source_url='https://sc/x', thumbnail=b'jpeg', track=track)


TRACKS = [make_track(id=f'track{i:02d}', title=f'Song {i}') for i in range(25)]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        bot_token='42:TEST', spotify_client_id='id', spotify_client_secret='secret',
        spotify_redirect_uri='http://127.0.0.1:8888/callback', allowed_user_ids=frozenset({USER_ID}),
        admin_user_ids=frozenset({ADMIN_ID}),
        data_dir=tmp_path, last_tracks_default=5, last_tracks_max=50,
        search_sources=('soundcloud',), audio_quality=192, max_concurrent_downloads=2,
    )


def _make_env(tmp_path, settings, spotify):
    storage = Storage(tmp_path / 'db.sqlite3', legacy_quality=settings.audio_quality)
    runtime = RuntimeSettings(settings, storage)
    downloader = FakeDownloader(tmp_path)
    sender = TrackSender(downloader, storage, settings.max_concurrent_downloads, runtime)
    session = FakeSession()
    bot = Bot('42:TEST', session=session, default=DefaultBotProperties(parse_mode='HTML'))
    dp = build_dispatcher(settings, spotify, storage, sender, runtime)
    return type('Env', (), dict(storage=storage, spotify=spotify, downloader=downloader, sender=sender,
                                runtime=runtime, session=session, bot=bot, dp=dp, tmp_path=tmp_path))


def _close_env(env):
    # роутеры модульные: отвязываем их, чтобы следующий тест мог собрать новый Dispatcher
    env.dp.sub_routers.clear()
    from bot.admin import router as admin_router
    from bot.handlers import router
    router._parent_router = None
    admin_router._parent_router = None
    env.storage.close()


@pytest.fixture
def env(tmp_path, settings):
    env = _make_env(tmp_path, settings, FakeSpotify(TRACKS))
    yield env
    _close_env(env)


@pytest.fixture
def env_no_api(tmp_path, settings):
    """Бот без ключей Spotify: только импорт выгрузок."""
    settings = dataclasses.replace(settings, spotify_client_id=None, spotify_client_secret=None)
    env = _make_env(tmp_path, settings, None)
    yield env
    _close_env(env)


def user(user_id=USER_ID):
    return User(id=user_id, is_bot=False, first_name='Test')


def message_update(text, user_id=USER_ID):
    return Update(update_id=next(_ids), message=Message(
        message_id=next(_ids), date=datetime.now(), chat=Chat(id=user_id, type='private'),
        from_user=user(user_id), text=text,
    ))


def document_update(filename, size, user_id=USER_ID, caption=None):
    return Update(update_id=next(_ids), message=Message(
        message_id=next(_ids), date=datetime.now(), chat=Chat(id=user_id, type='private'),
        from_user=user(user_id), caption=caption,
        document=Document(file_id=f'doc{next(_ids)}', file_unique_id='d', file_name=filename, file_size=size),
    ))


def callback_update(data, user_id=USER_ID):
    return Update(update_id=next(_ids), callback_query=CallbackQuery(
        id=str(next(_ids)), from_user=user(user_id), chat_instance='ci', data=data,
        message=Message(message_id=next(_ids), date=datetime.now(),
                        chat=Chat(id=user_id, type='private'), text='list'),
    ))


async def feed(env, update):
    await env.dp.feed_update(env.bot, update)


async def test_tracks_shows_first_page(env):
    await feed(env, message_update('/tracks'))

    (sent,) = env.session.of(SendMessage)
    assert 'Любимые треки</b>: 25' in sent.text
    rows = sent.reply_markup.inline_keyboard
    assert len(rows) == 11
    assert rows[0][0].text == 'Daft Punk, Pharrell Williams, Nile Rodgers — Song 0'
    assert [b.text for b in rows[-1]] == ['🔄 1/3', '▶️']
    assert env.storage.get_track('track09') == TRACKS[9]


async def test_page_navigation_edits_message(env):
    await feed(env, callback_update(PageCallback(page=2).pack()))

    (edited,) = env.session.of(EditMessageText)
    assert 'Страница 3 из 3' in edited.text
    rows = edited.reply_markup.inline_keyboard
    assert len(rows) == 6  # 5 треков + навигация
    assert [b.text for b in rows[-1]] == ['◀️', '🔄 3/3']
    assert env.session.of(AnswerCallbackQuery)


async def test_page_out_of_range_is_clamped(env):
    await feed(env, callback_update(PageCallback(page=10).pack()))
    (edited,) = env.session.of(EditMessageText)
    assert 'Страница 3 из 3' in edited.text


async def test_track_button_downloads_then_uses_cache(env):
    env.storage.save_tracks(TRACKS)
    data = TrackCallback(track_id='track03').pack()

    await feed(env, callback_update(data))

    (audio,) = env.session.of(SendAudio)
    assert audio.title == 'Song 3'
    assert audio.performer == 'Daft Punk, Pharrell Williams, Nile Rodgers'
    assert audio.audio.filename == 'Daft Punk, Pharrell Williams, Nile Rodgers - Song 3.mp3'
    assert audio.thumbnail is not None
    assert env.downloader.calls == ['track03']
    assert env.storage.get_file_id('track03', 192) == 'FILE-Song 3'
    assert env.downloader.qualities == [192]
    assert not (env.tmp_path / 'track03.mp3').exists()
    # статус «Ищу и скачиваю» удалён после отправки
    assert len(env.session.of(SendMessage)) == 1
    assert len(env.session.of(DeleteMessage)) == 1

    env.session.requests.clear()
    await feed(env, callback_update(data))

    (audio,) = env.session.of(SendAudio)
    assert audio.audio == 'FILE-Song 3'
    assert env.downloader.calls == ['track03']
    assert not env.session.of(SendMessage)


async def test_parallel_clicks_download_once(env):
    env.storage.save_tracks(TRACKS)
    data = TrackCallback(track_id='track01').pack()
    await asyncio.gather(feed(env, callback_update(data)), feed(env, callback_update(data)))
    assert env.downloader.calls == ['track01']
    assert len(env.session.of(SendAudio)) == 2


async def test_track_not_found_reports_error(env):
    env.storage.save_tracks(TRACKS)
    env.downloader.missing = {'track05'}
    await feed(env, callback_update(TrackCallback(track_id='track05').pack()))

    assert not env.session.of(SendAudio)
    (edited,) = env.session.of(EditMessageText)
    assert 'не нашёл подходящий трек' in edited.text


async def test_unknown_track_button(env):
    await feed(env, callback_update(TrackCallback(track_id='nope').pack()))
    (answer,) = env.session.of(AnswerCallbackQuery)
    assert answer.show_alert
    assert not env.downloader.calls


async def test_last_sends_oldest_first_and_reports_failures(env):
    env.downloader.missing = {'track01'}
    await feed(env, message_update('/last 3'))

    titles = [a.title for a in env.session.of(SendAudio)]
    assert titles == ['Song 2', 'Song 0']
    summary = env.session.of(SendMessage)[-1].text
    assert 'Готово: 2 из 3' in summary
    assert 'Song 1' in summary


async def test_last_default_and_validation(env):
    await feed(env, message_update('/last'))
    assert len(env.session.of(SendAudio)) == 5

    env.session.requests.clear()
    await feed(env, message_update('/last 500'))
    assert not env.session.of(SendAudio)
    assert 'от 1 до 50' in env.session.of(SendMessage)[0].text


async def test_not_authorized_user_is_sent_to_login(env):
    env.spotify.authorized = False
    await feed(env, message_update('/tracks'))
    (sent,) = env.session.of(SendMessage)
    assert '/login' in sent.text


async def test_login_flow(env):
    env.spotify.authorized = False
    await feed(env, message_update('http://127.0.0.1:8888/callback?code=abc&state=x'))
    assert 'Сначала отправьте /login' in env.session.of(SendMessage)[-1].text

    await feed(env, message_update('/login'))
    button = env.session.of(SendMessage)[-1].reply_markup.inline_keyboard[0][0]
    state = button.url.split('state=')[1]

    await feed(env, message_update(f'http://127.0.0.1:8888/callback?code=abc&state={state}'))
    assert env.spotify.auth_calls == [(f'http://127.0.0.1:8888/callback?code=abc&state={state}', state)]
    assert 'Spotify подключён' in env.session.of(SendMessage)[-1].text
    assert env.session.of(DeleteMessage)


async def test_access_denied_for_other_users(env):
    await feed(env, message_update('/tracks', user_id=999))
    to_admin, to_user = env.session.of(SendMessage)
    assert to_admin.chat_id == ADMIN_ID and '999' in to_admin.text
    assert 'приватный' in to_user.text
    assert 'запрос на доступ' in to_user.text

    env.session.requests.clear()
    await feed(env, callback_update(TrackCallback(track_id='track01').pack(), user_id=999))
    (answer,) = env.session.of(AnswerCallbackQuery)
    assert answer.show_alert
    assert not env.downloader.calls


# --- Импорт выгрузки (режим без Spotify API) ---

LIKED_CSV = """Track URI,Track Name,Artist Name(s),Album Name,Track Duration (ms),Added At
spotify:track:0DiWol3AO6WpXZgp0goxAV,One More Time,Daft Punk,Discovery,320357,2021-01-05T10:00:00Z
spotify:track:69kOkLUCkxIZYexIgSG8rq,Get Lucky,"Daft Punk,Pharrell Williams",Random Access Memories,369626,2023-07-01T08:30:00Z
spotify:track:2KH16WveTQWT6KOG9Rg6e2,Harder Better Faster Stronger,Daft Punk,Discovery,224693,2022-03-03T00:00:00Z
""".encode()


async def upload(env, data, filename='liked.csv', caption=None):
    env.session.file_content = data
    await feed(env, document_update(filename, len(data), caption=caption))


async def test_no_api_requires_import(env_no_api):
    env = env_no_api
    await feed(env, message_update('/tracks'))
    text = env.session.of(SendMessage)[-1].text
    assert '/import' in text and '/login' not in text

    await feed(env, message_update('/login'))
    assert 'Ключи Spotify API не заданы' in env.session.of(SendMessage)[-1].text

    await feed(env, message_update('/import'))
    assert 'exportify.app' in env.session.of(SendMessage)[-1].text


async def test_import_then_browse_download_and_forget(env_no_api):
    env = env_no_api
    await upload(env, LIKED_CSV)

    assert env.session.of(GetFile)
    reply = env.session.of(SendMessage)[-1].text
    assert 'Загружено треков: 3' in reply
    assert 'по дате добавления' in reply

    await feed(env, message_update('/tracks'))
    sent = env.session.of(SendMessage)[-1]
    assert 'Любимые треки</b>: 3' in sent.text
    buttons = [row[0].text for row in sent.reply_markup.inline_keyboard[:-1]]
    assert buttons == [
        'Daft Punk, Pharrell Williams — Get Lucky',
        'Daft Punk — Harder Better Faster Stronger',
        'Daft Punk — One More Time',
    ]

    track_button = sent.reply_markup.inline_keyboard[0][0]
    await feed(env, callback_update(track_button.callback_data))
    assert env.downloader.calls == ['69kOkLUCkxIZYexIgSG8rq']
    assert env.session.of(SendAudio)[-1].title == 'Get Lucky'

    env.session.requests.clear()
    await feed(env, message_update('/last 2'))
    # Get Lucky уже отправлялся — уходит по сохранённому file_id
    assert [a.title or a.audio for a in env.session.of(SendAudio)] == ['Harder Better Faster Stronger', 'FILE-Get Lucky']

    await feed(env, message_update('/forget'))
    assert 'Выгрузка удалена' in env.session.of(SendMessage)[-1].text
    await feed(env, message_update('/tracks'))
    assert '/import' in env.session.of(SendMessage)[-1].text


async def test_new_import_replaces_previous(env_no_api):
    env = env_no_api
    await upload(env, LIKED_CSV)
    await upload(env, b'Track Name,Artist Name(s)\nSolo,Someone\n', filename='other.csv')
    assert 'нет дат добавления' in env.session.of(SendMessage)[-1].text
    assert env.storage.library_size(USER_ID) == 1


async def test_import_with_command_caption(env_no_api):
    await upload(env_no_api, LIKED_CSV, caption='/import')
    assert 'Загружено треков: 3' in env_no_api.session.of(SendMessage)[-1].text


async def test_bad_and_too_large_files(env_no_api):
    env = env_no_api
    await upload(env, b'just some text', filename='notes.txt')
    assert 'не нашёл колонок' in env.session.of(SendMessage)[-1].text
    assert env.storage.library_size(USER_ID) == 0

    env.session.requests.clear()
    await feed(env, document_update('huge.zip', 30 * 1024 * 1024))
    assert 'больше 20 МБ' in env.session.of(SendMessage)[-1].text
    assert not env.session.of(GetFile)


async def test_import_takes_priority_over_spotify_until_login(env):
    await upload(env, LIKED_CSV)
    await feed(env, message_update('/tracks'))
    assert 'Любимые треки</b>: 3' in env.session.of(SendMessage)[-1].text

    await feed(env, message_update('/help'))
    assert 'загруженная выгрузка: 3' in env.session.of(SendMessage)[-1].text

    await feed(env, message_update('/login'))
    state = env.session.of(SendMessage)[-1].reply_markup.inline_keyboard[0][0].url.split('state=')[1]
    await feed(env, message_update(f'http://127.0.0.1:8888/callback?code=abc&state={state}'))
    assert 'выгрузка удалена' in env.session.of(SendMessage)[-1].text

    await feed(env, message_update('/tracks'))
    assert 'Любимые треки</b>: 25' in env.session.of(SendMessage)[-1].text


# --- Поиск по тексту ---

async def test_text_query_sends_track(env_no_api):
    env = env_no_api
    await feed(env, message_update('Daft Punk - Get Lucky'))

    (track,) = env.downloader.tracks
    assert (track.artists, track.title, track.duration_ms) == (('Daft Punk',), 'Get Lucky', 0)
    (audio,) = env.session.of(SendAudio)
    assert (audio.title, audio.performer) == ('Get Lucky', 'Daft Punk')
    # статус «Ищу и скачиваю» удалён после отправки
    assert 'Daft Punk — Get Lucky' in env.session.of(SendMessage)[0].text
    assert env.session.of(DeleteMessage)

    # повторный запрос — из кэша, без скачивания
    await feed(env, message_update('Daft Punk - Get Lucky'))
    assert len(env.downloader.calls) == 1
    assert env.session.of(SendAudio)[-1].audio == 'FILE-Get Lucky'


async def test_text_query_several_lines(env):
    await feed(env, message_update('Daft Punk - One More Time\n\nКороль и Шут — Кукла колдуна'))
    assert [t.title for t in env.downloader.tracks] == ['One More Time', 'Кукла колдуна']
    assert len(env.session.of(SendAudio)) == 2
    assert 'Готово: 2 из 2' in env.session.of(SendMessage)[-1].text


async def test_text_query_not_found(env_no_api):
    env = env_no_api
    env.downloader.missing = {track_from_query('nothing here').id}
    await feed(env, message_update('nothing here'))
    assert not env.session.of(SendAudio)
    assert 'не нашёл подходящий трек' in env.session.of(EditMessageText)[-1].text


async def test_links_and_unknown_commands_are_not_searched(env_no_api):
    env = env_no_api
    await feed(env, message_update('https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV'))
    assert 'Ссылки я не открываю' in env.session.of(SendMessage)[-1].text
    await feed(env, message_update('/whatever'))
    assert 'Не понял' in env.session.of(SendMessage)[-1].text
    assert not env.downloader.calls


# --- Админка ---

def texts(env, method=SendMessage):
    return [m.text for m in env.session.of(method)]


async def test_admin_panel_is_only_for_admin(env):
    await feed(env, message_update('/admin'))
    assert 'Не понял' in texts(env)[-1]

    await feed(env, message_update('/admin', user_id=ADMIN_ID))
    panel = env.session.of(SendMessage)[-1]
    assert '🎚 Качество mp3: <b>192 kbps</b>' in panel.text
    assert panel.reply_markup.inline_keyboard[0][0].text == '🎚 Качество mp3: 192 kbps'

    await feed(env, message_update('/help', user_id=ADMIN_ID))
    assert '/admin' in texts(env)[-1]
    await feed(env, message_update('/help'))
    assert '/admin' not in texts(env)[-1]


async def test_admin_changes_quality(env):
    env.storage.save_tracks(TRACKS)
    option = AdminCallback(action='option', key='audio_quality').pack()
    await feed(env, callback_update(option, user_id=ADMIN_ID))
    choices = env.session.of(EditMessageText)[-1].reply_markup.inline_keyboard[0]
    assert [b.text for b in choices] == ['128 kbps', '✓ 192 kbps', '256 kbps', '320 kbps']

    # обычный пользователь не может нажать кнопку админки
    await feed(env, callback_update(AdminCallback(action='set', key='audio_quality', value='128').pack()))
    assert env.runtime.audio_quality == 192

    await feed(env, callback_update(choices[3].callback_data, user_id=ADMIN_ID))
    assert env.runtime.audio_quality == 320
    assert '320 kbps' in env.session.of(AnswerCallbackQuery)[-1].text
    assert '<b>320 kbps</b>' in texts(env, EditMessageText)[-1]

    # треки качаются в новом качестве, кэш свой для каждого битрейта
    await feed(env, callback_update(TrackCallback(track_id='track03').pack()))
    assert env.downloader.qualities == [320]
    assert env.storage.get_file_id('track03', 320)
    env.runtime.set('audio_quality', '192')
    await feed(env, callback_update(TrackCallback(track_id='track03').pack()))
    assert env.downloader.qualities == [320, 192]


async def test_access_request_flow(env):
    stranger = 999
    await feed(env, message_update('Daft Punk - Get Lucky', user_id=stranger))
    request = env.session.of(SendMessage)[0]
    assert request.chat_id == ADMIN_ID
    buttons = request.reply_markup.inline_keyboard[0]
    assert [b.text for b in buttons] == ['✅ Разрешить', '🚫 Отклонить']
    assert not env.downloader.calls

    # повторное сообщение не дёргает админа ещё раз
    env.session.requests.clear()
    await feed(env, message_update('/start', user_id=stranger))
    (reply,) = env.session.of(SendMessage)
    assert reply.chat_id == stranger and 'уже отправлен' in reply.text

    env.session.requests.clear()
    await feed(env, callback_update(buttons[0].callback_data, user_id=ADMIN_ID))
    assert env.storage.is_allowed_user(stranger)
    assert 'Доступ открыт' in texts(env, EditMessageText)[-1]
    (granted,) = env.session.of(SendMessage)
    assert granted.chat_id == stranger and 'открыл вам доступ' in granted.text

    await feed(env, message_update('/tracks', user_id=stranger))
    assert 'Любимые треки</b>: 25' in texts(env)[-1]


async def test_access_request_decline(env):
    await feed(env, message_update('hi', user_id=999))
    await feed(env, callback_update(AccessRequestCallback(allow=False, user_id=999).pack(), user_id=ADMIN_ID))
    assert not env.storage.is_allowed_user(999)
    assert 'отклонён' in texts(env, EditMessageText)[-1]


async def test_allow_and_deny_commands(env):
    await feed(env, message_update('/allow 555, 666', user_id=ADMIN_ID))
    assert texts(env)[-1].count('доступ открыт') == 2
    assert env.storage.is_allowed_user(555) and env.storage.is_allowed_user(666)

    await feed(env, message_update('/allow abc', user_id=ADMIN_ID))
    assert 'Использование' in texts(env)[-1]

    await feed(env, message_update(f'/deny 555 {USER_ID} {ADMIN_ID} 777', user_id=ADMIN_ID))
    reply = texts(env)[-1]
    assert '555</code> — доступ закрыт' in reply
    assert 'задан в .env' in reply
    assert 'админ' in reply
    assert 'доступа и так не было' in reply
    assert not env.storage.is_allowed_user(555)

    # обычный пользователь не может выдавать доступ
    await feed(env, message_update('/allow 888'))
    assert not env.storage.is_allowed_user(888)


async def test_users_screen_and_picker(env):
    await feed(env, message_update('/allow 666', user_id=ADMIN_ID))
    await feed(env, callback_update(AdminCallback(action='users').pack(), user_id=ADMIN_ID))
    screen = env.session.of(EditMessageText)[-1]
    assert f'👑 <code>{ADMIN_ID}</code>' in screen.text
    assert f'🔒 <code>{USER_ID}</code>' in screen.text
    remove, add, back = (row[0] for row in screen.reply_markup.inline_keyboard)
    assert remove.text == '❌ 666' and add.text == '➕ Добавить'

    await feed(env, callback_update(remove.callback_data, user_id=ADMIN_ID))
    assert not env.storage.is_allowed_user(666)

    await feed(env, callback_update(add.callback_data, user_id=ADMIN_ID))
    picker = env.session.of(SendMessage)[-1].reply_markup
    assert isinstance(picker, ReplyKeyboardMarkup)
    assert picker.keyboard[0][0].request_users.user_is_bot is False

    shared = Update(update_id=next(_ids), message=Message(
        message_id=next(_ids), date=datetime.now(), chat=Chat(id=ADMIN_ID, type='private'),
        from_user=user(ADMIN_ID),
        users_shared=UsersShared(request_id=1, users=[
            SharedUser(user_id=777, first_name='Ann', last_name='Lee'),
            SharedUser(user_id=USER_ID, first_name='Already'),
        ]),
    ))
    await feed(env, shared)
    reply = env.session.of(SendMessage)[-1]
    assert 'Ann Lee (<code>777</code>) — доступ открыт' in reply.text
    assert 'уже есть доступ' in reply.text
    assert isinstance(reply.reply_markup, ReplyKeyboardRemove)
    assert [u.name for u in env.storage.allowed_users()] == ['Ann Lee']
