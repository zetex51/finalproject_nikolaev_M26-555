"""Coordinate provider requests, history, the Core cache, and update logs."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
    RateSample,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import ExchangeRateStorage

_RATE_PAIR = re.compile(r"^([A-Z0-9]{2,5})_([A-Z0-9]{2,5})$")


@dataclass(frozen=True)
class UpdateReport:
    """Summary of one parser run, including any partial provider failures."""

    history_records_added: int
    history_records_skipped: int
    cache_quotes_updated: int
    elapsed_ms: int
    errors: tuple[str, ...] = ()


class RatesUpdater:
    """Fetch from providers and publish history plus a Core rates snapshot.

    ``clients`` and ``storage`` can be injected for alternate providers or
    storage adapters. If omitted, the configured API clients and JSON database
    are used.
    """

    def __init__(
        self,
        clients: Iterable[BaseApiClient] | None = None,
        storage: Any | None = None,
        *,
        config: ParserConfig | None = None,
        history_storage: ExchangeRateStorage | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config or ParserConfig.from_settings()
        self.clients = tuple(
            clients
            if clients is not None
            else (
                ExchangeRateApiClient(self.config),
                CoinGeckoClient(self.config),
            )
        )
        self.storage = storage if storage is not None else DatabaseManager()
        self.history_storage = (
            history_storage
            if history_storage is not None
            else ExchangeRateStorage(self.config.history_path)
        )
        self.logger = logger if logger is not None else configure_logging()

    def run_update(self) -> UpdateReport:
        """Fetch all clients sequentially, retaining successful provider data."""
        started_at = time.perf_counter()
        self.logger.info("PARSER_UPDATE action=UPDATE_RATES result=START")
        samples: list[RateSample] = []
        errors: list[str] = []

        for client in self.clients:
            provider = self._provider_name(client)
            try:
                rates = client.fetch_rates()
                provider_samples = self._samples_from_rates(client, rates)
            except ApiRequestError as error:
                errors.append(error.reason)
                self.logger.error(
                    "PARSER_PROVIDER provider=%s result=ERROR error_type=%s reason=%s",
                    provider,
                    type(error).__name__,
                    error.reason,
                )
                continue
            except Exception as error:
                reason = f"{provider}: {type(error).__name__}: {error}"
                errors.append(reason)
                self.logger.exception(
                    "PARSER_PROVIDER provider=%s result=ERROR error_type=%s reason=%s",
                    provider,
                    type(error).__name__,
                    error,
                )
                continue

            samples.extend(provider_samples)
            request_ms = max(
                (
                    sample.meta.get("request_ms", 0)
                    for sample in provider_samples
                    if isinstance(sample.meta.get("request_ms", 0), int)
                ),
                default=0,
            )
            self.logger.info(
                "PARSER_PROVIDER provider=%s result=OK records=%d request_ms=%d",
                provider,
                len(provider_samples),
                request_ms,
            )

        if not samples:
            reason = "; ".join(errors) if errors else "провайдеры не вернули курсы"
            self.logger.error(
                "PARSER_UPDATE action=UPDATE_RATES result=ERROR reason=%s",
                reason,
            )
            raise ApiRequestError(f"обновление не выполнено: {reason}")

        records = [self._to_history_record(sample) for sample in samples]
        try:
            previous_ids = {record["id"] for record in self.history_storage.load()}
            added, skipped = self.history_storage.append_many(records)
        except Exception as error:
            self.logger.exception(
                "PARSER_HISTORY result=ERROR error_type=%s reason=%s",
                type(error).__name__,
                error,
            )
            if isinstance(error, ApiRequestError):
                raise
            raise ApiRequestError(
                f"не удалось обновить историю курсов: {error}"
            ) from error
        self._log_history_writes(records, previous_ids)

        try:
            cache_quotes_updated = self._update_core_cache(samples)
        except ApiRequestError:
            raise
        except Exception as error:
            self.logger.exception(
                "PARSER_CACHE result=ERROR error_type=%s reason=%s",
                type(error).__name__,
                error,
            )
            raise ApiRequestError(f"не удалось обновить rates.json: {error}") from error

        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        result = "PARTIAL" if errors else "OK"
        self.logger.info(
            "PARSER_UPDATE action=UPDATE_RATES result=%s samples=%d "
            "history_added=%d history_skipped=%d cache_quotes=%d elapsed_ms=%d",
            result,
            len(samples),
            added,
            skipped,
            cache_quotes_updated,
            elapsed_ms,
        )
        return UpdateReport(
            history_records_added=added,
            history_records_skipped=skipped,
            cache_quotes_updated=cache_quotes_updated,
            elapsed_ms=elapsed_ms,
            errors=tuple(errors),
        )

    def update_rates(self) -> UpdateReport:
        """Compatibility alias for earlier CLI and scheduler integrations."""
        return self.run_update()

    @classmethod
    def _samples_from_rates(
        cls,
        client: BaseApiClient,
        rates: object,
    ) -> list[RateSample]:
        if not isinstance(rates, Mapping):
            raise ApiRequestError(
                f"{cls._provider_name(client)}: fetch_rates() должен вернуть объект"
            )

        provider = cls._provider_name(client)
        last_samples = getattr(client, "last_samples", ())
        samples_by_pair: dict[str, RateSample] = {}
        if isinstance(last_samples, Iterable):
            for sample in last_samples:
                if isinstance(sample, RateSample):
                    samples_by_pair[f"{sample.from_currency}_{sample.to_currency}"] = (
                        sample
                    )

        fetched_at = datetime.now(timezone.utc)
        validated: list[RateSample] = []
        for pair, value in rates.items():
            if not isinstance(pair, str):
                raise ApiRequestError(f"{provider}: ключ курса должен быть строкой")
            match = _RATE_PAIR.fullmatch(pair)
            if match is None:
                raise ApiRequestError(f"{provider}: некорректная пара валют '{pair}'")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ApiRequestError(f"{provider}: некорректный курс для {pair}")
            rate = float(value)
            if not math.isfinite(rate) or rate <= 0:
                raise ApiRequestError(f"{provider}: некорректный курс для {pair}")

            from_code, to_code = match.groups()
            sample = samples_by_pair.get(pair)
            timestamp = fetched_at
            meta: dict[str, object] = {}
            if sample is not None:
                if not isinstance(sample.timestamp, datetime):
                    raise ApiRequestError(
                        f"{provider}: некорректный timestamp для {pair}"
                    )
                timestamp = sample.timestamp
                if timestamp.tzinfo is None:
                    raise ApiRequestError(
                        f"{provider}: timestamp для {pair} должен "
                        "содержать часовой пояс"
                    )
                if not isinstance(sample.meta, Mapping):
                    raise ApiRequestError(f"{provider}: некорректные meta для {pair}")
                meta = dict(sample.meta)
            validated.append(
                RateSample(
                    from_currency=from_code,
                    to_currency=to_code,
                    rate=rate,
                    timestamp=timestamp.astimezone(timezone.utc),
                    source=provider,
                    meta=meta,
                )
            )
        return validated

    @staticmethod
    def _provider_name(client: object) -> str:
        source = getattr(client, "source", None)
        if isinstance(source, str) and source.strip():
            return source.strip()
        return type(client).__name__

    @staticmethod
    def _to_history_record(sample: RateSample) -> dict[str, object]:
        timestamp = sample.timestamp.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
        if timestamp.endswith("+00:00"):
            timestamp = f"{timestamp[:-6]}Z"
        record_id = f"{sample.from_currency}_{sample.to_currency}_{timestamp}"
        return {
            "id": record_id,
            "from_currency": sample.from_currency,
            "to_currency": sample.to_currency,
            "rate": sample.rate,
            "timestamp": timestamp,
            "source": sample.source,
            "meta": sample.meta,
        }

    def _log_history_writes(
        self,
        records: list[dict[str, object]],
        previous_ids: set[str],
    ) -> None:
        seen_ids = set(previous_ids)
        for record in records:
            record_id = str(record["id"])
            status = "SKIPPED_DUPLICATE" if record_id in seen_ids else "APPENDED"
            seen_ids.add(record_id)
            self.logger.info(
                "PARSER_HISTORY id=%s source=%s rate=%s timestamp=%s result=%s",
                record_id,
                record["source"],
                record["rate"],
                record["timestamp"],
                status,
            )

    def _update_core_cache(self, samples: list[RateSample]) -> int:
        refreshed_at = datetime.now(timezone.utc)
        updated_at = refreshed_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        updated_pairs = 0
        transaction = getattr(self.storage, "transaction", None)
        transaction_context = transaction() if callable(transaction) else nullcontext()
        with transaction_context:
            load_rates = getattr(self.storage, "load_rates", None)
            rates = (
                load_rates()
                if callable(load_rates)
                else {"pairs": {}, "last_refresh": None}
            )
            if not isinstance(rates, Mapping):
                raise ApiRequestError("rates.json должен содержать JSON-объект")
            pairs = rates.get("pairs", {})
            if not isinstance(pairs, Mapping):
                raise ApiRequestError("Поле 'pairs' в rates.json должно быть объектом")
            next_pairs = dict(pairs)

            # Keep the latest successful provider's value for each directed pair.
            latest_samples: dict[str, RateSample] = {}
            for sample in samples:
                latest_samples[f"{sample.from_currency}_{sample.to_currency}"] = sample

            for pair_key, sample in latest_samples.items():
                updated_pairs += self._store_if_newer(
                    next_pairs,
                    pair_key,
                    sample.rate,
                    updated_at,
                    sample.source,
                    refreshed_at,
                )
                reverse_key = f"{sample.to_currency}_{sample.from_currency}"
                updated_pairs += self._store_if_newer(
                    next_pairs,
                    reverse_key,
                    1.0 / sample.rate,
                    updated_at,
                    sample.source,
                    refreshed_at,
                )

            snapshot = {
                "pairs": next_pairs,
                "last_refresh": updated_at,
            }
            save_rates = getattr(self.storage, "save_rates", None)
            if not callable(save_rates):
                raise ApiRequestError("storage должен поддерживать save_rates()")
            save_rates(snapshot)
        return updated_pairs

    @staticmethod
    def _store_if_newer(
        pairs: dict[str, Any],
        key: str,
        rate: float,
        updated_at: str,
        source: str,
        refreshed_at: datetime,
    ) -> int:
        """Replace a cached pair only when the incoming timestamp is newer."""
        current = pairs.get(key)
        if isinstance(current, Mapping):
            value = current.get("updated_at")
            if isinstance(value, str):
                try:
                    current_timestamp = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    if current_timestamp.tzinfo is None:
                        current_timestamp = current_timestamp.replace(
                            tzinfo=timezone.utc
                        )
                    else:
                        current_timestamp = current_timestamp.astimezone(timezone.utc)
                except ValueError:
                    current_timestamp = None
                if current_timestamp is not None and current_timestamp >= refreshed_at:
                    return 0
        pairs[key] = {
            "rate": rate,
            "updated_at": updated_at,
            "source": source,
        }
        return 1


class RateUpdater(RatesUpdater):
    """Backward-compatible constructor retained for existing integrations."""

    def __init__(
        self,
        config: ParserConfig | None = None,
        *,
        coingecko_client: CoinGeckoClient | None = None,
        exchangerate_client: ExchangeRateApiClient | None = None,
        history_storage: ExchangeRateStorage | None = None,
        database: DatabaseManager | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        configured = config or ParserConfig.from_settings()
        clients = (
            exchangerate_client
            if exchangerate_client is not None
            else ExchangeRateApiClient(configured),
            coingecko_client
            if coingecko_client is not None
            else CoinGeckoClient(configured),
        )
        super().__init__(
            clients=clients,
            storage=database,
            config=configured,
            history_storage=history_storage,
            logger=logger,
        )
