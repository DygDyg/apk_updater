from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import Database

FLAG_SIGNATURE = "signature_mismatch"
FLAG_OLDER_SDK = "older_sdk"

_FLAG_LABELS = {
    FLAG_SIGNATURE: "подпись не совпала",
    FLAG_OLDER_SDK: "нужен новее Android",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def flag_label(flag: str) -> str:
    return _FLAG_LABELS.get(flag, flag or "")


def get_flag(db: Database, serial: str, package_name: str) -> dict[str, str] | None:
    row = db.fetchone(
        "SELECT flag, source, message FROM package_install_flags "
        "WHERE device_serial = ? AND package_name = ?",
        ((serial or "").strip(), (package_name or "").strip()),
    )
    if row is None:
        return None
    return {
        "flag": str(row["flag"] or ""),
        "source": str(row["source"] or ""),
        "message": str(row["message"] or ""),
    }


def list_flags(db: Database, serial: str) -> dict[str, dict[str, str]]:
    rows = db.fetchall(
        "SELECT package_name, flag, source, message FROM package_install_flags "
        "WHERE device_serial = ?",
        ((serial or "").strip(),),
    )
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        pkg = str(row["package_name"] or "")
        if not pkg:
            continue
        out[pkg] = {
            "flag": str(row["flag"] or ""),
            "source": str(row["source"] or ""),
            "message": str(row["message"] or ""),
        }
    return out


def set_flag(
    db: Database,
    serial: str,
    package_name: str,
    *,
    flag: str,
    source: str = "",
    message: str = "",
) -> None:
    ser = (serial or "").strip()
    pkg = (package_name or "").strip()
    fl = (flag or "").strip()
    if not ser or not pkg or not fl:
        return
    db.execute(
        "INSERT INTO package_install_flags("
        "device_serial, package_name, flag, source, message, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(device_serial, package_name) DO UPDATE SET "
        "flag=excluded.flag, source=excluded.source, "
        "message=excluded.message, updated_at=excluded.updated_at",
        (ser, pkg, fl, (source or "").strip(), (message or "").strip(), _utc_now()),
    )


def clear_flag(db: Database, serial: str, package_name: str) -> None:
    db.execute(
        "DELETE FROM package_install_flags WHERE device_serial = ? AND package_name = ?",
        ((serial or "").strip(), (package_name or "").strip()),
    )
