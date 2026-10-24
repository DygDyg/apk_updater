from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_ignored(db: Database, serial: str, package_name: str) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM update_ignores WHERE device_serial = ? AND package_name = ?",
        ((serial or "").strip(), (package_name or "").strip()),
    )
    return row is not None


def list_ignored(db: Database, serial: str) -> set[str]:
    rows = db.fetchall(
        "SELECT package_name FROM update_ignores WHERE device_serial = ?",
        ((serial or "").strip(),),
    )
    return {str(r["package_name"] or "") for r in rows if r["package_name"]}


def set_ignored(
    db: Database, serial: str, package_name: str, *, ignored: bool
) -> None:
    ser = (serial or "").strip()
    pkg = (package_name or "").strip()
    if not ser or not pkg:
        raise ValueError("serial и package_name обязательны")
    if ignored:
        db.execute(
            "INSERT INTO update_ignores(device_serial, package_name, updated_at) "
            "VALUES(?, ?, ?) "
            "ON CONFLICT(device_serial, package_name) DO UPDATE SET "
            "updated_at=excluded.updated_at",
            (ser, pkg, _utc_now()),
        )
    else:
        db.execute(
            "DELETE FROM update_ignores WHERE device_serial = ? AND package_name = ?",
            (ser, pkg),
        )
