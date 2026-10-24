from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.adb.client import AdbClient
from app.db.database import Database
from app.providers.base import RemoteVersion
from app.services.apps import AppInfo, AppInventoryService
from app.services.cache import ApkCache
from app.services.devices import DeviceService
from app.services.install import InstallService
from app.services.persist_cache import (
    load_device_caches,
    load_remotes,
    save_device_apps,
    save_device_updates,
    save_remotes,
)
from app.services.qr_pairing import QrPairingSession
from app.services.updates import UpdateInfo, UpdateService

DEFAULT_UPDATE_CHECK_TTL_HOURS = 1.0


@dataclass
class QrRuntime:
    session: QrPairingSession
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass
class AppState:
    db: Database
    adb: AdbClient
    devices: DeviceService
    apps: AppInventoryService
    install: InstallService
    updates: UpdateService
    cache: ApkCache
    qr: QrRuntime | None = None
    # In-memory apps list per logical serial for update checks
    apps_cache: dict[str, list[AppInfo]] = field(default_factory=dict)
    # serial -> package -> UpdateInfo
    last_updates: dict[str, dict[str, UpdateInfo]] = field(default_factory=dict)
    # serial -> unix timestamp of last store check
    last_update_check_at: dict[str, float] = field(default_factory=dict)
    # package -> last fetched remote (shared across devices)
    remotes_cache: dict[str, RemoteVersion | None] = field(default_factory=dict)
    # package -> all store hits (RuStore, APKPure, …)
    remotes_stores_cache: dict[str, tuple[RemoteVersion, ...]] = field(default_factory=dict)
    remotes_fetched_at: float = 0.0
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    job_running: bool = False
    job_started_at: float = 0.0
    job_label: str = ""
    # Steal lock if a job never called end_job (SSE abort / crashed worker)
    job_stale_sec: float = 30 * 60

    def cache_key(self, serial: str) -> str:
        try:
            return self.devices.resolve_online(serial) or serial
        except Exception:  # noqa: BLE001
            return serial

    def get_apps(self, serial: str) -> list[AppInfo]:
        key = self.cache_key(serial)
        return self.apps_cache.get(serial) or self.apps_cache.get(key) or []

    def set_apps(self, serial: str, apps: list[AppInfo]) -> None:
        key = self.cache_key(serial)
        self.apps_cache[serial] = apps
        self.apps_cache[key] = apps
        try:
            save_device_apps(self.db, serial, apps)
            if key != serial:
                save_device_apps(self.db, key, apps)
        except Exception:  # noqa: BLE001
            pass

    def get_updates(self, serial: str) -> dict[str, UpdateInfo]:
        key = self.cache_key(serial)
        return self.last_updates.get(serial) or self.last_updates.get(key) or {}

    def set_updates(self, serial: str, mapping: dict[str, UpdateInfo]) -> None:
        key = self.cache_key(serial)
        self.last_updates[serial] = mapping
        self.last_updates[key] = mapping
        now = time.time()
        self.last_update_check_at[serial] = now
        self.last_update_check_at[key] = now
        try:
            save_device_updates(self.db, serial, mapping, now)
            if key != serial:
                save_device_updates(self.db, key, mapping, now)
        except Exception:  # noqa: BLE001
            pass

    def load_persisted_cache(self) -> None:
        """Restore inventory / updates / remotes from SQLite after restart."""
        try:
            apps, updates, checked = load_device_caches(self.db)
            remotes, stores, fetched_at = load_remotes(self.db)
        except Exception:  # noqa: BLE001
            return
        self.apps_cache.update(apps)
        self.last_updates.update(updates)
        self.last_update_check_at.update(checked)
        if remotes or stores:
            self.remotes_cache = remotes
            self.remotes_stores_cache = stores
            self.remotes_fetched_at = fetched_at

    def update_check_ttl_sec(self) -> float:
        raw = self.db.get_setting("update_check_ttl_hours", str(DEFAULT_UPDATE_CHECK_TTL_HOURS))
        try:
            hours = float(raw) if raw is not None else DEFAULT_UPDATE_CHECK_TTL_HOURS
        except ValueError:
            hours = DEFAULT_UPDATE_CHECK_TTL_HOURS
        hours = max(0.25, min(168.0, hours))
        return hours * 3600.0

    def updates_fresh(self, serial: str) -> bool:
        key = self.cache_key(serial)
        ts = self.last_update_check_at.get(serial) or self.last_update_check_at.get(key)
        if ts is None:
            return False
        return (time.time() - ts) < self.update_check_ttl_sec()

    def set_remotes(
        self,
        remotes: dict[str, RemoteVersion | None],
        *,
        stores: dict[str, tuple[RemoteVersion, ...]] | None = None,
        replace: bool = False,
    ) -> None:
        if replace:
            self.remotes_cache = dict(remotes)
            self.remotes_stores_cache = dict(stores or {})
            persist_remotes = dict(self.remotes_cache)
            persist_stores = dict(self.remotes_stores_cache)
        else:
            self.remotes_cache.update(remotes)
            if stores:
                self.remotes_stores_cache.update(stores)
            affected = set(remotes) | set(stores or {})
            persist_remotes = {p: self.remotes_cache.get(p) for p in affected}
            persist_stores = {
                p: self.remotes_stores_cache.get(p, ()) for p in affected
            }
        self.remotes_fetched_at = time.time()
        try:
            save_remotes(
                self.db,
                persist_remotes,
                persist_stores,
                fetched_at=self.remotes_fetched_at,
                replace=replace,
            )
        except Exception:  # noqa: BLE001
            pass

    def try_begin_job(self, *, label: str = "") -> bool:
        with self.job_lock:
            if self.job_running:
                started = self.job_started_at or 0.0
                if started and (time.time() - started) >= self.job_stale_sec:
                    # Previous job never released (client abort before SSE start, etc.)
                    self.job_running = False
                    self.job_started_at = 0.0
                    self.job_label = ""
                else:
                    return False
            self.job_running = True
            self.job_started_at = time.time()
            self.job_label = label or ""
            return True

    def end_job(self) -> None:
        with self.job_lock:
            self.job_running = False
            self.job_started_at = 0.0
            self.job_label = ""

    def force_end_job(self) -> dict:
        """Manual unlock from settings when the lock is stuck."""
        with self.job_lock:
            was = self.job_running
            label = self.job_label
            started = self.job_started_at
            self.job_running = False
            self.job_started_at = 0.0
            self.job_label = ""
        return {
            "was_running": was,
            "label": label,
            "started_at": started,
            "message": (
                "Блокировка сброшена"
                if was
                else "Блокировки не было — можно работать"
            ),
        }

    def job_status(self) -> dict:
        with self.job_lock:
            running = self.job_running
            started = self.job_started_at
            label = self.job_label
        age = (time.time() - started) if running and started else 0.0
        return {
            "job_running": running,
            "job_label": label,
            "job_started_at": started,
            "job_age_sec": age,
        }

    def hide_play_store(self) -> bool:
        return (self.db.get_setting("hide_play_store", "1") or "1").strip() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def third_party_only(self) -> bool:
        return (self.db.get_setting("third_party_only", "1") or "1").strip() in {
            "1",
            "true",
            "yes",
            "on",
        }
