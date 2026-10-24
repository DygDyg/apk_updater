from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.adb.client import AdbClient
from app.api.routers import (
    app_update,
    apps,
    devices,
    install,
    media,
    qr,
    settings,
    status,
    store,
    updates,
)
from app.api.state import AppState
from app.db.database import Database
from app.paths import WEB_DIST_DIR, ensure_data_dirs
from app.services.apps import AppInventoryService
from app.services.cache import ApkCache
from app.services.devices import DeviceService
from app.services.install import InstallService
from app.services.background import BackgroundWorker
from app.services.updates import UpdateService

WEB_DIST = WEB_DIST_DIR


def create_app(
    *,
    db: Database | None = None,
    adb: AdbClient | None = None,
) -> FastAPI:
    ensure_data_dirs()
    database = db or Database()
    client = adb or AdbClient()
    cache = ApkCache()
    devices_svc = DeviceService(database, client)
    apps_svc = AppInventoryService(client)
    install_svc = InstallService(database, client, devices=devices_svc)
    updates_svc = UpdateService(database, install_svc, cache)

    api_state = AppState(
        db=database,
        adb=client,
        devices=devices_svc,
        apps=apps_svc,
        install=install_svc,
        updates=updates_svc,
        cache=cache,
    )
    api_state.load_persisted_cache()
    bg_worker = BackgroundWorker(api_state)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.api = api_state
        bg_worker.start()
        try:
            yield
        finally:
            bg_worker.stop()
            database.close()

    app = FastAPI(title="APK Updater", lifespan=lifespan)
    app.state.api = api_state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        ],
        # Phone / other PC on LAN opening http://192.168.x.x:8765 (or Vite on LAN)
        allow_origin_regex=(
            r"https?://("
            r"localhost|127\.0\.0\.1|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
            r")(:\d+)?"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(status.router)
    app.include_router(app_update.router)
    app.include_router(settings.router)
    app.include_router(devices.router)
    app.include_router(qr.router)
    app.include_router(apps.router)
    app.include_router(updates.router)
    app.include_router(install.router)
    app.include_router(store.router)
    app.include_router(media.router)

    if WEB_DIST.is_dir():
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api":
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Not found")
            candidate = WEB_DIST / full_path
            try:
                resolved = candidate.resolve()
                if candidate.is_file() and (
                    resolved == WEB_DIST.resolve() or WEB_DIST.resolve() in resolved.parents
                ):
                    return FileResponse(candidate)
            except OSError:
                pass
            return FileResponse(WEB_DIST / "index.html")

    return app
