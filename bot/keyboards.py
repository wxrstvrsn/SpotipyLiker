from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .spotify import Track

PAGE_SIZE = 10
MAX_BUTTON_TEXT = 60


class PageCallback(CallbackData, prefix='page'):
    page: int


class TrackCallback(CallbackData, prefix='track'):
    track_id: str


def total_pages(total_tracks: int) -> int:
    return max(1, -(-total_tracks // PAGE_SIZE))


def _shorten(text: str, limit: int = MAX_BUTTON_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def tracks_keyboard(tracks: list[Track], page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for track in tracks:
        builder.row(InlineKeyboardButton(
            text=_shorten(track.display_name),
            callback_data=TrackCallback(track_id=track.id).pack(),
        ))

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='◀️', callback_data=PageCallback(page=page - 1).pack()))
    # Кнопка с номером страницы обновляет текущую страницу
    nav.append(InlineKeyboardButton(text=f'🔄 {page + 1}/{pages}', callback_data=PageCallback(page=page).pack()))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text='▶️', callback_data=PageCallback(page=page + 1).pack()))
    builder.row(*nav)
    return builder.as_markup()
