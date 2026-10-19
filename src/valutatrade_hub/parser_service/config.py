"""Configuration for external rate providers and update scheduling."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    FiatCurrency,
    get_currency,
    supported_currency_codes,
)
from valutatrade_hub.infra.settings import SettingsLoader

CRYPTO_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


@dataclass(frozen=True)
class ParserServiceConfig:
    """Resolved paths and safe runtime settings for the parser service."""

    data_dir: Path
    rates_ttl_seconds: int
    request_timeout_seconds: float
    update_interval_seconds: int
    exchange_rate_api_key: str | None = field(default=None, repr=False)
    coingecko_api_key: str | None = field(default=None, repr=False)

    @property
    def history_path(self) -> Path:
        """Path to the parser's append-only historical measurements."""
        return self.data_dir / "exchange_rates.json"

    @property
    def rates_cache_path(self) -> Path:
        """Path to the latest rates consumed by Core Service."""
        return self.data_dir / "rates.json"

    @property
    def fiat_codes(self) -> tuple[str, ...]:
        """Return registered fiat codes that need a USD quote."""
        return tuple(
            code
            for code in supported_currency_codes()
            if code != "USD" and isinstance(get_currency(code), FiatCurrency)
        )

    @property
    def crypto_ids(self) -> dict[str, str]:
        """Return configured CoinGecko IDs present in the currency registry."""
        return {
            code: coin_id
            for code, coin_id in CRYPTO_ID_MAP.items()
            if isinstance(get_currency(code), CryptoCurrency)
        }

    @classmethod
    def from_settings(
        cls,
        settings: SettingsLoader | None = None,
    ) -> "ParserServiceConfig":
        """Build a service configuration without putting secrets in TOML."""
        settings = settings or SettingsLoader()
        return cls(
            data_dir=Path(settings.get("data_dir", "data")),
            rates_ttl_seconds=settings.get("rates_ttl_seconds", 300),
            request_timeout_seconds=float(settings.get("rates_timeout_seconds", 10)),
            update_interval_seconds=settings.get(
                "rates_update_interval_seconds",
                240,
            ),
            exchange_rate_api_key=_environment_value("EXCHANGERATE_API_KEY"),
            coingecko_api_key=_environment_value("COINGECKO_API_KEY"),
        )


def _environment_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
