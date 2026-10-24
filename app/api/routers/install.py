from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.api.schemas import InstallCachedIn
from app.api.sse_job import stream_background_job
from app.paths import CACHE_DIR

router = APIRouter(tags=["install"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _safe_cache_apk(path_str: str) -> Path:
    raw = Path(path_str)
    try:
        resolved = raw.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректный путь APK: {exc}") from exc
    cache_root = CACHE_DIR.resolve()
    try:
        resolved.relative_to(cache_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Разрешена установка только из кеша приложения"
        ) from exc
    if not resolved.is_file() or resolved.suffix.lower() != ".apk":
        raise HTTPException(status_code=400, detail="APK в кеше не найден")
    return resolved


@router.post("/api/install")
async def install_apk(
    request: Request,
    serials: str = Form(...),  # JSON array or comma-separated
    apk: UploadFile = File(...),
) -> StreamingResponse:
    state = request.app.state.api
    try:
        targets = json.loads(serials)
        if not isinstance(targets, list):
            raise ValueError("serials must be a JSON array")
        targets = [str(s) for s in targets]
    except (json.JSONDecodeError, ValueError):
        targets = [s.strip() for s in serials.split(",") if s.strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="Выберите устройства")

    suffix = Path(apk.filename or "app.apk").suffix or ".apk"
    if suffix.lower() != ".apk":
        raise HTTPException(status_code=400, detail="Нужен файл .apk")

    raw = await apk.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")

    tmp = Path(tempfile.gettempdir()) / f"apk_updater_upload_{int(time.time())}{suffix}"
    tmp.write_bytes(raw)

    def event_stream():
        events: list[str] = []
        result_box: list = []
        error_box: list[str] = []

        def worker() -> None:
            try:
                events.append(f"Установка {tmp.name}…")
                results = state.install.install_on_devices(tmp, targets)
                result_box.append(results)
            except Exception as exc:  # noqa: BLE001
                error_box.append(str(exc))
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        last_i = 0
        while thread.is_alive() or last_i < len(events):
            while last_i < len(events):
                msg = events[last_i]
                last_i += 1
                yield f"data: {json.dumps({'type': 'progress', 'message': msg}, ensure_ascii=False)}\n\n"
            time.sleep(0.1)
        thread.join(timeout=1)

        if error_box:
            yield f"data: {json.dumps({'type': 'error', 'message': error_box[0]}, ensure_ascii=False)}\n\n"
            return

        results = result_box[0] if result_box else []
        lines = []
        for r in results:
            mark = "OK" if r.success else "FAIL"
            lines.append(f"[{mark}] {r.serial}: {r.message}")
        payload = {
            "type": "done",
            "ok": all(r.success for r in results) if results else False,
            "message": "\n".join(lines) or "Нет результата",
            "results": [
                {"serial": r.serial, "success": r.success, "message": r.message} for r in results
            ],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/install/cached")
def install_cached(body: InstallCachedIn, request: Request) -> StreamingResponse:
    """Re-install an APK already in cache (e.g. retry with allow_downgrade)."""
    state = request.app.state.api
    serial = (body.serial or "").strip()
    if not serial:
        raise HTTPException(status_code=400, detail="Укажите serial")
    try:
        state.devices.resolve_online(serial)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    apk_path = _safe_cache_apk(body.apk_path)
    package_name = (body.package_name or "").strip() or apk_path.parent.name
    allow_downgrade = bool(body.allow_downgrade)

    if not state.try_begin_job(label=f"install-cached:{package_name}"):
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    def run(progress):
        try:
            known = {d.serial: d for d in state.devices.list_known()}
            device_label = known[serial].label if serial in known else serial
            item_id = f"{package_name}:{serial}"
            progress(
                {
                    "type": "install_plan",
                    "message": (
                        f"Установка с даунгрейдом {package_name}"
                        if allow_downgrade
                        else f"Повторная установка {package_name}"
                    ),
                    "items": [
                        {
                            "id": item_id,
                            "package": package_name,
                            "app_label": package_name,
                            "serial": serial,
                            "device_label": device_label,
                            "status": "queued",
                            "detail": "В очереди",
                        }
                    ],
                }
            )

            def item_progress(ser: str, phase: str, detail: str) -> None:
                progress(
                    {
                        "type": "install_item",
                        "id": item_id,
                        "package": package_name,
                        "app_label": package_name,
                        "serial": ser,
                        "device_label": device_label,
                        "status": phase,
                        "detail": detail,
                        "apk_path": str(apk_path),
                    }
                )

            results = state.install.install_on_devices(
                apk_path,
                [serial],
                item_progress=item_progress,
                allow_downgrade=allow_downgrade,
            )
            r = results[0] if results else None
            if r is None:
                raise RuntimeError("Нет результата установки")
            progress(
                {
                    "type": "install_item",
                    "id": item_id,
                    "package": package_name,
                    "app_label": package_name,
                    "serial": serial,
                    "device_label": device_label,
                    "status": "ok" if r.success else "fail",
                    **r.as_progress_fields(),
                }
            )
            return {
                "type": "done",
                "ok": r.success,
                "message": r.message,
                "package_name": package_name,
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")
