from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    CheckUpdatesBatchIn,
    CheckUpdatesIn,
    InstallAllIn,
    InstallUpdateIn,
    ReinstallUpdateIn,
)
from app.api.serialize import app_out
from app.api.sse_job import stream_background_job
from app.services.batch_jobs import cache_snapshot, run_check_batch, run_install_all
from app.services.package_sources import (
    install_source_tooltip,
    resolve_preferred_source,
)
from app.services.updates import UpdateInfo

router = APIRouter(tags=["updates"])


@router.get("/api/updates/cache")
def get_updates_cache(request: Request) -> dict:
    return cache_snapshot(request.app.state.api)


@router.post("/api/updates/check-batch")
def check_updates_batch(body: CheckUpdatesBatchIn, request: Request) -> StreamingResponse:
    state = request.app.state.api
    if not state.try_begin_job():
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        try:
            if body.hide_play_store is not None:
                # ephemeral preference already in DB usually; ignore override for batch hide
                pass
            result = run_check_batch(
                state,
                force=body.force,
                serials=body.serials,
                progress=progress,
                record_bg_run=False,
            )
            return {
                "type": "done",
                "devices": result.devices,
                "pending_serials": result.pending_serials,
                "cached": result.cached,
                "message": result.message,
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")


@router.post("/api/updates/install-all")
def install_all_updates(body: InstallAllIn, request: Request) -> StreamingResponse:
    state = request.app.state.api
    if not state.try_begin_job():
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        try:
            result = run_install_all(state, serials=body.serials, progress=progress)
            snap = cache_snapshot(state)
            return {
                "type": "done",
                "ok": result.fail == 0,
                "message": result.message,
                "details": result.details,
                "pending_serials": result.pending_serials,
                "devices": snap["devices"],
                "update_counts": snap["update_counts"],
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")


@router.post("/api/updates/check")
def check_updates(body: CheckUpdatesIn, request: Request) -> StreamingResponse:
    """Single-device check via batch job (dedup remotes still apply for one device)."""
    state = request.app.state.api
    if not state.try_begin_job():
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        try:
            result = run_check_batch(
                state,
                force=body.force,
                serials=[body.serial],
                progress=progress,
            )
            apps = result.devices.get(body.serial) or []
            return {
                "type": "done",
                "apps": apps,
                "cached": result.cached,
                "message": result.message,
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")


@router.post("/api/updates/install")
def install_update(body: InstallUpdateIn, request: Request) -> StreamingResponse:
    state = request.app.state.api
    serial = body.serial
    from app.services.update_ignores import is_ignored

    if is_ignored(state.db, serial, body.package_name):
        raise HTTPException(
            status_code=400,
            detail="Обновление отключено для этого приложения (снимите пометку «не обновлять»)",
        )
    apps = state.get_apps(serial)
    last_map: dict[str, UpdateInfo] = state.get_updates(serial)
    update = last_map.get(body.package_name)
    if update is None or not update.has_update:
        app = next((a for a in apps if a.package_name == body.package_name), None)
        if app is None:
            raise HTTPException(status_code=404, detail="Приложение не в кеше — загрузите список")
        mapping = state.updates.check_updates([app], remotes=state.remotes_cache or None)
        state.set_updates(serial, {**last_map, **mapping})
        update = mapping.get(body.package_name)
        if update is None or not update.has_update:
            raise HTTPException(status_code=400, detail="Обновление недоступно")

    if not state.try_begin_job():
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        app_label = update.label or body.package_name
        try:
            known = {d.serial: d for d in state.devices.list_known()}
            device_label = known[serial].label if serial in known else serial
            item_id = f"{body.package_name}::{serial}"
            progress(
                {
                    "type": "install_plan",
                    "items": [
                        {
                            "id": item_id,
                            "package": body.package_name,
                            "app_label": app_label,
                            "serial": serial,
                            "device_label": device_label,
                            "status": "queued",
                            "detail": "В очереди",
                        }
                    ],
                    "message": f"Обновление {app_label}",
                }
            )

            def dl_prog(m: str) -> None:
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "package": body.package_name,
                        "app_label": app_label,
                        "serial": serial,
                        "device_label": device_label,
                        "status": "downloading",
                        "detail": m,
                    }
                )

            try:
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "package": body.package_name,
                        "app_label": app_label,
                        "serial": serial,
                        "device_label": device_label,
                        "status": "downloading",
                        "detail": "Скачивание…",
                    }
                )
                _artifact, results = state.updates.download_and_install(
                    update,
                    [serial],
                    progress=dl_prog,
                    item_progress=lambda s, phase, detail: progress(
                        {
                            "type": "install_item",
                            "id": item_id,
                            "package": body.package_name,
                            "app_label": app_label,
                            "serial": s,
                            "device_label": device_label,
                            "status": phase,
                            "detail": detail,
                        }
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                state.db.enqueue_pending(
                    serial,
                    "install",
                    package_name=body.package_name,
                    reason=f"install_fail: {exc}",
                )
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "status": "fail",
                        "detail": str(exc),
                    }
                )
                return {"type": "error", "message": str(exc)}

            r = results[0] if results else None
            if r and not r.success:
                from app.services.install_flags import (
                    FLAG_OLDER_SDK,
                    FLAG_SIGNATURE,
                    set_flag,
                )

                permanent = r.error_code in {FLAG_SIGNATURE, FLAG_OLDER_SDK}
                if permanent:
                    try:
                        set_flag(
                            state.db,
                            serial,
                            body.package_name,
                            flag=r.error_code,
                            source=(update.remote.source if update.remote else ""),
                            message=r.message or r.error_title or "",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    state.db.clear_pending(
                        serial, kind="install", package_name=body.package_name
                    )
                else:
                    state.db.enqueue_pending(
                        serial,
                        "install",
                        package_name=body.package_name,
                        reason=f"install_fail: {r.message}",
                    )
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "package": body.package_name,
                        "app_label": app_label,
                        "serial": serial,
                        "device_label": device_label,
                        "status": "fail",
                        **r.as_progress_fields(),
                    }
                )
            elif r and r.success:
                mapping = dict(state.get_updates(serial))
                old = mapping.get(body.package_name)
                if old:
                    mapping[body.package_name] = UpdateInfo(
                        package_name=old.package_name,
                        label=old.label,
                        local_code=old.remote.version_code if old.remote else old.local_code,
                        local_name=(old.remote.version_name if old.remote else old.local_name),
                        remote=old.remote,
                        skipped_play=old.skipped_play,
                        status="up_to_date",
                        error="",
                        stores=old.stores,
                        checked_sources=old.checked_sources,
                    )
                    state.set_updates(serial, mapping)
                state.db.clear_pending(
                    serial, kind="install", package_name=body.package_name
                )
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "status": "ok",
                        "detail": "Готово",
                    }
                )
            from app.services.update_ignores import list_ignored

            ignored = list_ignored(state.db, serial)
            return {
                "type": "done",
                "ok": bool(r and r.success),
                "message": r.message if r else "Нет результата",
                "apps": [
                    app_out(
                        a,
                        state.get_updates(serial).get(a.package_name),
                        install_source=resolve_preferred_source(
                            state.db, serial, a.package_name, installer=a.installer
                        ),
                        install_source_tooltip=install_source_tooltip(
                            resolve_preferred_source(
                                state.db,
                                serial,
                                a.package_name,
                                installer=a.installer,
                            ),
                            a.installer,
                        ),
                        update_ignored=a.package_name in ignored,
                    ).model_dump()
                    for a in state.get_apps(serial)
                ],
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")


@router.post("/api/updates/reinstall")
def reinstall_update(body: ReinstallUpdateIn, request: Request) -> StreamingResponse:
    """Uninstall then install — for signature mismatch. Wipes app data on device."""
    state = request.app.state.api
    serial = body.serial
    last_map: dict[str, UpdateInfo] = state.get_updates(serial)
    update = last_map.get(body.package_name)
    if update is None or update.remote is None:
        raise HTTPException(
            status_code=400,
            detail="Нет данных обновления в кеше — сначала проверьте обновления",
        )

    if not state.try_begin_job():
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        app_label = update.label or body.package_name
        try:
            known = {d.serial: d for d in state.devices.list_known()}
            device_label = known[serial].label if serial in known else serial
            item_id = f"{body.package_name}::{serial}"
            progress(
                {
                    "type": "install_plan",
                    "items": [
                        {
                            "id": item_id,
                            "package": body.package_name,
                            "app_label": app_label,
                            "serial": serial,
                            "device_label": device_label,
                            "status": "queued",
                            "detail": "Переустановка…",
                        }
                    ],
                    "message": f"Переустановка {app_label}",
                }
            )
            progress(
                {
                    "type": "install_item",
                    "id": item_id,
                    "status": "installing",
                    "detail": "Удаление старой версии (данные будут стёрты)…",
                }
            )
            ur = state.install.uninstall_on_device(serial, body.package_name)
            if not ur.success:
                # Already uninstalled is ok on some devices
                msg_l = (ur.message or "").lower()
                if "not installed" not in msg_l and "unknown package" not in msg_l:
                    progress(
                        {
                            "type": "install_item",
                            "id": item_id,
                            "status": "fail",
                            "detail": ur.message,
                        }
                    )
                    return {"type": "error", "message": ur.message}

            def dl_prog(m: str) -> None:
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "status": "downloading",
                        "detail": m,
                    }
                )

            try:
                _artifact, results = state.updates.download_and_install(
                    update,
                    [serial],
                    progress=dl_prog,
                    item_progress=lambda s, phase, detail: progress(
                        {
                            "type": "install_item",
                            "id": item_id,
                            "package": body.package_name,
                            "app_label": app_label,
                            "serial": s,
                            "device_label": device_label,
                            "status": phase,
                            "detail": detail,
                        }
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "status": "fail",
                        "detail": str(exc),
                    }
                )
                return {"type": "error", "message": str(exc)}

            r = results[0] if results else None
            if r and r.success:
                mapping = dict(state.get_updates(serial))
                old = mapping.get(body.package_name)
                if old and old.remote:
                    mapping[body.package_name] = UpdateInfo(
                        package_name=old.package_name,
                        label=old.label,
                        local_code=old.remote.version_code,
                        local_name=old.remote.version_name or old.local_name,
                        remote=old.remote,
                        skipped_play=old.skipped_play,
                        status="up_to_date",
                        error="",
                        stores=old.stores,
                        checked_sources=old.checked_sources,
                    )
                    state.set_updates(serial, mapping)
                state.db.clear_pending(
                    serial, kind="install", package_name=body.package_name
                )
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "status": "ok",
                        "detail": "Переустановлено",
                    }
                )
            elif r:
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "package": body.package_name,
                        "app_label": app_label,
                        "serial": serial,
                        "device_label": device_label,
                        "status": "fail",
                        **r.as_progress_fields(),
                    }
                )
            from app.services.update_ignores import list_ignored

            ignored = list_ignored(state.db, serial)
            return {
                "type": "done",
                "ok": bool(r and r.success),
                "message": r.message if r else "Нет результата",
                "apps": [
                    app_out(
                        a,
                        state.get_updates(serial).get(a.package_name),
                        install_source=resolve_preferred_source(
                            state.db, serial, a.package_name, installer=a.installer
                        ),
                        install_source_tooltip=install_source_tooltip(
                            resolve_preferred_source(
                                state.db,
                                serial,
                                a.package_name,
                                installer=a.installer,
                            ),
                            a.installer,
                        ),
                        update_ignored=a.package_name in ignored,
                    ).model_dump()
                    for a in state.get_apps(serial)
                ],
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")
