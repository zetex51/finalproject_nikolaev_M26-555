"""Fetch and normalize fiat and cryptocurrency exchange rates."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from valutatrade_hub.core.currencies import CryptoCurrency, FiatCurrency, get_currency
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import DEFAULT_EXCHANGE_RATES, validate_amount
from valutatrade_hub.infra.settings import SettingsLoader

CRYPTO_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


@dataclass(frozen=True)
class ParsedRate:
    """A normalized quote produced from one or more rate providers."""

    from_currency: str
    to_currency: str
    rate: float
    updated_at: datetime
    source: str


class ParserService:
    """Get USD-denominated prices and convert them into requested pairs.

    ExchangeRate-API returns units of a fiat currency per USD. The application
    stores rates as USD per one unit of a currency, so fiat values are inverted
    before they are combined with cryptocurrency prices.
    """

    COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
    COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_URL = "https://v6.exchangerate-api.com/v6"

    def __init__(self, settings: SettingsLoader | None = None) -> None:
        self.settings = settings or SettingsLoader()
        self.timeout = float(self.settings.get("rates_timeout_seconds", 10))

    def get_rate(self, from_currency: str, to_currency: str) -> ParsedRate:
        """Fetch a quote for two registered currencies, using USD as pivot."""
        source = get_currency(from_currency)
        target = get_currency(to_currency)
        if source.code == target.code:
            return ParsedRate(
                source.code,
                target.code,
                1.0,
                datetime.now(timezone.utc),
                "Identity",
            )

        requested_currencies = {source.code: source, target.code: target}
        fiat_codes = {
            code
            for code, currency in requested_currencies.items()
            if code != "USD" and isinstance(currency, FiatCurrency)
        }
        crypto_codes = {
            code
            for code, currency in requested_currencies.items()
            if isinstance(currency, CryptoCurrency)
        }

        usd_rates: dict[str, tuple[float, datetime, str]] = {"USD": self._usd_rate()}
        usd_rates.update(self._get_fiat_usd_rates(fiat_codes))
        usd_rates.update(self._get_crypto_usd_rates(crypto_codes))

        try:
            source_rate, source_updated, source_provider = usd_rates[source.code]
            target_rate, target_updated, target_provider = usd_rates[target.code]
        except KeyError as error:
            raise ApiRequestError(
                f"не удалось получить USD-курс для {error.args[0]}"
            ) from error

        rate = validate_amount(source_rate / target_rate)
        providers = dict.fromkeys(
            provider
            for provider in (source_provider, target_provider)
            if provider != "Identity"
        )
        source_name = "+".join(providers) if providers else "Identity"
        timestamps = [
            timestamp
            for timestamp, provider in (
                (source_updated, source_provider),
                (target_updated, target_provider),
            )
            if provider != "Identity"
        ]
        updated_at = min(timestamps) if timestamps else datetime.now(timezone.utc)
        return ParsedRate(source.code, target.code, rate, updated_at, source_name)

    @staticmethod
    def _usd_rate() -> tuple[float, datetime, str]:
        return 1.0, datetime.now(timezone.utc), "Identity"

    def _get_fiat_usd_rates(
        self,
        currency_codes: set[str],
    ) -> dict[str, tuple[float, datetime, str]]:
        if not currency_codes:
            return {}

        api_key = os.environ.get("EXCHANGERATE_API_KEY", "").strip()
        if not api_key:
            # Keep the educational CLI usable before a personal API key is set.
            now = datetime.now(timezone.utc)
            fallback: dict[str, tuple[float, datetime, str]] = {}
            for code in sorted(currency_codes):
                rate = DEFAULT_EXCHANGE_RATES.get(code)
                if rate is None:
                    raise ApiRequestError(
                        f"ExchangeRate-API: задайте EXCHANGERATE_API_KEY "
                        f"для валюты {code}"
                    )
                fallback[code] = (validate_amount(rate), now, "LocalStub")
            return fallback

        encoded_key = quote(api_key, safe="")
        url = f"{self.EXCHANGERATE_URL}/{encoded_key}/latest/USD"
        payload = self._request_json(url, "ExchangeRate-API")
        if payload.get("result") != "success":
            error_type = payload.get("error-type", "unknown-error")
            raise ApiRequestError(f"ExchangeRate-API: {error_type}")
        base_code = payload.get("base_code", "USD")
        if base_code != "USD":
            raise ApiRequestError(
                f"ExchangeRate-API вернул базовую валюту {base_code!r} вместо USD"
            )

        rates = payload.get("conversion_rates", payload.get("rates"))
        if not isinstance(rates, Mapping):
            raise ApiRequestError("ExchangeRate-API: в ответе отсутствуют курсы")
        updated_at = self._parse_timestamp(
            payload.get("time_last_update_unix"),
            payload.get("time_last_update_utc"),
        )

        normalized: dict[str, tuple[float, datetime, str]] = {}
        for code in sorted(currency_codes):
            response_rate = self._positive_rate(
                rates.get(code),
                provider="ExchangeRate-API",
                currency_code=code,
            )
            normalized[code] = (1.0 / response_rate, updated_at, "ExchangeRate-API")
        return normalized

    def _get_crypto_usd_rates(
        self,
        currency_codes: set[str],
    ) -> dict[str, tuple[float, datetime, str]]:
        if not currency_codes:
            return {}

        try:
            coin_ids = {code: CRYPTO_ID_MAP[code] for code in currency_codes}
        except KeyError as error:
            raise ApiRequestError(
                f"CoinGecko: для валюты {error.args[0]} не задан ID монеты"
            ) from error

        query = urlencode(
            {
                "ids": ",".join(sorted(coin_ids.values())),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            }
        )
        api_key = os.environ.get("COINGECKO_API_KEY", "").strip()
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        base_url = self.COINGECKO_PRO_URL if api_key else self.COINGECKO_URL
        payload = self._request_json(f"{base_url}?{query}", "CoinGecko", headers)

        normalized: dict[str, tuple[float, datetime, str]] = {}
        for code, coin_id in coin_ids.items():
            coin_data = payload.get(coin_id)
            if not isinstance(coin_data, Mapping):
                raise ApiRequestError(f"CoinGecko: нет данных для {code} ({coin_id})")
            price = self._positive_rate(
                coin_data.get("usd"),
                provider="CoinGecko",
                currency_code=code,
            )
            updated_at = self._parse_timestamp(coin_data.get("last_updated_at"))
            normalized[code] = (price, updated_at, "CoinGecko")
        return normalized

    def _request_json(
        self,
        url: str,
        provider: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        request_headers = {"Accept": "application/json", "User-Agent": "ValutaTradeHub"}
        request_headers.update(headers or {})
        request = Request(url, headers=request_headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_body = response.read()
        except HTTPError as error:
            if error.code == 429:
                reason = "лимит запросов (HTTP 429); повторите позже"
            else:
                reason = f"HTTP {error.code}"
            raise ApiRequestError(f"{provider}: {reason}") from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ApiRequestError(f"{provider}: {reason}") from error

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiRequestError(
                f"{provider}: ответ не является корректным JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ApiRequestError(f"{provider}: ожидался JSON-объект")
        return payload

    @staticmethod
    def _positive_rate(
        value: object,
        *,
        provider: str,
        currency_code: str,
    ) -> float:
        try:
            rate = validate_amount(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ApiRequestError(
                f"{provider}: некорректный курс для {currency_code}"
            ) from error
        if not isfinite(rate) or rate <= 0:
            raise ApiRequestError(f"{provider}: некорректный курс для {currency_code}")
        return rate

    @staticmethod
    def _parse_timestamp(unix_value: object, text_value: object = None) -> datetime:
        if isinstance(unix_value, (int, float)) and not isinstance(unix_value, bool):
            try:
                return datetime.fromtimestamp(float(unix_value), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
        if isinstance(text_value, str):
            try:
                timestamp = parsedate_to_datetime(text_value)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                return timestamp.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        return datetime.now(timezone.utc)
