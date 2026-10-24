from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("apk_updater.notify")

# title, message
Notifier = Callable[[str, str], None]

_notifier: Notifier | None = None


def set_notifier(fn: Notifier | None) -> None:
    global _notifier
    _notifier = fn


def tray_notify(message: str, title: str = "APK Updater") -> None:
    fn = _notifier
    if fn is None:
        return
    try:
        fn(title, message)
    except Exception:  # noqa: BLE001
        log.exception("Tray notify failed")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def maybe_notify_updates_available(db, *, total_updates: int, cached: bool) -> None:
    """Notify when updates exist and auto-install is off."""
    if cached or total_updates <= 0:
        return
    if _as_bool(db.get_setting("auto_install_enabled", "0")):
        return
    if not _as_bool(db.get_setting("notify_updates_enabled", "1"), True):
        return
    tray_notify(
        f"Доступно обновлений: {total_updates}",
        "APK Updater",
    )
