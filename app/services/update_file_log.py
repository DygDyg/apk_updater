from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.paths import LOG_DIR, ensure_data_dirs

UPDATES_LOG = LOG_DIR / "updates.log"

_lock = threading.Lock()
_configured = False


def updates_log_path() -> Path:
    return UPDATES_LOG


def ensure_update_file_log() -> logging.Logger:
    """Rotating UTF-8 log at data/logs/updates.log (keep ~2 MB x 5)."""
    global _configured
    log = logging.getLogger("apk_updater.updates")
    with _lock:
        if _configured:
            return log
        ensure_data_dirs()
        from app.paths import LOG_DIR as _log_dir

        _log_dir.mkdir(parents=True, exist_ok=True)
        log.setLevel(logging.INFO)
        log.propagate = False
        # Avoid duplicate handlers on reload
        if not any(
            isinstance(h, RotatingFileHandler)
            and Path(getattr(h, "baseFilename", "")) == UPDATES_LOG.resolve()
            for h in log.handlers
        ):
            handler = RotatingFileHandler(
                UPDATES_LOG,
                maxBytes=2_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            log.addHandler(handler)
        _configured = True
    return log


def write(message: str, *, level: int = logging.INFO) -> None:
    ensure_update_file_log().log(level, message)
