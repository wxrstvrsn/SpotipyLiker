"""Настройки, которые админ меняет прямо в боте (/admin), и проверка доступа."""

from collections.abc import Callable
from dataclasses import dataclass

from .config import Settings
from .storage import AllowedUser, Storage


@dataclass(frozen=True)
class Option:
    """Настройка из меню /admin. Новая настройка = новая запись в OPTIONS + чтение через RuntimeSettings."""

    key: str
    title: str
    choices: tuple[str, ...]
    # Значение по умолчанию, пока админ ничего не выбрал (обычно из .env)
    default: Callable[[Settings], str]
    unit: str = ''
    hint: str = ''

    def label(self, value: str) -> str:
        return f'{value} {self.unit}'.strip()


OPTIONS: dict[str, Option] = {option.key: option for option in (
    Option(
        key='audio_quality',
        title='🎚 Качество mp3',
        choices=('128', '192', '256', '320'),
        default=lambda settings: str(settings.audio_quality),
        unit='kbps',
        hint='Уже отправленные треки перекачаются в новом качестве при следующем запросе.',
    ),
)}


class RuntimeSettings:
    def __init__(self, settings: Settings, db: Storage):
        self._settings = settings
        self._db = db

    def get(self, key: str) -> str:
        option = OPTIONS[key]
        value = self._db.get_setting(key)
        return value if value in option.choices else option.default(self._settings)

    def set(self, key: str, value: str) -> None:
        if value not in OPTIONS[key].choices:
            raise ValueError(f'{key}: недопустимое значение {value!r}')
        self._db.set_setting(key, value)

    @property
    def audio_quality(self) -> int:
        return int(self.get('audio_quality'))


class AccessControl:
    """Кто может пользоваться ботом.

    Админы (ADMIN_USER_IDS) и пользователи из ALLOWED_USER_IDS заданы в .env и из бота не
    удаляются. Остальных админ добавляет и убирает в /admin. Если не задан никто — бот открыт.
    """

    def __init__(self, settings: Settings, db: Storage):
        self._settings = settings
        self._db = db

    @property
    def admins(self) -> frozenset[int]:
        return self._settings.admin_user_ids

    @property
    def env_users(self) -> frozenset[int]:
        return self._settings.allowed_user_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins

    def is_open(self) -> bool:
        return not (self.admins or self.env_users or self._db.allowed_users())

    def is_allowed(self, user_id: int) -> bool:
        return (
            self.is_admin(user_id)
            or user_id in self.env_users
            or self._db.is_allowed_user(user_id)
            or self.is_open()
        )

    def managed_users(self) -> list[AllowedUser]:
        """Пользователи, добавленные через бота (их можно убрать из /admin)."""
        return self._db.allowed_users()

    def allow(self, user_id: int, name: str | None, added_by: int | None) -> bool:
        return self._db.add_allowed_user(user_id, name, added_by)

    def revoke(self, user_id: int) -> bool:
        return self._db.remove_allowed_user(user_id)
