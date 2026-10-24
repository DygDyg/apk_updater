from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from app.services import activity_log, update_file_log
from app.services.batch_jobs import run_catch_up_pending, run_check_batch, run_install_all
from app.services.file_cache import (
    DEFAULT_APK_KEEP_DAYS,
    clamp_apk_keep_days,
    purge_expired_apks,
)

if TYPE_CHECKING:
    from app.api.state import AppState

log = logging.getLogger("apk_updater.background")

POLL_SEC = 45.0


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _hours(db, key: str, default: float) -> float:
    raw = db.get_setting(key, str(default))
    try:
        hours = float(raw) if raw is not None else default
    except ValueError:
        hours = default
    return max(0.25, min(168.0, hours))


def _last_run(db, key: str) -> float:
    raw = db.get_setting(key, "0") or "0"
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _due(db, last_key: str, interval_key: str, default_hours: float) -> bool:
    last = _last_run(db, last_key)
    hours = _hours(db, interval_key, default_hours)
    return (time.time() - last) >= hours * 3600.0


class BackgroundWorker:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="apk-bg", daemon=True)
        self._thread.start()
        log.info("Background worker started")
        update_file_log.write(
            f"Background worker started → log {update_file_log.updates_log_path()}"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("Background worker stopped")

    def _loop(self) -> None:
        # Delay so UI can claim the first check when cache is empty
        self._stop.wait(8.0)
        if not self._stop.is_set():
            try:
                self._startup_check()
            except Exception:  # noqa: BLE001
                log.exception("Startup update check failed")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("Background tick failed")
            self._stop.wait(POLL_SEC)

    def _startup_check(self) -> None:
        """One check-batch when the process starts (devices + stores).

        Skip if UI / previous run already left a persisted inventory cache —
        the web UI also auto-starts a check when the cache is empty.
        """
        state = self.state
        db = state.db
        try:
            state.devices.refresh(with_profiles=False)
        except Exception:  # noqa: BLE001
            log.exception("Startup device refresh failed")
        # Prefer in-memory / DB cache restored at boot
        if any(state.apps_cache.values()):
            log.info("Startup check skipped: cache already loaded")
            activity_log.append(
                db,
                kind="startup",
                event="skip",
                status="skip",
                message="Стартовая проверка пропущена: кеш уже загружен",
            )
            return
        try:
            from app.services.persist_cache import load_device_caches

            apps, _updates, _checked = load_device_caches(state.db)
            if any(apps.values()):
                log.info("Startup check skipped: persisted cache present")
                activity_log.append(
                    db,
                    kind="startup",
                    event="skip",
                    status="skip",
                    message="Стартовая проверка пропущена: есть сохранённый кеш",
                )
                return
        except Exception:  # noqa: BLE001
            pass
        if not state.try_begin_job():
            log.info("Startup check skipped: job already running")
            activity_log.append(
                db,
                kind="startup",
                event="skip",
                status="skip",
                message="Стартовая проверка пропущена: уже идёт другая задача",
            )
            return
        try:
            log.info("Startup check-batch")
            activity_log.append(
                db,
                kind="startup",
                event="start",
                message="Стартовая проверка обновлений",
            )
            prog = activity_log.make_progress(
                db, "startup", also_log=lambda m: log.info("startup-check: %s", m)
            )
            result = run_check_batch(
                state,
                force=True,
                progress=prog,
                record_bg_run=True,
            )
            activity_log.append(
                db,
                kind="startup",
                event="finish",
                status="ok",
                message=result.message or "Стартовая проверка завершена",
            )
        except Exception as exc:  # noqa: BLE001
            activity_log.append(
                db,
                kind="startup",
                event="finish",
                status="fail",
                message=f"Стартовая проверка с ошибкой: {exc}",
            )
            raise
        finally:
            state.end_job()

    def _purge_old_apks(self) -> None:
        db = self.state.db
        days = clamp_apk_keep_days(
            db.get_setting("apk_cache_keep_days", str(DEFAULT_APK_KEEP_DAYS)),
            DEFAULT_APK_KEEP_DAYS,
        )
        try:
            result = purge_expired_apks(days, db=db)
            if result.get("files"):
                log.info("%s", result.get("message"))
        except Exception:  # noqa: BLE001
            log.exception("APK cache TTL purge failed")

    def _due_info(self, last_key: str, interval_key: str, default_hours: float) -> str:
        db = self.state.db
        last = _last_run(db, last_key)
        hours = _hours(db, interval_key, default_hours)
        age_h = (time.time() - last) / 3600.0 if last > 0 else 9999.0
        due = age_h >= hours
        return f"due={due} age_h={age_h:.2f} interval_h={hours}"

    def _tick(self) -> None:
        state = self.state
        db = state.db

        # Drop expired downloaded APKs (icons / DB untouched)
        self._purge_old_apks()

        # Light refresh of device online state
        try:
            state.devices.refresh(with_profiles=False)
        except Exception:  # noqa: BLE001
            log.exception("Device refresh failed")

        known = state.devices.list_known()
        online_n = sum(1 for d in known if d.online)
        pending_due = db.list_pending(due_only=True)
        bg_on = _as_bool(db.get_setting("bg_updates_enabled", "0"))
        auto_on = _as_bool(db.get_setting("auto_install_enabled", "0"))
        ttl = db.get_setting("update_check_ttl_hours", "1") or "1"
        update_file_log.write(
            "tick | "
            f"devices={len(known)} online={online_n} pending_due={len(pending_due)} | "
            f"bg_check={'on' if bg_on else 'off'} "
            f"({self._due_info('bg_updates_last_run', 'bg_updates_interval_hours', 6.0)}) | "
            f"auto_install={'on' if auto_on else 'off'} "
            f"({self._due_info('auto_install_last_run', 'auto_install_interval_hours', 24.0)}) | "
            f"ttl_h={ttl} job={state.job_label or '-'}"
        )

        # Catch-up pending — take job lock only when there is work
        due = pending_due
        if due and state.try_begin_job(label="catch_up"):
            try:
                prog = activity_log.make_progress(
                    db, "catch_up", also_log=lambda m: log.info("catch-up: %s", m)
                )
                activity_log.append(
                    db,
                    kind="catch_up",
                    event="start",
                    message=f"Догон очереди: {len(due)} задач(и)",
                )
                msg = run_catch_up_pending(state, progress=prog)
                if msg:
                    log.info("Catch-up: %s", msg)
                activity_log.append(
                    db,
                    kind="catch_up",
                    event="finish",
                    status="ok" if msg else "skip",
                    message=msg or "Догон: online-устройств нет или нечего делать",
                )
            finally:
                state.end_job()
        elif due:
            update_file_log.write(
                f"catch_up skipped: job busy ({state.job_label or 'unknown'}), "
                f"pending_due={len(due)}"
            )

        if bg_on and _due(db, "bg_updates_last_run", "bg_updates_interval_hours", 6.0):
            if state.try_begin_job(label="bg_check"):
                try:
                    log.info("Scheduled check-batch")
                    activity_log.append(
                        db,
                        kind="check",
                        event="start",
                        message="Фоновая проверка обновлений (force по интервалу)",
                    )
                    prog = activity_log.make_progress(
                        db, "check", also_log=lambda m: log.info("check: %s", m)
                    )
                    # force=True: interval already gates frequency; TTL must not
                    # swallow hourly checks when update_check_ttl_hours is large.
                    result = run_check_batch(
                        state,
                        force=True,
                        progress=prog,
                        record_bg_run=True,
                    )
                    upd_n = 0
                    for rows in (result.devices or {}).values():
                        upd_n += sum(1 for a in rows if a.get("has_update"))
                    activity_log.append(
                        db,
                        kind="check",
                        event="finish",
                        status="ok",
                        message=result.message or "Проверка завершена",
                        detail=(
                            f"cached={result.cached}; updates={upd_n}; "
                            f"pending={len(result.pending_serials or [])}"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    activity_log.append(
                        db,
                        kind="check",
                        event="finish",
                        status="fail",
                        message=f"Ошибка фоновой проверки: {exc}",
                    )
                    raise
                finally:
                    state.end_job()
            else:
                update_file_log.write(
                    f"bg_check due but job busy ({state.job_label or 'unknown'})"
                )

        # Portable app self-update (GitHub Releases)
        app_check_on = _as_bool(db.get_setting("app_update_check_enabled", "1"), True)
        if app_check_on and _due(
            db,
            "app_update_last_check",
            "app_update_check_interval_hours",
            1.0,
        ):
            try:
                from app.services import self_update

                result = self_update.check_for_update(persist_db=db)
                update_file_log.write(
                    f"app_update_check | ok={result.ok} "
                    f"local={result.local_build} latest={result.latest_build} "
                    f"available={result.update_available}"
                )
                auto_app = _as_bool(
                    db.get_setting("app_auto_update_enabled", "0"), False
                )
                if (
                    result.ok
                    and result.update_available
                    and auto_app
                    and result.frozen
                ):
                    update_file_log.write(
                        f"app_auto_update applying {result.latest_build}"
                    )
                    applied = self_update.apply_update(
                        download_url=result.download_url,
                        delay_sec=5,
                        db=db,
                    )
                    update_file_log.write(
                        f"app_auto_update | ok={applied.ok} {applied.message}"
                    )
                    if applied.restarting:
                        return
            except Exception:  # noqa: BLE001
                log.exception("App self-update check failed")

        if auto_on and _due(
            db, "auto_install_last_run", "auto_install_interval_hours", 24.0
        ):
            if state.try_begin_job(label="auto_install"):
                try:
                    log.info("Scheduled auto-install: force check then install-all")
                    activity_log.append(
                        db,
                        kind="auto_install",
                        event="start",
                        message=(
                            "Автоустановка: проверка, затем установка "
                            "(известный стор / при Авто — выбранный remote)"
                        ),
                    )
                    check_prog = activity_log.make_progress(
                        db,
                        "auto_install",
                        also_log=lambda m: log.info("auto-check: %s", m),
                    )
                    check = run_check_batch(
                        state,
                        force=True,
                        progress=check_prog,
                        record_bg_run=True,
                    )
                    upd_n = 0
                    for rows in (check.devices or {}).values():
                        upd_n += sum(1 for a in rows if a.get("has_update"))
                    activity_log.append(
                        db,
                        kind="auto_install",
                        event="progress",
                        message=f"Проверка перед установкой: {check.message}",
                        detail=f"updates={upd_n}",
                    )
                    inst_prog = activity_log.make_progress(
                        db,
                        "auto_install",
                        also_log=lambda m: log.info("auto-install: %s", m),
                    )
                    inst = run_install_all(
                        state,
                        progress=inst_prog,
                        same_source_only=True,
                    )
                    db.set_setting("auto_install_last_run", str(time.time()))
                    st = "ok" if inst.fail == 0 else ("fail" if inst.ok == 0 else "info")
                    activity_log.append(
                        db,
                        kind="auto_install",
                        event="finish",
                        status=st,
                        message=inst.message or "Автоустановка завершена",
                        detail=f"ok={inst.ok}, fail={inst.fail}",
                    )
                except Exception as exc:  # noqa: BLE001
                    activity_log.append(
                        db,
                        kind="auto_install",
                        event="finish",
                        status="fail",
                        message=f"Ошибка автоустановки: {exc}",
                    )
                    raise
                finally:
                    state.end_job()
            else:
                update_file_log.write(
                    f"auto_install due but job busy ({state.job_label or 'unknown'})"
                )
