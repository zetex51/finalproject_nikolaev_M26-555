"""Configure the rotating, human-readable application action log."""

import logging
import logging.handlers
from pathlib import Path
from threading import Lock

_LOGGER_NAME = "valutatrade_hub.actions"
_CONFIG_LOCK = Lock()


def configure_logging(*, force: bool = False) -> logging.Logger:
    """Return the action logger configured for rotation and INFO by default."""
    from valutatrade_hub.infra.settings import SettingsLoader

    logger = logging.getLogger(_LOGGER_NAME)
    with _CONFIG_LOCK:
        if getattr(logger, "_valutatrade_configured", False) and not force:
            return logger
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        settings = SettingsLoader()
        log_path = Path(settings.get("log_file", "logs/actions.log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        level_name = str(settings.get("log_level", "INFO")).upper()
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=int(settings.get("log_max_bytes", 1_048_576)),
            backupCount=int(settings.get("log_backup_count", 3)),
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                settings.get(
                    "log_format",
                    "%(levelname)s %(asctime)s %(message)s",
                ),
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
        logger._valutatrade_configured = True  # type: ignore[attr-defined]
    return logger
