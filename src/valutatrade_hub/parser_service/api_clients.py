"""HTTP clients for CoinGecko and ExchangeRate-API."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import ClassVar
from urllib.parse import quote

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.parser_service.config import ParserConfig


@dataclass(frozen=True)
class RateSample:
    """One validated currency measurement with provider history metadata."""

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


class BaseApiClient(ABC):
    """Common request handling and the normalized rate-client interface."""

    source: ClassVar[str] = "API"

    def __init__(self, config: ParserConfig) -> None:
        self.config = config
        self._last_samples: tuple[RateSample, ...] = ()

    @abstractmethod
    def fetch_rates(self) -> dict[str, float]:
        """Return validated rates keyed by pair, such as ``BTC_USD``."""

    def fetch_samples(self) -> list[RateSample]:
        """Adapt a basic client implementation to the parser's history model."""
        timestamp = datetime.now(timezone.utc)
        samples: list[RateSample] = []
        for pair, rate in self.fetch_rates().items():
            try:
                from_currency, to_currency = pair.split("_", maxsplit=1)
            except ValueError as error:
                raise ApiRequestError(
                    f"{self.source}: некорректная пара '{pair}'"
                ) from error
            samples.append(
                RateSample(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=rate,
                    timestamp=timestamp,
                    source=self.source,
                    meta={},
                )
            )
        self._last_samples = tuple(samples)
        return samples

    @property
    def last_samples(self) -> tuple[RateSample, ...]:
        """Return detailed samples from the most recent successful request."""
        return self._last_samples

    def _request_json(
        self,
        url: str,
        provider: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _ApiResponse:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "ValutaTradeHub/0.1",
        }
        request_headers.update(headers or {})
        started_at = time.perf_counter()
        try:
            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.exceptions.RequestException as error:
            raise ApiRequestError(
                f"{provider}: ошибка сети или таймаут ({type(error).__name__})"
            ) from error

        request_ms = round((time.perf_counter() - started_at) * 1000)
        status_code = int(response.status_code)
        if status_code == 429:
            raise ApiRequestError(
                f"{provider}: превышен лимит запросов (HTTP 429); повторите позже"
            )
        if status_code in {401, 403}:
            if provider == "CoinGecko":
                hint = "проверьте COINGECKO_API_KEY и доступность API-тарифа"
            elif provider == "ExchangeRate-API":
                hint = "проверьте EXCHANGERATE_API_KEY и права доступа"
            else:
                hint = "проверьте API-ключ и права доступа"
            raise ApiRequestError(
                f"{provider}: доступ отклонён (HTTP {status_code}); {hint}"
            )
        if not 200 <= status_code < 300:
            try:
                response.raise_for_status()
            except requests.exceptions.RequestException as error:
                raise ApiRequestError(f"{provider}: HTTP {status_code}") from error
            raise ApiRequestError(f"{provider}: неожиданный HTTP статус {status_code}")
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            raise ApiRequestError(f"{provider}: HTTP {status_code}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise ApiRequestError(
                f"{provider}: ответ не является корректным JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ApiRequestError(f"{provider}: ожидался JSON-объект")
        return _ApiResponse(
            payload=payload,
            request_ms=request_ms,
            status_code=status_code,
            etag=response.headers.get("ETag"),
        )


class CoinGeckoClient(BaseApiClient):
    """Fetch cryptocurrency-to-base-currency rates from CoinGecko."""

    source = "CoinGecko"

    def fetch_rates(self) -> dict[str, float]:
        """Return rates in the shared ``FROM_TO`` format."""
        return {
            f"{sample.from_currency}_{sample.to_currency}": sample.rate
            for sample in self.fetch_samples()
        }

    def fetch_samples(self) -> list[RateSample]:
        """Fetch configured coin IDs and retain metadata for history records."""
        coin_ids = self.config.crypto_ids
        if not coin_ids:
            self._last_samples = ()
            return []
        params = {
            "ids": ",".join(sorted(coin_ids.values())),
            "vs_currencies": self.config.BASE_FIAT_CURRENCY.lower(),
            "include_last_updated_at": "true",
        }
        api_key = self.config.coingecko_api_key
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        endpoint = (
            self.config.COINGECKO_PRO_URL if api_key else self.config.COINGECKO_URL
        )
        response = self._request_json(
            endpoint,
            self.source,
            params=params,
            headers=headers,
        )

        samples: list[RateSample] = []
        base_currency = self.config.BASE_FIAT_CURRENCY
        for code, coin_id in sorted(coin_ids.items()):
            coin_data = response.payload.get(coin_id)
            if not isinstance(coin_data, Mapping):
                raise ApiRequestError(f"CoinGecko: нет данных для {code} ({coin_id})")
            rate = _positive_rate(
                coin_data.get(base_currency.lower()),
                self.source,
                code,
            )
            samples.append(
                RateSample(
                    from_currency=code,
                    to_currency=base_currency,
                    rate=rate,
                    timestamp=_parse_timestamp(coin_data.get("last_updated_at")),
                    source=self.source,
                    meta=_response_meta(response, raw_id=coin_id),
                )
            )
        self._last_samples = tuple(samples)
        return samples

    def fetch_usd_rates(self) -> list[RateSample]:
        """Compatibility adapter for earlier Parser Service integrations."""
        return self.fetch_samples()


class ExchangeRateApiClient(BaseApiClient):
    """Fetch fiat rates relative to the configured base currency."""

    source = "ExchangeRate-API"

    def fetch_rates(self) -> dict[str, float]:
        """Return rates in the shared ``FROM_TO`` format."""
        return {
            f"{sample.from_currency}_{sample.to_currency}": sample.rate
            for sample in self.fetch_samples()
        }

    def fetch_samples(self) -> list[RateSample]:
        """Fetch fiat data and invert the provider's units-per-base rates."""
        self._last_samples = ()
        api_key = self.config.exchange_rate_api_key
        if not api_key:
            raise ApiRequestError(
                "ExchangeRate-API: задайте EXCHANGERATE_API_KEY для обновления фиата"
            )
        encoded_key = quote(api_key, safe="")
        base_currency = self.config.BASE_FIAT_CURRENCY
        endpoint = self.config.EXCHANGERATE_API_URL.format(
            api_key=encoded_key,
            base_currency=base_currency,
        )
        response = self._request_json(endpoint, self.source)
        payload = response.payload
        if payload.get("result") != "success":
            error_type = payload.get("error-type", "unknown-error")
            raise ApiRequestError(f"ExchangeRate-API: {error_type}")
        if payload.get("base_code", base_currency) != base_currency:
            raise ApiRequestError(
                f"ExchangeRate-API: ответ получен не для базы {base_currency}"
            )
        rates = payload.get("conversion_rates", payload.get("rates"))
        if not isinstance(rates, Mapping):
            raise ApiRequestError("ExchangeRate-API: в ответе отсутствуют курсы")
        timestamp = _parse_timestamp(
            payload.get("time_last_update_unix"),
            payload.get("time_last_update_utc"),
        )

        samples: list[RateSample] = []
        for code in self.config.fiat_codes:
            units_per_base = _positive_rate(rates.get(code), self.source, code)
            base_per_unit = _positive_rate(1.0 / units_per_base, self.source, code)
            samples.append(
                RateSample(
                    from_currency=code,
                    to_currency=base_currency,
                    rate=base_per_unit,
                    timestamp=timestamp,
                    source=self.source,
                    meta=_response_meta(response),
                )
            )
        self._last_samples = tuple(samples)
        return samples

    def fetch_usd_rates(self) -> list[RateSample]:
        """Compatibility adapter for earlier Parser Service integrations."""
        return self.fetch_samples()


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
