"""Run rate updates periodically using the Python standard library."""

from __future__ import annotations

import argparse
from threading import Event

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.updater import RateUpdater


def run_scheduler(
    interval_seconds: int | None = None,
    *,
    updater: RateUpdater | None = None,
    stop_event: Event | None = None,
) -> None:
    """Update immediately, then repeat until interrupted or stopped."""
    config = ParserConfig.from_settings()
    interval = (
        config.update_interval_seconds if interval_seconds is None else interval_seconds
    )
    if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
        raise ValueError("Интервал обновления должен быть положительным целым числом.")

    service = updater or RateUpdater(config)
    logger = configure_logging()
    logger.info("PARSER_SCHEDULER result=START interval_seconds=%d", interval)
    stop = stop_event or Event()
    try:
        while not stop.is_set():
            try:
                service.update_rates()
            except ApiRequestError as error:
                logger.error(
                    "PARSER_SCHEDULER result=UPDATE_ERROR reason=%s",
                    error.reason,
                )
            if stop.wait(interval):
                break
    except KeyboardInterrupt:
        logger.info("PARSER_SCHEDULER result=STOP reason=keyboard_interrupt")
    else:
        logger.info("PARSER_SCHEDULER result=STOP")


def main() -> None:
    """Launch the configured scheduler as ``wallet-rate-scheduler``."""
    parser = argparse.ArgumentParser(description="Периодическое обновление курсов")
    parser.add_argument(
        "--interval",
        type=int,
        help="интервал в секундах (по умолчанию 240)",
    )
    arguments = parser.parse_args()
    run_scheduler(arguments.interval)


if __name__ == "__main__":
    main()
