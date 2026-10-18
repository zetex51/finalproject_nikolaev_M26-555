"""Singleton configuration loader for ValutaTrade Hub."""

import math
import os
import tomllib
from pathlib import Path
from threading import Lock
from typing import Any

_MISSING = object()


class SettingsLoader:
    """Load project settings once and expose them through a small key API."""

    _instance: "SettingsLoader | None" = None
    _instance_lock = Lock()

    _defaults: dict[str, Any] = {
        "data_dir": "data",
        "rates_ttl_seconds": 300,
        "rates_timeout_seconds": 10,
        "default_base_currency": "USD",
        "log_file": "logs/actions.log",
        "log_format": "%(levelname)s %(asctime)s %(message)s",
        "log_level": "INFO",
        "log_max_bytes": 1_048_576,
        "log_backup_count": 3,
    }

    def __new__(cls, config_path: str | Path | None = None) -> "SettingsLoader":
        # A locked __new__ keeps construction readable and thread-safe.
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: str | Path | None = None) -> None:
        with self._instance_lock:
            if getattr(self, "_initialized", False):
                return
            default_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
            self.config_path = Path(config_path) if config_path else default_path
            self._values: dict[str, Any] = {}
            self.reload()
            self._initialized = True

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Return a setting, or the provided default for an absent key."""
        if not isinstance(key, str):
            raise TypeError("Ключ настройки должен быть строкой.")
        normalized_key = key.lower()
        if normalized_key in self._values:
            return self._values[normalized_key]
        if default is not _MISSING:
            return default
        raise KeyError(f"Настройка '{key}' не найдена.")

    def reload(self) -> None:
        """Reload TOML settings and apply supported environment overrides."""
        values = self._defaults.copy()
        if self.config_path.is_file():
            with self.config_path.open("rb") as config_file:
                config = tomllib.load(config_file)
            tool_settings = config.get("tool", {}).get("valutatrade", {})
            if isinstance(tool_settings, dict):
                values.update(tool_settings)

        environment_values = {
            "data_dir": os.environ.get("VALUTATRADE_DATA_DIR"),
            "log_file": os.environ.get("VALUTATRADE_LOG_FILE"),
            "log_level": os.environ.get("VALUTATRADE_LOG_LEVEL"),
        }
        ttl = os.environ.get("VALUTATRADE_RATES_TTL_SECONDS")
        if ttl is not None:
            try:
                values["rates_ttl_seconds"] = int(ttl)
            except ValueError as error:
                raise ValueError(
                    "VALUTATRADE_RATES_TTL_SECONDS должен быть целым числом."
                ) from error
        timeout = os.environ.get("VALUTATRADE_RATES_TIMEOUT_SECONDS")
        if timeout is not None:
            try:
                values["rates_timeout_seconds"] = float(timeout)
            except ValueError as error:
                raise ValueError(
                    "VALUTATRADE_RATES_TIMEOUT_SECONDS должен быть числом."
                ) from error
        values.update(
            {key: value for key, value in environment_values.items() if value}
        )

        if (
            not isinstance(values.get("rates_ttl_seconds"), int)
            or values["rates_ttl_seconds"] <= 0
        ):
            raise ValueError(
                "rates_ttl_seconds должен быть положительным целым числом."
            )
        timeout = values.get("rates_timeout_seconds")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("rates_timeout_seconds должен быть положительным числом.")
        if not isinstance(values.get("default_base_currency"), str):
            raise ValueError("default_base_currency должен быть строкой.")
        self._values = values
