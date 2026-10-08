"""Сквозные тесты обработчиков: апдейты прогоняются через Dispatcher, Telegram API подменён."""

import asyncio
import itertools
from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    SendAudio,
    SendMessage,
)
from aiogram.types import Audio, CallbackQuery, Chat, Message, Update, User

from bot.__main__ import build_dispatcher
from bot.config import Settings
from bot.downloader import DownloadResult, TrackNotFoundError
from bot.keyboards import PageCallback, TrackCallback
from bot.sender import TrackSender
from bot.spotify import NotAuthorizedError
from bot.storage import Storage

from .conftest import make_track

USER_ID = 1
_ids = itertools.count(1)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
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
        yield b''

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

    def download(self, track):
        self.calls.append(track.id)
        if track.id in self.missing:
            raise TrackNotFoundError(track.display_name)
        path = self.tmp_path / f'{track.id}.mp3'
        path.write_bytes(b'ID3 fake mp3')
        return DownloadResult(path=path, duration=track.duration_ms // 1000,
                              source_url='https://sc/x', thumbnail=b'jpeg')


TRACKS = [make_track(id=f'track{i:02d}', title=f'Song {i}') for i in range(25)]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        bot_token='42:TEST', spotify_client_id='id', spotify_client_secret='secret',
        spotify_redirect_uri='http://127.0.0.1:8888/callback', allowed_user_ids=frozenset({USER_ID}),
        data_dir=tmp_path, last_tracks_default=5, last_tracks_max=50,
        search_sources=('soundcloud',), audio_quality=192, max_concurrent_downloads=2,
    )


@pytest.fixture
def env(tmp_path, settings):
    storage = Storage(tmp_path / 'db.sqlite3')
    spotify = FakeSpotify(TRACKS)
    downloader = FakeDownloader(tmp_path)
    sender = TrackSender(downloader, storage, settings.max_concurrent_downloads)
    session = FakeSession()
    bot = Bot('42:TEST', session=session, default=DefaultBotProperties(parse_mode='HTML'))
    dp = build_dispatcher(settings, spotify, storage, sender)
    env = type('Env', (), dict(storage=storage, spotify=spotify, downloader=downloader, sender=sender,
                               session=session, bot=bot, dp=dp, tmp_path=tmp_path))
    yield env
    # роутер модульный: отвязываем его, чтобы следующий тест мог собрать новый Dispatcher
    dp.sub_routers.clear()
    from bot.handlers import router
    router._parent_router = None
    storage.close()


def user(user_id=USER_ID):
    return User(id=user_id, is_bot=False, first_name='Test')


def message_update(text, user_id=USER_ID):
    return Update(update_id=next(_ids), message=Message(
        message_id=next(_ids), date=datetime.now(), chat=Chat(id=user_id, type='private'),
        from_user=user(user_id), text=text,
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
    assert env.storage.get_file_id('track03') == 'FILE-Song 3'
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
    (sent,) = env.session.of(SendMessage)
    assert 'приватный' in sent.text
    assert '999' in sent.text

    env.session.requests.clear()
    await feed(env, callback_update(TrackCallback(track_id='track01').pack(), user_id=999))
    (answer,) = env.session.of(AnswerCallbackQuery)
    assert answer.show_alert
    assert not env.downloader.calls
