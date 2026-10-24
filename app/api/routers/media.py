from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.paths import CACHE_DIR, DATA_DIR, DEVICE_CACHE_DIR, ICON_CACHE_DIR

router = APIRouter(tags=["media"])

_ALLOWED_ROOTS = (
    DEVICE_CACHE_DIR.resolve(),
    ICON_CACHE_DIR.resolve(),
    CACHE_DIR.resolve(),
    (DATA_DIR / "cache").resolve(),
)


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved == root or root in resolved.parents for root in _ALLOWED_ROOTS)


@router.get("/api/media/file")
def media_file(path: str = Query(...)) -> FileResponse:
    target = Path(path)
    if not _is_allowed(target) or not target.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    media = "image/png"
    suf = target.suffix.lower()
    if suf in {".jpg", ".jpeg"}:
        media = "image/jpeg"
    elif suf == ".webp":
        media = "image/webp"
    elif suf == ".apk":
        media = "application/vnd.android.package-archive"
    return FileResponse(target, media_type=media)
