"""Singleton database facade backed by the JSON storage adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from threading import Lock
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from valutatrade_hub.core.models import Portfolio, User
    from valutatrade_hub.core.storage import JsonStorage


class DatabaseManager:
    """Provide one configured JSON storage facade for the application."""

    _instance: "DatabaseManager | None" = None
    _instance_lock = Lock()

    def __new__(cls, storage: JsonStorage | None = None) -> "DatabaseManager":
        # __new__ provides one instance without constructing it during imports.
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, storage: JsonStorage | None = None) -> None:
        with self._instance_lock:
            if getattr(self, "_initialized", False):
                return
            if storage is None:
                from valutatrade_hub.core.storage import JsonStorage
                from valutatrade_hub.infra.settings import SettingsLoader

                storage = JsonStorage(SettingsLoader().get("data_dir", "data"))
            self._storage = storage
            self._initialized = True

    @property
    def storage(self) -> JsonStorage:
        """Return the underlying storage adapter for supported integrations."""
        return self._storage

    @contextmanager
    def transaction(self) -> Iterator["DatabaseManager"]:
        """Serialize a read-modify-write operation across this process."""
        with self._storage.transaction():
            yield self

    def load_users(self) -> list[User]:
        return self._storage.load_users()

    def save_users(self, users: Sequence[User]) -> None:
        self._storage.save_users(users)

    def load_portfolios(
        self,
        users_by_id: Mapping[int, User] | None = None,
    ) -> list[Portfolio]:
        return self._storage.load_portfolios(users_by_id)

    def save_portfolios(self, portfolios: Sequence[Portfolio]) -> None:
        self._storage.save_portfolios(portfolios)

    def load_rates(self) -> dict[str, Any]:
        return self._storage.load_rates()

    def save_rates(self, rates: Mapping[str, Any]) -> None:
        self._storage.save_rates(rates)
