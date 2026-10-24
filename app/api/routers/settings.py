from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.schemas import (
    AppSettingsIn,
    AppSettingsOut,
    BgActivityLogOut,
    ClearCacheOut,
    MessageOut,
)
from app.providers.stores import STORE_DEFAULT_ENABLED, STORE_ORDER, setting_key
from app.services import activity_log
from app.services.self_update import local_build_id
from app.services.file_cache import (
    DEFAULT_APK_KEEP_DAYS,
    MAX_APK_KEEP_DAYS,
    MIN_APK_KEEP_DAYS,
    clamp_apk_keep_days,
    purge_file_caches,
)

router = APIRouter(tags=["settings"])

_DEFAULTS = {
    "bg_updates_enabled": "0",
    "bg_updates_interval_hours": "6",
    "update_check_ttl_hours": "1",
    "auto_install_enabled": "0",
    "auto_install_interval_hours": "24",
    "notify_updates_enabled": "1",
    "prefer_release_updates": "1",
    "open_browser_on_start": "0",
    "hide_play_store": "1",
    "third_party_only": "1",
    "apk_cache_keep_days": str(DEFAULT_APK_KEEP_DAYS),
    "app_update_check_enabled": "1",
    "app_auto_update_enabled": "0",
    "app_update_check_interval_hours": "1",
}
for _sid in STORE_ORDER:
    _DEFAULTS[setting_key(_sid)] = "1" if STORE_DEFAULT_ENABLED.get(_sid, True) else "0"


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clamp_hours(raw: str | None, default: float) -> float:
    try:
        hours = float(raw) if raw is not None else default
    except ValueError:
        hours = default
    return max(0.25, min(168.0, hours))  # 15 min … 7 days


def _store_flags(db) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for sid in STORE_ORDER:
        key = setting_key(sid)
        flags[sid] = _as_bool(
            db.get_setting(key, _DEFAULTS[key]),
            STORE_DEFAULT_ENABLED.get(sid, True),
        )
    if not any(flags.values()):
        flags["rustore"] = True
    return flags


def _load_settings(db) -> AppSettingsOut:
    enabled = _as_bool(
        db.get_setting("bg_updates_enabled", _DEFAULTS["bg_updates_enabled"]),
        False,
    )
    hours = _clamp_hours(
        db.get_setting(
            "bg_updates_interval_hours", _DEFAULTS["bg_updates_interval_hours"]
        ),
        6.0,
    )
    ttl_hours = _clamp_hours(
        db.get_setting(
            "update_check_ttl_hours", _DEFAULTS["update_check_ttl_hours"]
        ),
        1.0,
    )
    auto_enabled = _as_bool(
        db.get_setting("auto_install_enabled", _DEFAULTS["auto_install_enabled"]),
        False,
    )
    auto_hours = _clamp_hours(
        db.get_setting(
            "auto_install_interval_hours", _DEFAULTS["auto_install_interval_hours"]
        ),
        24.0,
    )
    notify_enabled = _as_bool(
        db.get_setting("notify_updates_enabled", _DEFAULTS["notify_updates_enabled"]),
        True,
    )
    stores = _store_flags(db)
    return AppSettingsOut(
        bg_updates_enabled=enabled,
        bg_updates_interval_hours=hours,
        update_check_ttl_hours=ttl_hours,
        auto_install_enabled=auto_enabled,
        auto_install_interval_hours=auto_hours,
        notify_updates_enabled=notify_enabled,
        store_rustore_enabled=stores["rustore"],
        store_apkpure_enabled=stores["apkpure"],
        store_aptoide_enabled=stores["aptoide"],
        store_fdroid_enabled=stores["fdroid"],
        prefer_release_updates=_as_bool(
            db.get_setting(
                "prefer_release_updates", _DEFAULTS["prefer_release_updates"]
            ),
            True,
        ),
        open_browser_on_start=_as_bool(
            db.get_setting(
                "open_browser_on_start", _DEFAULTS["open_browser_on_start"]
            ),
            False,
        ),
        hide_play_store=_as_bool(
            db.get_setting("hide_play_store", _DEFAULTS["hide_play_store"]), True
        ),
        third_party_only=_as_bool(
            db.get_setting("third_party_only", _DEFAULTS["third_party_only"]), True
        ),
        apk_cache_keep_days=clamp_apk_keep_days(
            db.get_setting(
                "apk_cache_keep_days", _DEFAULTS["apk_cache_keep_days"]
            ),
            DEFAULT_APK_KEEP_DAYS,
        ),
        bg_updates_last_run=db.get_setting("bg_updates_last_run", "") or "",
        auto_install_last_run=db.get_setting("auto_install_last_run", "") or "",
        app_update_check_enabled=_as_bool(
            db.get_setting(
                "app_update_check_enabled", _DEFAULTS["app_update_check_enabled"]
            ),
            True,
        ),
        app_auto_update_enabled=_as_bool(
            db.get_setting(
                "app_auto_update_enabled", _DEFAULTS["app_auto_update_enabled"]
            ),
            False,
        ),
        app_update_check_interval_hours=_clamp_hours(
            db.get_setting(
                "app_update_check_interval_hours",
                _DEFAULTS["app_update_check_interval_hours"],
            ),
            1.0,
        ),
        app_update_last_check=db.get_setting("app_update_last_check", "") or "",
        build_id=local_build_id(),
    )


@router.get("/api/settings", response_model=AppSettingsOut)
def get_settings(request: Request) -> AppSettingsOut:
    return _load_settings(request.app.state.api.db)


@router.put("/api/settings", response_model=AppSettingsOut)
def put_settings(body: AppSettingsIn, request: Request) -> AppSettingsOut:
    db = request.app.state.api.db
    if body.bg_updates_enabled is not None:
        db.set_setting("bg_updates_enabled", "1" if body.bg_updates_enabled else "0")
    if body.bg_updates_interval_hours is not None:
        hours = float(body.bg_updates_interval_hours)
        if hours < 0.25 or hours > 168:
            raise HTTPException(status_code=400, detail="Интервал: от 0.25 до 168 часов")
        db.set_setting("bg_updates_interval_hours", str(hours))
    if body.update_check_ttl_hours is not None:
        ttl = float(body.update_check_ttl_hours)
        if ttl < 0.25 or ttl > 168:
            raise HTTPException(
                status_code=400, detail="Пауза повторной проверки: от 0.25 до 168 часов"
            )
        db.set_setting("update_check_ttl_hours", str(ttl))
    if body.auto_install_enabled is not None:
        db.set_setting(
            "auto_install_enabled", "1" if body.auto_install_enabled else "0"
        )
    if body.auto_install_interval_hours is not None:
        hours = float(body.auto_install_interval_hours)
        if hours < 0.25 or hours > 168:
            raise HTTPException(
                status_code=400, detail="Интервал автоустановки: от 0.25 до 168 часов"
            )
        db.set_setting("auto_install_interval_hours", str(hours))
    if body.notify_updates_enabled is not None:
        db.set_setting(
            "notify_updates_enabled", "1" if body.notify_updates_enabled else "0"
        )

    body_stores = {
        "rustore": body.store_rustore_enabled,
        "apkpure": body.store_apkpure_enabled,
        "aptoide": body.store_aptoide_enabled,
        "fdroid": body.store_fdroid_enabled,
    }
    if any(v is not None for v in body_stores.values()):
        cur = _store_flags(db)
        next_flags = {
            sid: (body_stores[sid] if body_stores[sid] is not None else cur[sid])
            for sid in STORE_ORDER
        }
        if not any(next_flags.values()):
            raise HTTPException(
                status_code=400, detail="Нужен хотя бы один включённый стор"
            )
        for sid, val in body_stores.items():
            if val is not None:
                db.set_setting(setting_key(sid), "1" if val else "0")

    if body.prefer_release_updates is not None:
        db.set_setting(
            "prefer_release_updates",
            "1" if body.prefer_release_updates else "0",
        )
    if body.open_browser_on_start is not None:
        db.set_setting(
            "open_browser_on_start",
            "1" if body.open_browser_on_start else "0",
        )
    if body.hide_play_store is not None:
        db.set_setting("hide_play_store", "1" if body.hide_play_store else "0")
    if body.third_party_only is not None:
        db.set_setting("third_party_only", "1" if body.third_party_only else "0")
    if body.apk_cache_keep_days is not None:
        days = float(body.apk_cache_keep_days)
        if days < MIN_APK_KEEP_DAYS or days > MAX_APK_KEEP_DAYS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Срок хранения APK: от {MIN_APK_KEEP_DAYS} до "
                    f"{MAX_APK_KEEP_DAYS} дней"
                ),
            )
        db.set_setting("apk_cache_keep_days", str(clamp_apk_keep_days(days)))
    if body.app_update_check_enabled is not None:
        db.set_setting(
            "app_update_check_enabled",
            "1" if body.app_update_check_enabled else "0",
        )
    if body.app_auto_update_enabled is not None:
        db.set_setting(
            "app_auto_update_enabled",
            "1" if body.app_auto_update_enabled else "0",
        )
    if body.app_update_check_interval_hours is not None:
        hours = float(body.app_update_check_interval_hours)
        if hours < 0.25 or hours > 168:
            raise HTTPException(
                status_code=400,
                detail="Интервал проверки сборки: от 0.25 до 168 часов",
            )
        db.set_setting("app_update_check_interval_hours", str(hours))
    return _load_settings(db)


@router.post("/api/settings/reset-bg", response_model=MessageOut)
def reset_bg_hint(request: Request) -> MessageOut:
    """Optional: clear last bg timestamp marker (client-driven; kept for symmetry)."""
    request.app.state.api.db.set_setting("bg_updates_last_run", "")
    return MessageOut(message="Сброшено")


@router.get("/api/settings/activity-log", response_model=BgActivityLogOut)
def get_activity_log(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
) -> BgActivityLogOut:
    entries = activity_log.list_entries(request.app.state.api.db, limit=limit)
    return BgActivityLogOut(entries=entries)


@router.delete("/api/settings/activity-log", response_model=MessageOut)
def clear_activity_log(request: Request) -> MessageOut:
    activity_log.clear(request.app.state.api.db)
    return MessageOut(message="Журнал очищен")


@router.post("/api/settings/clear-cache", response_model=ClearCacheOut)
def clear_file_cache(request: Request) -> ClearCacheOut:
    """Delete APK / icon / device preview files. SQLite DB and tools stay intact."""
    _ = request  # keep signature consistent with other settings routes
    result = purge_file_caches()
    return ClearCacheOut(
        ok=bool(result.get("ok", True)),
        message=str(result.get("message") or "Кеш очищен"),
        files=int(result.get("files") or 0),
        total_bytes=int(result.get("bytes") or 0),
        by_kind=dict(result.get("by_kind") or {}),
    )


@router.post("/api/settings/unlock-job", response_model=MessageOut)
def unlock_job(request: Request) -> MessageOut:
    """Force-clear stuck job_running lock (does not stop an in-flight worker)."""
    info = request.app.state.api.force_end_job()
    return MessageOut(ok=True, message=str(info.get("message") or "Готово"))
