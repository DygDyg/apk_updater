from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.paths import CACHE_DIR, DEVICE_CACHE_DIR, ICON_CACHE_DIR, ensure_data_dirs

log = logging.getLogger("apk_updater.file_cache")

# Only these trees — never DB, tools, settings, logs
_CACHE_ROOTS = (
    ("apk", CACHE_DIR),
    ("icons", ICON_CACHE_DIR),
    ("devices", DEVICE_CACHE_DIR),
)

DEFAULT_APK_KEEP_DAYS = 2.0
MIN_APK_KEEP_DAYS = 0.25  # 6 hours
MAX_APK_KEEP_DAYS = 365.0


def _dir_stats(root: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) under root."""
    files = 0
    total = 0
    if not root.exists():
        return 0, 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return files, total


def _wipe_dir_contents(root: Path) -> tuple[int, int]:
    """Delete everything inside root; keep the directory itself."""
    files, bytes_ = _dir_stats(root)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return 0, 0
    for child in list(root.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass
    root.mkdir(parents=True, exist_ok=True)
    return files, bytes_


def purge_file_caches() -> dict[str, Any]:
    """Remove downloadable/file caches. Does not touch the SQLite DB or tools."""
    ensure_data_dirs()
    by_kind: dict[str, dict[str, int]] = {}
    total_files = 0
    total_bytes = 0
    for kind, path in _CACHE_ROOTS:
        n, b = _wipe_dir_contents(path)
        by_kind[kind] = {"files": n, "bytes": b}
        total_files += n
        total_bytes += b
    ensure_data_dirs()
    return {
        "ok": True,
        "files": total_files,
        "bytes": total_bytes,
        "by_kind": by_kind,
        "message": (
            f"Удалено файлов: {total_files}"
            f" ({_fmt_bytes(total_bytes)}). База данных не затронута."
        ),
    }


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def clamp_apk_keep_days(raw: float | str | None, default: float = DEFAULT_APK_KEEP_DAYS) -> float:
    try:
        days = float(raw) if raw is not None and raw != "" else default
    except (TypeError, ValueError):
        days = default
    return max(MIN_APK_KEEP_DAYS, min(MAX_APK_KEEP_DAYS, days))


def purge_expired_apks(keep_days: float, *, db=None) -> dict[str, Any]:
    """Delete APK files older than keep_days (by mtime). Icons/DB/tools untouched."""
    days = clamp_apk_keep_days(keep_days)
    cutoff = time.time() - days * 86400.0
    ensure_data_dirs()
    removed = 0
    freed = 0
    removed_paths: list[str] = []

    if CACHE_DIR.is_dir():
        for path in list(CACHE_DIR.rglob("*")):
            if not path.is_file():
                continue
            # APKs and leftover download artifacts under apk cache
            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except OSError:
                continue
            if mtime > cutoff:
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
                freed += size
                removed_paths.append(str(path))
            except OSError as exc:
                log.warning("Failed to delete expired cache file %s: %s", path, exc)

        # Drop empty package dirs
        for directory in sorted(
            (p for p in CACHE_DIR.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                pass

    if db is not None and removed_paths:
        try:
            for file_path in removed_paths:
                db.execute("DELETE FROM apk_cache WHERE file_path = ?", (file_path,))
        except Exception:  # noqa: BLE001
            log.exception("Failed to prune apk_cache rows after TTL purge")

    return {
        "ok": True,
        "files": removed,
        "bytes": freed,
        "keep_days": days,
        "message": (
            f"Устарело APK: удалено {removed} ({_fmt_bytes(freed)}), "
            f"срок хранения {days:g} дн."
            if removed
            else f"Устаревших APK нет (срок {days:g} дн.)"
        ),
    }
