"""HTTP clients for CoinGecko and ExchangeRate-API."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.parser_service.config import ParserServiceConfig


@dataclass(frozen=True)
class RateSample:
    """One validated currency-to-USD measurement from a provider."""

    from_currency: str
    to_currency: str
    rate: float
    timestamp: datetime
    source: str
    meta: dict[str, object]


@dataclass(frozen=True)
class _ApiResponse:
    payload: dict[str, object]
    request_ms: int
    status_code: int
    etag: str | None


class _JsonApiClient:
    """Common timed JSON request and response validation."""

    def __init__(self, config: ParserServiceConfig) -> None:
        self.config = config

    def _request_json(
        self,
        url: str,
        provider: str,
        headers: Mapping[str, str] | None = None,
    ) -> _ApiResponse:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "ValutaTradeHub/0.1",
        }
        request_headers.update(headers or {})
        request = Request(url, headers=request_headers)
        started_at = time.perf_counter()
        try:
            with urlopen(
                request, timeout=self.config.request_timeout_seconds
            ) as response:
                body = response.read()
                status_code = int(getattr(response, "status", 200))
                etag = response.headers.get("ETag")
        except HTTPError as error:
            if error.code == 429:
                reason = "лимит запросов (HTTP 429); повторите позже"
            else:
                reason = f"HTTP {error.code}"
            raise ApiRequestError(f"{provider}: {reason}") from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ApiRequestError(f"{provider}: {reason}") from error

        request_ms = round((time.perf_counter() - started_at) * 1000)
        if not 200 <= status_code < 300:
            raise ApiRequestError(f"{provider}: HTTP {status_code}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiRequestError(
                f"{provider}: ответ не является корректным JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ApiRequestError(f"{provider}: ожидался JSON-объект")
        return _ApiResponse(payload, request_ms, status_code, etag)


class CoinGeckoClient(_JsonApiClient):
    """Fetch crypto-to-USD prices in one CoinGecko simple/price request."""

    PUBLIC_URL = "https://api.coingecko.com/api/v3/simple/price"
    PRO_URL = "https://pro-api.coingecko.com/api/v3/simple/price"

    def fetch_usd_rates(self) -> list[RateSample]:
        """Fetch configured coin IDs and return validated measurements."""
        coin_ids = self.config.crypto_ids
        if not coin_ids:
            return []
        query = urlencode(
            {
                "ids": ",".join(sorted(coin_ids.values())),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            }
        )
        headers = (
            {"x-cg-pro-api-key": self.config.coingecko_api_key}
            if self.config.coingecko_api_key
            else {}
        )
        base_url = self.PRO_URL if self.config.coingecko_api_key else self.PUBLIC_URL
        response = self._request_json(
            f"{base_url}?{query}",
            "CoinGecko",
            headers,
        )

        samples: list[RateSample] = []
        for code, coin_id in sorted(coin_ids.items()):
            coin_data = response.payload.get(coin_id)
            if not isinstance(coin_data, Mapping):
                raise ApiRequestError(f"CoinGecko: нет данных для {code} ({coin_id})")
            rate = _positive_rate(coin_data.get("usd"), "CoinGecko", code)
            timestamp = _parse_timestamp(coin_data.get("last_updated_at"))
            samples.append(
                RateSample(
                    from_currency=code,
                    to_currency="USD",
                    rate=rate,
                    timestamp=timestamp,
                    source="CoinGecko",
                    meta=_response_meta(response, raw_id=coin_id),
                )
            )
        return samples


class ExchangeRateApiClient(_JsonApiClient):
    """Fetch fiat rates relative to USD from ExchangeRate-API."""

    BASE_URL = "https://v6.exchangerate-api.com/v6"

    def fetch_usd_rates(self) -> list[RateSample]:
        """Return fiat-to-USD samples; the provider's USD rates are inverted."""
        if not self.config.exchange_rate_api_key:
            raise ApiRequestError(
                "ExchangeRate-API: задайте EXCHANGERATE_API_KEY для обновления фиата"
            )
        encoded_key = quote(self.config.exchange_rate_api_key, safe="")
        response = self._request_json(
            f"{self.BASE_URL}/{encoded_key}/latest/USD",
            "ExchangeRate-API",
        )
        payload = response.payload
        if payload.get("result") != "success":
            error_type = payload.get("error-type", "unknown-error")
            raise ApiRequestError(f"ExchangeRate-API: {error_type}")
        if payload.get("base_code", "USD") != "USD":
            raise ApiRequestError("ExchangeRate-API: ответ получен не для базы USD")
        rates = payload.get("conversion_rates", payload.get("rates"))
        if not isinstance(rates, Mapping):
            raise ApiRequestError("ExchangeRate-API: в ответе отсутствуют курсы")
        timestamp = _parse_timestamp(
            payload.get("time_last_update_unix"),
            payload.get("time_last_update_utc"),
        )

        samples: list[RateSample] = []
        for code in self.config.fiat_codes:
            units_per_usd = _positive_rate(
                rates.get(code),
                "ExchangeRate-API",
                code,
            )
            usd_per_unit = _positive_rate(
                1.0 / units_per_usd,
                "ExchangeRate-API",
                code,
            )
            samples.append(
                RateSample(
                    from_currency=code,
                    to_currency="USD",
                    rate=usd_per_unit,
                    timestamp=timestamp,
                    source="ExchangeRate-API",
                    meta=_response_meta(response),
                )
            )
        return samples


def _response_meta(
    response: _ApiResponse,
    *,
    raw_id: str | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {
        "request_ms": response.request_ms,
        "status_code": response.status_code,
    }
    if raw_id is not None:
        meta["raw_id"] = raw_id
    if response.etag:
        meta["etag"] = response.etag
    return meta


def _positive_rate(value: object, provider: str, currency_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiRequestError(f"{provider}: некорректный курс для {currency_code}")
    try:
        rate = float(value)
    except (OverflowError, ValueError) as error:
        raise ApiRequestError(
            f"{provider}: некорректный курс для {currency_code}"
        ) from error
    if not isfinite(rate) or rate <= 0:
        raise ApiRequestError(f"{provider}: некорректный курс для {currency_code}")
    return rate


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
