"""Configuration for external rate providers and update scheduling."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    FiatCurrency,
    get_currency,
)
from valutatrade_hub.infra.settings import SettingsLoader


def _environment_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class ParserConfig:
    """All provider, currency, timeout, and storage settings for the parser."""

    COINGECKO_URL: ClassVar[str] = "https://api.coingecko.com/api/v3/simple/price"
    COINGECKO_PRO_URL: ClassVar[str] = (
        "https://pro-api.coingecko.com/api/v3/simple/price"
    )
    EXCHANGERATE_API_URL: ClassVar[str] = (
        "https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_currency}"
    )

    BASE_FIAT_CURRENCY: ClassVar[str] = "USD"
    FIAT_CURRENCIES: ClassVar[tuple[str, ...]] = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: ClassVar[tuple[str, ...]] = ("BTC", "ETH", "SOL")
    CRYPTO_ID_MAP: ClassVar[dict[str, str]] = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
    }
    REQUEST_TIMEOUT: ClassVar[float] = 10

    data_dir: Path = Path("data")
    rates_ttl_seconds: int = 300
    request_timeout_seconds: float = REQUEST_TIMEOUT
    update_interval_seconds: int = 240
    rates_file_path: Path | None = None
    history_file_path: Path | None = None
    exchange_rate_api_key: str | None = field(
        default_factory=lambda: _environment_value("EXCHANGERATE_API_KEY"),
        repr=False,
    )
    coingecko_api_key: str | None = field(
        default_factory=lambda: _environment_value("COINGECKO_API_KEY"),
        repr=False,
    )

    @property
    def EXCHANGERATE_API_KEY(self) -> str | None:
        """Environment-provided ExchangeRate-API key (never hard-coded)."""
        return self.exchange_rate_api_key

    @property
    def COINGECKO_API_KEY(self) -> str | None:
        """Optional environment-provided CoinGecko Pro key."""
        return self.coingecko_api_key

    @property
    def RATES_FILE_PATH(self) -> Path:
        """Configured path to the Core Service's current rates snapshot."""
        return self.rates_cache_path

    @property
    def HISTORY_FILE_PATH(self) -> Path:
        """Configured path to the parser's measurement history."""
        return self.history_path

    def __post_init__(self) -> None:
        data_dir = Path(self.data_dir)
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(
            self,
            "rates_file_path",
            Path(self.rates_file_path)
            if self.rates_file_path is not None
            else data_dir / "rates.json",
        )
        object.__setattr__(
            self,
            "history_file_path",
            Path(self.history_file_path)
            if self.history_file_path is not None
            else data_dir / "exchange_rates.json",
        )

    @property
    def rates_cache_path(self) -> Path:
        """Path to the current snapshot consumed by Core Service."""
        return self.rates_file_path or self.data_dir / "rates.json"

    @property
    def history_path(self) -> Path:
        """Path to the parser's historical measurements."""
        return self.history_file_path or self.data_dir / "exchange_rates.json"

    @property
    def fiat_codes(self) -> tuple[str, ...]:
        """Configured fiat codes present in the application's currency registry."""
        return tuple(
            code
            for code in self.FIAT_CURRENCIES
            if code != self.BASE_FIAT_CURRENCY
            and isinstance(get_currency(code), FiatCurrency)
        )

    @property
    def crypto_ids(self) -> dict[str, str]:
        """Configured CoinGecko IDs present in the currency registry."""
        return {
            code: self.CRYPTO_ID_MAP[code]
            for code in self.CRYPTO_CURRENCIES
            if code in self.CRYPTO_ID_MAP
            and isinstance(get_currency(code), CryptoCurrency)
        }

    @classmethod
    def from_settings(
        cls,
        settings: SettingsLoader | None = None,
    ) -> ParserConfig:
        """Build config from project settings and environment-only API secrets."""
        settings = settings or SettingsLoader()
        return cls(
            data_dir=Path(settings.get("data_dir", "data")),
            rates_ttl_seconds=settings.get("rates_ttl_seconds", 300),
            request_timeout_seconds=float(settings.get("rates_timeout_seconds", 10)),
            update_interval_seconds=settings.get(
                "rates_update_interval_seconds",
                240,
            ),
        )


# Keep the earlier public name available for integrations using the first version.
ParserServiceConfig = ParserConfig
