from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import AppUpdateApplyOut, AppUpdateCheckOut, MessageOut
from app.services import self_update

router = APIRouter(tags=["app-update"])


@router.get("/api/app-update/status", response_model=AppUpdateCheckOut)
def app_update_status(request: Request) -> AppUpdateCheckOut:
    db = request.app.state.api.db
    snap = self_update.status_snapshot(db)
    return AppUpdateCheckOut(
        ok=True,
        local_build=str(snap["build_id"]),
        latest_build=str(snap.get("latest_build") or ""),
        update_available=bool(snap.get("update_available")),
        download_url=(db.get_setting("app_update_download_url", "") or "")
        if snap.get("update_available")
        else "",
        message=str(snap.get("last_error") or ""),
        frozen=bool(snap.get("frozen")),
        github_repo=str(snap.get("github_repo") or ""),
        last_check=str(snap.get("last_check") or ""),
    )


@router.post("/api/app-update/check", response_model=AppUpdateCheckOut)
def app_update_check(request: Request) -> AppUpdateCheckOut:
    db = request.app.state.api.db
    result = self_update.check_for_update(persist_db=db)
    return AppUpdateCheckOut(
        ok=result.ok,
        local_build=result.local_build,
        latest_build=result.latest_build,
        update_available=result.update_available,
        download_url=result.download_url,
        message=result.message,
        frozen=result.frozen,
        github_repo=self_update.github_repo(),
        last_check=db.get_setting("app_update_last_check", "") or "",
    )


@router.post("/api/app-update/apply", response_model=AppUpdateApplyOut)
def app_update_apply(request: Request) -> AppUpdateApplyOut:
    db = request.app.state.api.db
    result = self_update.apply_update(db=db, delay_sec=5)
    if not result.ok and not result.restarting:
        raise HTTPException(status_code=400, detail=result.message)
    return AppUpdateApplyOut(
        ok=result.ok,
        message=result.message,
        restarting=result.restarting,
    )


@router.post("/api/app-update/dismiss", response_model=MessageOut)
def app_update_dismiss(request: Request) -> MessageOut:
    """Hide banner until next successful check finds a newer build."""
    db = request.app.state.api.db
    latest = (db.get_setting("app_update_latest_build", "") or "").strip()
    if latest:
        db.set_setting("app_update_dismissed_build", latest)
    return MessageOut(ok=True, message="Скрыто")
