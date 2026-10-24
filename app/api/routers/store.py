from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import StoreInstallIn
from app.api.sse_job import stream_background_job
from app.providers.stores import STORE_TITLES
from app.services.store_search import StoreSearchService
from app.services.updates import UpdateInfo

router = APIRouter(tags=["store"])


@router.get("/api/store/search")
def store_search(
    request: Request,
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    query = (q or "").strip()
    if not query:
        return {"query": "", "results": []}
    state = request.app.state.api
    svc = StoreSearchService(state.updates.provider)
    enabled = state.updates.enabled_store_sources()
    results = svc.search(query, limit=limit, enabled=enabled)
    return {"query": query, "results": results}


@router.get("/api/store/app")
def store_app(
    request: Request,
    package: str = Query(..., min_length=1),
) -> dict:
    package_name = package.strip()
    state = request.app.state.api
    svc = StoreSearchService(state.updates.provider)
    enabled = state.updates.enabled_store_sources()
    card = svc.get_app_card(package_name, enabled=enabled)
    if card is None:
        raise HTTPException(status_code=404, detail="Приложение не найдено в включённых сторах")
    return card


@router.get("/api/store/versions")
def store_versions(
    request: Request,
    package: str = Query(..., min_length=1),
    source: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    package_name = package.strip()
    src = source.strip()
    state = request.app.state.api
    enabled = state.updates.enabled_store_sources()
    if src not in enabled:
        raise HTTPException(status_code=400, detail=f"Стор «{src}» выключен")
    svc = StoreSearchService(state.updates.provider)
    versions = svc.list_versions(package_name, source=src, limit=limit)
    return {
        "package_name": package_name,
        "source": src,
        "source_title": STORE_TITLES.get(src, src),
        "versions": versions,
    }


@router.post("/api/store/install")
def store_install(body: StoreInstallIn, request: Request) -> StreamingResponse:
    state = request.app.state.api
    package_name = (body.package_name or "").strip()
    source = (body.source or "").strip()
    if not package_name:
        raise HTTPException(status_code=400, detail="Укажите package_name")
    if not source:
        raise HTTPException(status_code=400, detail="Укажите source")
    enabled = state.updates.enabled_store_sources()
    if source not in enabled:
        raise HTTPException(status_code=400, detail=f"Стор «{source}» выключен")

    serials = [s.strip() for s in (body.serials or []) if s and str(s).strip()]
    if not serials:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одно устройство")

    known = state.devices.list_known()
    online = {d.serial for d in known if d.online}
    missing = [s for s in serials if s not in online]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Устройства офлайн: {', '.join(missing)}",
        )

    if not state.try_begin_job(label=f"store-install:{package_name}"):
        raise HTTPException(status_code=409, detail="Уже выполняется другая задача")

    version_code = body.version_code

    def run(progress):
        device_labels = {d.serial: d.label for d in known}
        plan_items = [
            {
                "id": f"{package_name}:{serial}",
                "package": package_name,
                "app_label": package_name,
                "serial": serial,
                "device_label": device_labels.get(serial, serial),
                "status": "queued",
                "detail": "Подготовка…",
            }
            for serial in serials
        ]

        def item_progress(serial: str, phase: str, detail: str) -> None:
            progress(
                {
                    "type": "install_item",
                    "id": f"{package_name}:{serial}",
                    "package": package_name,
                    "app_label": package_name,
                    "serial": serial,
                    "device_label": device_labels.get(serial, serial),
                    "status": phase,
                    "detail": detail or "",
                }
            )

        try:
            # Emit plan immediately so UI is never empty while resolving/downloading.
            progress(
                {
                    "type": "install_plan",
                    "message": (
                        f"Установка {package_name} "
                        f"({STORE_TITLES.get(source, source)})"
                    ),
                    "items": plan_items,
                }
            )

            provider = state.updates.provider
            for serial in serials:
                item_progress(serial, "downloading", "Поиск версии…")

            remote = provider.resolve_version(
                package_name, source=source, version_code=version_code
            )
            if remote is None:
                raise RuntimeError(
                    f"Версия не найдена в {STORE_TITLES.get(source, source)}"
                )
            if remote.unsupported_reason and not remote.download_url and not remote.app_id:
                raise RuntimeError(remote.unsupported_reason)

            update = UpdateInfo(
                package_name=package_name,
                label=package_name,
                local_code=0,
                local_name="",
                remote=remote,
                status="update",
                stores=(remote,),
                checked_sources=(source,),
            )

            for serial in serials:
                item_progress(serial, "downloading", "Скачивание…")

            artifact, results = state.updates.download_and_install(
                update,
                serials,
                progress=lambda m: progress({"type": "progress", "message": str(m)}),
                item_progress=item_progress,
            )

            ok = all(r.success for r in results)
            for r in results:
                payload = {
                    "type": "install_item",
                    "id": f"{package_name}:{r.serial}",
                    "package": package_name,
                    "app_label": package_name,
                    "serial": r.serial,
                    "device_label": device_labels.get(r.serial, r.serial),
                    "status": "ok" if r.success else "fail",
                    **r.as_progress_fields(),
                }
                if r.success and not payload.get("detail"):
                    payload["detail"] = "OK"
                progress(payload)
            return {
                "type": "done",
                "ok": ok,
                "message": (
                    f"Установлено {artifact.package_name} "
                    f"{artifact.version_name or artifact.version_code} "
                    f"из {STORE_TITLES.get(artifact.source, artifact.source)}"
                    if ok
                    else "Установка завершилась с ошибками"
                ),
                "package_name": package_name,
                "source": artifact.source,
                "version_code": artifact.version_code,
                "version_name": artifact.version_name,
            }
        finally:
            state.end_job()

    return StreamingResponse(stream_background_job(run), media_type="text/event-stream")
