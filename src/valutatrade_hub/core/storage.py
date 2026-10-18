"""JSON persistence for users, portfolios, and cached exchange rates."""

import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, Iterator

from valutatrade_hub.core.models import Portfolio, User


class JsonStorage:
    """Read and write the application's three JSON data files."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._lock = RLock()
        configured_dir = data_dir or os.environ.get("VALUTATRADE_DATA_DIR")
        if configured_dir is None:
            from valutatrade_hub.infra.settings import SettingsLoader

            configured_dir = SettingsLoader().get("data_dir", "data")
        self.data_dir = Path(configured_dir)
        self.users_path = self.data_dir / "users.json"
        self.portfolios_path = self.data_dir / "portfolios.json"
        self.rates_path = self.data_dir / "rates.json"
        self.initialize()

    def initialize(self) -> None:
        """Create the data directory and empty JSON files when needed."""
        with self.transaction():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for path, default in (
                (self.users_path, []),
                (self.portfolios_path, []),
                (self.rates_path, {}),
            ):
                if not path.exists():
                    self._write_json(path, default)

    @contextmanager
    def transaction(self) -> Iterator["JsonStorage"]:
        """Serialize read-modify-write transactions within this process."""
        with self._lock:
            yield self

    def load_users(self) -> list[User]:
        """Load all persisted users."""
        with self._lock:
            data = self._read_json(self.users_path, list)
        return [User.from_dict(record) for record in data]

    def save_users(self, users: Sequence[User]) -> None:
        """Persist all users, including only salted password hashes."""
        with self._lock:
            self._write_json(self.users_path, [user.to_dict() for user in users])

    def load_portfolios(
        self,
        users_by_id: Mapping[int, User] | None = None,
    ) -> list[Portfolio]:
        """Load portfolios and attach users when they are available."""
        with self._lock:
            data = self._read_json(self.portfolios_path, list)
        users = users_by_id or {}
        return [
            Portfolio.from_dict(record, user=users.get(record.get("user_id")))
            for record in data
        ]

    def save_portfolios(self, portfolios: Sequence[Portfolio]) -> None:
        """Persist portfolio and wallet balances."""
        with self._lock:
            self._write_json(
                self.portfolios_path,
                [portfolio.to_dict() for portfolio in portfolios],
            )

    def load_rates(self) -> dict[str, Any]:
        """Load the cached pair rates and refresh metadata."""
        with self._lock:
            return self._read_json(self.rates_path, dict)

    def save_rates(self, rates: Mapping[str, Any]) -> None:
        """Persist cached rates and refresh metadata."""
        with self._lock:
            self._write_json(self.rates_path, dict(rates))

    @staticmethod
    def _read_json(path: Path, expected_type: type) -> Any:
        try:
            with path.open(encoding="utf-8") as data_file:
                data = json.load(data_file)
        except json.JSONDecodeError as error:
            raise ValueError(f"Некорректный JSON в файле {path}.") from error
        if not isinstance(data, expected_type):
            raise ValueError(f"Некорректный формат данных в файле {path}.")
        return data

    def _write_json(self, path: Path, data: Any) -> None:
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.data_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(data, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
            temporary_path.replace(path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
