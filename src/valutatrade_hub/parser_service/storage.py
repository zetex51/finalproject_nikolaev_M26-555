"""Append-only, atomic JSON storage for parser rate history."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

from valutatrade_hub.core.exceptions import ApiRequestError

_CURRENCY_CODE = re.compile(r"^[A-Z0-9]{2,5}$")


class ExchangeRateStorage:
    """Persist validated historical measurements without duplicate IDs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> list[dict[str, Any]]:
        """Load historical records, returning an empty list for a new store."""
        with self._lock:
            if not self.path.exists():
                return []
            try:
                with self.path.open(encoding="utf-8") as history_file:
                    records = json.load(history_file)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ApiRequestError(
                    f"не удалось прочитать историю курсов: {error}"
                ) from error
            if not isinstance(records, list):
                raise ApiRequestError("история курсов должна быть JSON-массивом")
            return [self._validate_record(record) for record in records]

    def append_many(self, records: Iterable[Mapping[str, object]]) -> tuple[int, int]:
        """Atomically append unseen IDs; return (added, already_present)."""
        validated = [self._validate_record(record) for record in records]
        with self._lock:
            loaded = self.load()
            current: list[dict[str, Any]] = []
            known_ids: set[str] = set()
            for record in loaded:
                if record["id"] not in known_ids:
                    known_ids.add(record["id"])
                    current.append(record)
            added: list[dict[str, Any]] = []
            skipped = 0
            for record in validated:
                if record["id"] in known_ids:
                    skipped += 1
                    continue
                known_ids.add(record["id"])
                added.append(record)
            if added or len(current) != len(loaded) or not self.path.exists():
                self._write_atomic([*current, *added])
            return len(added), skipped

    @classmethod
    def _validate_record(cls, record: object) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            raise ApiRequestError("запись курса должна быть JSON-объектом")
        from_code = record.get("from_currency")
        to_code = record.get("to_currency")
        timestamp = record.get("timestamp")
        source = record.get("source")
        rate = record.get("rate")
        if (
            not isinstance(from_code, str)
            or not _CURRENCY_CODE.fullmatch(from_code)
            or not isinstance(to_code, str)
            or not _CURRENCY_CODE.fullmatch(to_code)
        ):
            raise ApiRequestError("коды валют в истории должны быть в верхнем регистре")
        if not isinstance(timestamp, str):
            raise ApiRequestError("у записи курса отсутствует timestamp")
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ApiRequestError("timestamp должен быть ISO-датой") from error
        if (
            parsed_timestamp.tzinfo is None
            or parsed_timestamp.utcoffset() != timezone.utc.utcoffset(parsed_timestamp)
        ):
            raise ApiRequestError("timestamp должен быть в UTC")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ApiRequestError("rate должен быть числом")
        try:
            numeric_rate = float(rate)
        except (OverflowError, ValueError) as error:
            raise ApiRequestError(
                "rate должен быть конечным положительным числом"
            ) from error
        if not math.isfinite(numeric_rate) or numeric_rate <= 0:
            raise ApiRequestError("rate должен быть конечным положительным числом")
        if not isinstance(source, str) or not source.strip():
            raise ApiRequestError("source должен быть непустой строкой")
        meta = record.get("meta", {})
        if not isinstance(meta, Mapping):
            raise ApiRequestError("meta должен быть JSON-объектом")
        expected_id = f"{from_code}_{to_code}_{timestamp}"
        record_id = record.get("id")
        if not isinstance(record_id, str) or record_id != expected_id:
            raise ApiRequestError("id записи не соответствует паре валют и timestamp")
        validated = {
            "id": expected_id,
            "from_currency": from_code,
            "to_currency": to_code,
            "rate": numeric_rate,
            "timestamp": timestamp,
            "source": source.strip(),
            "meta": dict(meta),
        }
        try:
            json.dumps(validated, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ApiRequestError("запись курса содержит не-JSON данные") from error
        return validated

    def _write_atomic(self, records: list[dict[str, Any]]) -> None:
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    records,
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            raise ApiRequestError(
                f"не удалось атомарно сохранить историю: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
