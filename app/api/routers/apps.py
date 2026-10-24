from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request

from app.adb.client import AdbError
from app.api.schemas import (
    AppActionIn,
    AppActionOut,
    AppOut,
    AppPermissionIn,
    MessageOut,
    SetAppSourceIn,
    SetUpdateIgnoreIn,
    UpdateIgnoreOut,
)
from app.api.serialize import app_out
from app.providers.stores import STORE_ORDER, STORE_TITLES
from app.services.app_details import AppDetailsService, details_to_dict
from app.services.package_sources import (
    delete_package_source,
    install_source_tooltip,
    resolve_preferred_source,
    set_package_source,
    sync_markers_to_device,
)
from app.services.install_flags import clear_flag, get_flag, list_flags
from app.services.update_ignores import list_ignored, set_ignored

router = APIRouter(tags=["apps"])

_ACTIONS = frozenset(
    {"force_stop", "clear_cache", "clear_data", "uninstall", "open_settings"}
)


def _details_svc(request: Request) -> AppDetailsService:
    state = request.app.state.api
    return AppDetailsService(state.adb, devices=state.devices, install=state.install)


@router.get("/api/apps", response_model=list[AppOut])
def list_apps(
    request: Request,
    serial: str = Query(...),
    third_party_only: bool = Query(True),
) -> list[AppOut]:
    state = request.app.state.api
    working = ""
    try:
        working = state.devices.prepare_online(serial, hold_awake=True)
        try:
            from app.services.package_sources import sync_markers_from_device

            sync_markers_from_device(state.db, state.adb, state.devices, serial)
        except Exception:  # noqa: BLE001
            pass
        apps = state.apps.list_packages(
            working,
            third_party_only=third_party_only,
            enrich=True,
        )
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if working:
            state.devices.release_online(working)
    state.set_apps(serial, apps)
    mapping = state.get_updates(serial)
    ignored = list_ignored(state.db, serial)
    flags = list_flags(state.db, serial)
    out: list[AppOut] = []
    for a in apps:
        src = resolve_preferred_source(
            state.db, serial, a.package_name, installer=a.installer
        )
        tip = install_source_tooltip(src, a.installer)
        fl = flags.get(a.package_name) or {}
        out.append(
            app_out(
                a,
                mapping.get(a.package_name),
                install_source=src,
                install_source_tooltip=tip,
                update_ignored=a.package_name in ignored,
                install_flag=str(fl.get("flag") or ""),
                install_flag_source=str(fl.get("source") or ""),
            )
        )
    return out


@router.put("/api/apps/update-ignore", response_model=UpdateIgnoreOut)
def put_update_ignore(body: SetUpdateIgnoreIn, request: Request) -> UpdateIgnoreOut:
    state = request.app.state.api
    serial = (body.serial or "").strip()
    pkg = (body.package_name or "").strip()
    if not serial or not pkg:
        raise HTTPException(status_code=400, detail="serial и package_name обязательны")
    try:
        set_ignored(state.db, serial, pkg, ignored=bool(body.ignored))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_row: AppOut | None = None
    cached = next(
        (a for a in state.get_apps(serial) if a.package_name == pkg),
        None,
    )
    if cached is not None:
        src = resolve_preferred_source(
            state.db, serial, pkg, installer=cached.installer
        )
        tip = install_source_tooltip(src, cached.installer)
        fl = get_flag(state.db, serial, pkg) or {}
        app_row = app_out(
            cached,
            state.get_updates(serial).get(pkg),
            install_source=src,
            install_source_tooltip=tip,
            update_ignored=bool(body.ignored),
            install_flag=str(fl.get("flag") or ""),
            install_flag_source=str(fl.get("source") or ""),
        )
    msg = (
        f"{pkg}: обновления отключены"
        if body.ignored
        else f"{pkg}: обновления снова разрешены"
    )
    return UpdateIgnoreOut(message=msg, app=app_row)


@router.put("/api/apps/source", response_model=MessageOut)
def put_app_source(body: SetAppSourceIn, request: Request) -> MessageOut:
    state = request.app.state.api
    serial = (body.serial or "").strip()
    pkg = (body.package_name or "").strip()
    src = (body.source or "").strip()
    if not serial or not pkg:
        raise HTTPException(status_code=400, detail="serial и package_name обязательны")
    if src and src not in STORE_ORDER:
        raise HTTPException(status_code=400, detail=f"Неизвестный стор: {src}")
    try:
        if not src:
            delete_package_source(state.db, serial, pkg)
        else:
            set_package_source(state.db, serial, pkg, src, manual=True)
        # Changing store preference: allow auto-install to retry
        try:
            clear_flag(state.db, serial, pkg)
        except Exception:  # noqa: BLE001
            pass
        try:
            sync_markers_to_device(state.db, state.adb, state.devices, serial)
        except Exception:  # noqa: BLE001
            pass
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    label = src or "авто / неизвестно"
    return MessageOut(message=f"Источник для {pkg}: {label}")


@router.get("/api/apps/details")
def get_app_details(
    request: Request,
    serial: str = Query(...),
    package: str = Query(..., min_length=1),
) -> dict:
    state = request.app.state.api
    ser = (serial or "").strip()
    pkg = (package or "").strip()
    if not ser or not pkg:
        raise HTTPException(status_code=400, detail="serial и package обязательны")
    try:
        state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _details_svc(request)
    try:
        details = svc.get_details(ser, pkg)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Enrich label/icon from inventory cache when dumpsys lacks label
    cached = next(
        (a for a in state.get_apps(ser) if a.package_name == pkg),
        None,
    )
    payload = details_to_dict(details)
    if cached:
        if not payload.get("label") or payload["label"] == pkg:
            payload["label"] = cached.label or pkg
        if cached.icon_path and Path(cached.icon_path).is_file():
            payload["icon_url"] = f"/api/media/file?path={quote(cached.icon_path, safe='')}"
        else:
            payload["icon_url"] = None
        if not payload.get("installer") or payload["installer"] == "unknown":
            payload["installer"] = cached.installer or payload.get("installer") or ""
    else:
        payload["icon_url"] = None

    src = resolve_preferred_source(
        state.db, ser, pkg, installer=str(payload.get("installer") or "")
    )
    payload["install_source"] = src
    payload["install_source_label"] = STORE_TITLES.get(src, src) if src else ""
    payload["install_source_tooltip"] = install_source_tooltip(
        src, str(payload.get("installer") or "")
    )
    return payload


@router.post("/api/apps/action", response_model=AppActionOut)
def app_action(body: AppActionIn, request: Request) -> AppActionOut:
    state = request.app.state.api
    ser = (body.serial or "").strip()
    pkg = (body.package_name or "").strip()
    action = (body.action or "").strip()
    if not ser or not pkg:
        raise HTTPException(status_code=400, detail="serial и package_name обязательны")
    if action not in _ACTIONS:
        raise HTTPException(status_code=400, detail=f"Неизвестное действие: {action}")
    try:
        state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _details_svc(request)
    try:
        if action == "force_stop":
            ok, message = svc.force_stop(ser, pkg)
        elif action == "clear_cache":
            ok, message = svc.clear_cache(ser, pkg)
        elif action == "clear_data":
            ok, message = svc.clear_data(ser, pkg)
        elif action == "uninstall":
            ok, message = svc.uninstall(ser, pkg)
            if ok:
                # Drop from in-memory / persisted app lists
                apps = [a for a in state.get_apps(ser) if a.package_name != pkg]
                state.set_apps(ser, apps)
                updates = dict(state.get_updates(ser))
                updates.pop(pkg, None)
                state.set_updates(ser, updates)
                try:
                    delete_package_source(state.db, ser, pkg)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    set_ignored(state.db, ser, pkg, ignored=False)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    clear_flag(state.db, ser, pkg)
                except Exception:  # noqa: BLE001
                    pass
        else:  # open_settings
            ok, message = svc.open_settings(ser, pkg)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    details = None
    if ok and action in ("clear_cache", "clear_data"):
        try:
            details = details_to_dict(svc.get_details(ser, pkg))
        except AdbError:
            details = None
    return AppActionOut(ok=ok, message=message, details=details)


@router.post("/api/apps/permission", response_model=AppActionOut)
def app_permission(body: AppPermissionIn, request: Request) -> AppActionOut:
    state = request.app.state.api
    ser = (body.serial or "").strip()
    pkg = (body.package_name or "").strip()
    perm = (body.permission or "").strip()
    if not ser or not pkg or not perm:
        raise HTTPException(
            status_code=400, detail="serial, package_name и permission обязательны"
        )
    try:
        state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _details_svc(request)
    try:
        ok, message = svc.set_permission(ser, pkg, perm, granted=body.granted)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    details = None
    if ok:
        try:
            details = details_to_dict(svc.get_details(ser, pkg))
        except AdbError:
            details = None
    return AppActionOut(ok=ok, message=message, details=details)
