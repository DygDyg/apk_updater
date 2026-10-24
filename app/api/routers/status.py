from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.schemas import AdbPathIn, MessageOut, StatusOut
from app.runtime_control import RUNTIME

router = APIRouter(tags=["status"])

# Simple LAN guard — change via env APK_UPDATER_RESTART_KEY if needed.
_DEFAULT_RESTART_KEY = "restart"


def _restart_key() -> str:
    import os

    return (os.environ.get("APK_UPDATER_RESTART_KEY") or _DEFAULT_RESTART_KEY).strip()


@router.get("/api/status", response_model=StatusOut)
def get_status(request: Request) -> StatusOut:
    from app.services import self_update

    state = request.app.state.api
    ok, msg = state.devices.check_adb()
    job = state.job_status()
    snap = self_update.status_snapshot(state.db)
    return StatusOut(
        adb_available=ok,
        adb_path=state.devices.get_adb_path() or "",
        adb_message=msg,
        job_running=bool(job.get("job_running")),
        job_label=str(job.get("job_label") or ""),
        job_age_sec=float(job.get("job_age_sec") or 0),
        build_id=str(snap.get("build_id") or "dev"),
        latest_build=str(snap.get("latest_build") or ""),
        update_available=bool(snap.get("update_available")),
        app_frozen=bool(snap.get("frozen")),
        github_repo=str(snap.get("github_repo") or ""),
    )


@router.get("/api/restart", response_model=MessageOut)
def restart_app(
    key: str = Query("", description="Must match restart key (default: restart)"),
    delay: int = Query(
        4,
        ge=2,
        le=120,
        description="Seconds before the new process starts (use 20-40 for deploy)",
    ),
) -> MessageOut:
    """Stop now; a helper starts a new process after ``delay`` seconds.

    Deploy flow: call with delay=30, then copy files, then wait for /api/status.

    Example: GET http://192.168.1.150:8765/api/restart?key=restart&delay=30
    """
    expected = _restart_key()
    if expected and (key or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Bad key")
    if not RUNTIME.request_restart(delay_sec=delay):
        return MessageOut(ok=True, message="Restart already in progress")
    return MessageOut(
        ok=True,
        message=f"Stopping now; new process in ~{delay}s",
    )


@router.post("/api/adb/path", response_model=MessageOut)
def set_adb_path(body: AdbPathIn, request: Request) -> MessageOut:
    state = request.app.state.api
    state.devices.set_adb_path(body.path.strip())
    ok, msg = state.devices.check_adb()
    return MessageOut(ok=ok, message=msg)


@router.post("/api/adb/ensure", response_model=MessageOut)
def ensure_adb(request: Request) -> MessageOut:
    state = request.app.state.api
    ok, msg = state.devices.ensure_adb(download=True)
    return MessageOut(ok=ok, message=msg)
