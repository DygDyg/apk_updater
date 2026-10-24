from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.adb.client import AdbError
from app.api.schemas import (
    ConnectIn,
    DeviceActionIn,
    DeviceActionOut,
    DeviceInfoOut,
    DeviceLiveOut,
    DeviceOut,
    DeviceScreenshotOut,
    DeviceScrcpyOut,
    DiscoverActionIn,
    DiscoverOut,
    MessageOut,
    PairIn,
    SerialIn,
)
from app.api.serialize import device_out, discover_out
from app.services.device_actions import DeviceActionsService, cached_screenshot_url
from app.services.discovery import DiscoveredEndpoint, EndpointKind
from app.services.scrcpy_service import launch_scrcpy

router = APIRouter(tags=["devices"])


def _actions_svc(request: Request) -> DeviceActionsService:
    return DeviceActionsService(request.app.state.api.devices.adb)


def _find_device(request: Request, serial: str) -> DeviceOut:
    state = request.app.state.api
    ser = (serial or "").strip()
    for rec in state.devices.list_known():
        if rec.serial == ser or ser in (rec.transports or []):
            return device_out(rec)
    raise HTTPException(status_code=404, detail="Устройство не найдено")


@router.get("/api/devices", response_model=list[DeviceOut])
def list_devices(request: Request) -> list[DeviceOut]:
    state = request.app.state.api
    return [device_out(r) for r in state.devices.list_known()]


@router.post("/api/devices/refresh", response_model=list[DeviceOut])
def refresh_devices(request: Request) -> list[DeviceOut]:
    state = request.app.state.api
    records = state.devices.refresh(with_profiles=True)
    return [device_out(r) for r in records]


@router.post("/api/devices/discover", response_model=list[DiscoverOut])
def discover(request: Request) -> list[DiscoverOut]:
    state = request.app.state.api
    found = state.devices.discover_network(duration=4.0)
    labels = state.devices.resolve_discovered_labels(found, probe_unknown=True)
    return [discover_out(ep, label=labels.get(ep.key, "")) for ep in found]


@router.post("/api/devices/pair", response_model=MessageOut)
def pair(body: PairIn, request: Request) -> MessageOut:
    state = request.app.state.api
    try:
        msg = state.devices.pair(body.ip, body.port, body.code)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageOut(message=msg)


@router.post("/api/devices/connect", response_model=MessageOut)
def connect(body: ConnectIn, request: Request) -> MessageOut:
    state = request.app.state.api
    try:
        msg = state.devices.connect(body.ip, body.port)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageOut(message=msg)


@router.post("/api/devices/disconnect", response_model=MessageOut)
def disconnect(body: SerialIn, request: Request) -> MessageOut:
    state = request.app.state.api
    try:
        msg = state.devices.disconnect(body.serial)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageOut(message=msg)


@router.post("/api/devices/remove", response_model=MessageOut)
def remove(body: SerialIn, request: Request) -> MessageOut:
    state = request.app.state.api
    state.devices.remove(body.serial)
    return MessageOut(message="Удалено из реестра")


@router.post("/api/devices/discovered/action", response_model=MessageOut)
def discovered_action(body: DiscoverActionIn, request: Request) -> MessageOut:
    state = request.app.state.api
    try:
        kind = EndpointKind(body.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown endpoint kind") from exc
    ep = DiscoveredEndpoint(kind=kind, name="", ip=body.ip, port=body.port, full_name="")
    try:
        if kind == EndpointKind.PAIRING:
            if not body.code.strip():
                raise HTTPException(status_code=400, detail="Нужен 6-значный код")
            msg = state.devices.pair_discovered(ep, body.code.strip())
        else:
            msg = state.devices.connect_discovered(ep)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageOut(message=msg)


@router.post("/api/devices/profile", response_model=DeviceOut)
def fetch_profile(body: SerialIn, request: Request) -> DeviceOut:
    state = request.app.state.api
    try:
        state.devices.fetch_profile(body.serial, refresh_preview=True)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _find_device(request, body.serial)


@router.get("/api/devices/info", response_model=DeviceInfoOut)
def device_info(request: Request, serial: str = Query(...)) -> DeviceInfoOut:
    state = request.app.state.api
    ser = (serial or "").strip()
    if not ser:
        raise HTTPException(status_code=400, detail="serial обязателен")
    try:
        working = state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    device = _find_device(request, ser)
    svc = _actions_svc(request)
    shot = cached_screenshot_url(ser, working, device.serial, *(device.transports or []))
    try:
        svc.ensure_ready(working)
        live = svc.snapshot(working)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceInfoOut(
        device=device,
        live=DeviceLiveOut(**live.to_dict()),
        screenshot_url=shot,
    )


@router.post("/api/devices/action", response_model=DeviceActionOut)
def device_action(body: DeviceActionIn, request: Request) -> DeviceActionOut:
    state = request.app.state.api
    ser = (body.serial or "").strip()
    if not ser:
        raise HTTPException(status_code=400, detail="serial обязателен")
    try:
        working = state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _actions_svc(request)
    try:
        ok, message = svc.run_action(working, body.action)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceActionOut(ok=ok, message=message)


@router.post("/api/devices/screenshot", response_model=DeviceScreenshotOut)
def device_screenshot(body: SerialIn, request: Request) -> DeviceScreenshotOut:
    state = request.app.state.api
    ser = (body.serial or "").strip()
    if not ser:
        raise HTTPException(status_code=400, detail="serial обязателен")
    try:
        working = state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _actions_svc(request)
    try:
        _path, url = svc.capture_screenshot(working, also_as=ser)
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return DeviceScreenshotOut(ok=True, message="Скриншот готов", url=url)


@router.post("/api/devices/scrcpy", response_model=DeviceScrcpyOut)
def device_scrcpy(body: SerialIn, request: Request) -> DeviceScrcpyOut:
    """Launch scrcpy for the device; download win64 build if missing."""
    state = request.app.state.api
    ser = (body.serial or "").strip()
    if not ser:
        raise HTTPException(status_code=400, detail="serial обязателен")
    try:
        working = state.devices.resolve_online(ser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    label = ""
    try:
        label = _find_device(request, ser).label
    except HTTPException:
        label = working

    try:
        ok, message = launch_scrcpy(
            state.devices.adb,
            working,
            download=True,
            title=f"APK Updater · {label or working}",
        )
    except AdbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceScrcpyOut(ok=ok, message=message)
