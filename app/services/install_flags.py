from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.db.database import Database

if TYPE_CHECKING:
    from app.services.updates import UpdateInfo

FLAG_SIGNATURE = "signature_mismatch"
FLAG_OLDER_SDK = "older_sdk"

# Failures that must not be retried by auto/batch install until cleared
AUTO_BLOCK_FLAGS = frozenset({FLAG_SIGNATURE, FLAG_OLDER_SDK})

_FLAG_LABELS = {
    FLAG_SIGNATURE: "подпись не совпала",
    FLAG_OLDER_SDK: "нужен новее Android",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def flag_label(flag: str) -> str:
    return _FLAG_LABELS.get(flag, flag or "")


def flag_blocks_auto(flag: str) -> bool:
    return (flag or "").strip() in AUTO_BLOCK_FLAGS


def get_flag(db: Database, serial: str, package_name: str) -> dict[str, str] | None:
    row = db.fetchone(
        "SELECT flag, source, message, version_code FROM package_install_flags "
        "WHERE device_serial = ? AND package_name = ?",
        ((serial or "").strip(), (package_name or "").strip()),
    )
    if row is None:
        return None
    return _row_to_flag(row)


def list_flags(db: Database, serial: str) -> dict[str, dict[str, str]]:
    rows = db.fetchall(
        "SELECT package_name, flag, source, message, version_code FROM package_install_flags "
        "WHERE device_serial = ?",
        ((serial or "").strip(),),
    )
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        pkg = str(row["package_name"] or "")
        if not pkg:
            continue
        out[pkg] = _row_to_flag(row)
    return out


def _row_to_flag(row) -> dict[str, str]:
    vc = row["version_code"] if "version_code" in row.keys() else 0
    try:
        vc_s = str(int(vc or 0))
    except (TypeError, ValueError):
        vc_s = "0"
    return {
        "flag": str(row["flag"] or ""),
        "source": str(row["source"] or ""),
        "message": str(row["message"] or ""),
        "version_code": vc_s,
    }


def set_flag(
    db: Database,
    serial: str,
    package_name: str,
    *,
    flag: str,
    source: str = "",
    message: str = "",
    version_code: int = 0,
) -> None:
    ser = (serial or "").strip()
    pkg = (package_name or "").strip()
    fl = (flag or "").strip()
    if not ser or not pkg or not fl:
        return
    try:
        vc = int(version_code or 0)
    except (TypeError, ValueError):
        vc = 0
    db.execute(
        "INSERT INTO package_install_flags("
        "device_serial, package_name, flag, source, message, version_code, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(device_serial, package_name) DO UPDATE SET "
        "flag=excluded.flag, source=excluded.source, "
        "message=excluded.message, version_code=excluded.version_code, "
        "updated_at=excluded.updated_at",
        (ser, pkg, fl, (source or "").strip(), (message or "").strip(), vc, _utc_now()),
    )


def clear_flag(db: Database, serial: str, package_name: str) -> None:
    db.execute(
        "DELETE FROM package_install_flags WHERE device_serial = ? AND package_name = ?",
        ((serial or "").strip(), (package_name or "").strip()),
    )


def refresh_flags_after_check(
    db: Database,
    serial: str,
    mapping: dict[str, UpdateInfo],
) -> list[str]:
    """Drop install flags when the blocked remote build is gone or changed.

    Returns package names whose flags were cleared.
    """
    flags = list_flags(db, serial)
    cleared: list[str] = []
    for pkg, fl in flags.items():
        if not flag_blocks_auto(fl.get("flag") or ""):
            continue
        upd = mapping.get(pkg)
        try:
            flagged_vc = int(fl.get("version_code") or 0)
        except ValueError:
            flagged_vc = 0
        remote_vc = 0
        if upd and upd.remote is not None:
            try:
                remote_vc = int(upd.remote.version_code or 0)
            except (TypeError, ValueError):
                remote_vc = 0

        # Local caught up / no longer an update candidate
        if upd is None or (not upd.has_update and not upd.manual_update):
            clear_flag(db, serial, pkg)
            cleared.append(pkg)
            continue
        # Store offered a different build — worth retrying auto-install
        if flagged_vc and remote_vc and remote_vc != flagged_vc:
            clear_flag(db, serial, pkg)
            cleared.append(pkg)
            continue
        # Source preference moved away from the failing store build
        flagged_src = (fl.get("source") or "").strip()
        remote_src = (upd.remote.source if upd.remote else "") or ""
        if flagged_src and remote_src and flagged_src != remote_src:
            clear_flag(db, serial, pkg)
            cleared.append(pkg)
    return cleared
