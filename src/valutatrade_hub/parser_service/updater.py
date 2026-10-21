"""Coordinate provider requests, history, the Core cache, and update logs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
    RateSample,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import ExchangeRateStorage


@dataclass(frozen=True)
class UpdateReport:
    """Summary of one parser run, including any partial provider failures."""

    history_records_added: int
    history_records_skipped: int
    cache_quotes_updated: int
    elapsed_ms: int
    errors: tuple[str, ...] = ()


class RateUpdater:
    """Fetch external quotes and publish validated snapshots to local JSON."""

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
        self.config = config or ParserConfig.from_settings()
        self.coingecko_client = coingecko_client or CoinGeckoClient(self.config)
        self.exchangerate_client = exchangerate_client or ExchangeRateApiClient(
            self.config
        )
        self.history_storage = history_storage or ExchangeRateStorage(
            self.config.history_path
        )
        self.database = database or DatabaseManager()
        self.logger = logger or configure_logging()

    def update_rates(self) -> UpdateReport:
        """Update each reachable provider, preserving history and Core rates."""
        started_at = time.perf_counter()
        self.logger.info("PARSER_UPDATE action=UPDATE_RATES result=START")
        samples: list[RateSample] = []
        errors: list[str] = []
        providers = (
            ("ExchangeRate-API", self.exchangerate_client.fetch_samples),
            ("CoinGecko", self.coingecko_client.fetch_samples),
        )

        for provider, fetch_samples in providers:
            try:
                provider_samples = fetch_samples()
            except ApiRequestError as error:
                errors.append(error.reason)
                self.logger.error(
                    "PARSER_PROVIDER provider=%s result=ERROR error_type=%s reason=%s",
                    provider,
                    type(error).__name__,
                    error.reason,
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
                "PARSER_PROVIDER provider=%s result=OK records=%d "
                "request_ms=%d status_code=%d",
                provider,
                len(provider_samples),
                request_ms,
                max(
                    (
                        int(sample.meta.get("status_code", 200))
                        for sample in provider_samples
                    ),
                    default=200,
                ),
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
        except ApiRequestError as error:
            self.logger.error(
                "PARSER_HISTORY result=ERROR error_type=%s reason=%s",
                type(error).__name__,
                error.reason,
            )
            raise
        self._log_history_writes(records, previous_ids)
        cache_quotes_updated = self._update_core_cache(samples)

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
        updated_at = refreshed_at.isoformat().replace("+00:00", "Z")
        updated_pairs = 0
        try:
            with self.database.transaction():
                try:
                    rates = self.database.load_rates()
                except (OSError, ValueError):
                    rates = {"pairs": {}, "last_refresh": None}
                pairs = rates.get("pairs")
                if not isinstance(pairs, dict):
                    pairs = {}
                for sample in samples:
                    pair_key = f"{sample.from_currency}_{sample.to_currency}"
                    reverse_key = f"{sample.to_currency}_{sample.from_currency}"
                    updated_pairs += self._store_if_newer(
                        pairs,
                        pair_key,
                        sample.rate,
                        updated_at,
                        sample.source,
                        refreshed_at,
                    )
                    updated_pairs += self._store_if_newer(
                        pairs,
                        reverse_key,
                        1.0 / sample.rate,
                        updated_at,
                        sample.source,
                        refreshed_at,
                    )
                rates["pairs"] = pairs
                rates["last_refresh"] = updated_at
                self.database.save_rates(rates)
        except (OSError, ValueError) as error:
            self.logger.error(
                "PARSER_CACHE result=ERROR error_type=%s reason=%s",
                type(error).__name__,
                error,
            )
            raise ApiRequestError(f"не удалось обновить rates.json: {error}") from error
        return updated_pairs

    @staticmethod
    def _store_if_newer(
        pairs: dict[str, object],
        key: str,
        rate: float,
        updated_at: str,
        source: str,
        refreshed_at: datetime,
    ) -> int:
        """Replace a cached pair only when the incoming timestamp is newer."""
        current = pairs.get(key)
        if isinstance(current, dict):
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
