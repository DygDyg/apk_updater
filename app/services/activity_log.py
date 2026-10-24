from __future__ import annotations

import time
from typing import Any

from app.db.database import Database

# Keep the journal bounded
_MAX_ROWS = 400

# Progress lines that flood the UI during store fetch
_SKIP_PREFIXES = ("Сторы (",)


def append(
    db: Database,
    *,
    kind: str,
    event: str,
    status: str = "info",
    message: str = "",
    package_name: str = "",
    app_label: str = "",
    serial: str = "",
    device_label: str = "",
    detail: str = "",
) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO bg_activity_log("
        "created_at, kind, event, status, message, package_name, app_label, "
        "serial, device_label, detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            now,
            kind,
            event,
            status,
            message or "",
            package_name or "",
            app_label or "",
            serial or "",
            device_label or "",
            detail or "",
        ),
    )
    _trim(db)
    try:
        from app.services import update_file_log

        bits = [
            f"{kind}/{event}",
            status,
            message or "",
        ]
        if device_label or serial:
            bits.append(f"dev={device_label or serial}")
        if package_name:
            bits.append(f"pkg={package_name}")
        if app_label and app_label != package_name:
            bits.append(f"app={app_label}")
        if detail:
            bits.append(f"detail={detail}")
        line = " | ".join(b for b in bits if b)
        level = 20  # INFO
        if status == "fail":
            level = 40  # ERROR
        elif status == "skip":
            level = 20
        update_file_log.write(line, level=level)
    except Exception:  # noqa: BLE001
        pass


def _trim(db: Database) -> None:
    row = db.fetchone("SELECT COUNT(*) AS c FROM bg_activity_log")
    count = int(row["c"]) if row else 0
    if count <= _MAX_ROWS:
        return
    drop = count - _MAX_ROWS
    db.execute(
        "DELETE FROM bg_activity_log WHERE id IN ("
        "SELECT id FROM bg_activity_log ORDER BY created_at ASC, id ASC LIMIT ?"
        ")",
        (drop,),
    )


def list_entries(db: Database, *, limit: int = 200) -> list[dict[str, Any]]:
    lim = max(1, min(500, int(limit)))
    rows = db.fetchall(
        "SELECT id, created_at, kind, event, status, message, package_name, "
        "app_label, serial, device_label, detail "
        "FROM bg_activity_log ORDER BY created_at DESC, id DESC LIMIT ?",
        (lim,),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "created_at": float(r["created_at"] or 0),
                "kind": str(r["kind"] or ""),
                "event": str(r["event"] or ""),
                "status": str(r["status"] or "info"),
                "message": str(r["message"] or ""),
                "package_name": str(r["package_name"] or ""),
                "app_label": str(r["app_label"] or ""),
                "serial": str(r["serial"] or ""),
                "device_label": str(r["device_label"] or ""),
                "detail": str(r["detail"] or ""),
            }
        )
    return out


def clear(db: Database) -> None:
    db.execute("DELETE FROM bg_activity_log")


def make_progress(db: Database, kind: str, *, also_log=None):
    """Wrap batch progress: persist meaningful events, forward to also_log."""

    def progress(m: str | dict) -> None:
        if also_log:
            try:
                also_log(m)
            except Exception:  # noqa: BLE001
                pass
        if isinstance(m, dict):
            t = m.get("type")
            if t == "install_plan":
                append(
                    db,
                    kind=kind,
                    event="plan",
                    status="info",
                    message=str(m.get("message") or "План установки"),
                    detail=f"items={len(m.get('items') or [])}",
                )
                return
            if t == "install_item":
                st = str(m.get("status") or "")
                if st not in {"ok", "fail"}:
                    return
                append(
                    db,
                    kind=kind,
                    event="install",
                    status=st,
                    message=str(m.get("app_label") or m.get("package") or ""),
                    package_name=str(m.get("package") or ""),
                    app_label=str(m.get("app_label") or ""),
                    serial=str(m.get("serial") or ""),
                    device_label=str(m.get("device_label") or ""),
                    detail=str(m.get("detail") or ""),
                )
                return
            return
        text = str(m or "").strip()
        if not text:
            return
        if any(text.startswith(p) for p in _SKIP_PREFIXES):
            return
        append(
            db,
            kind=kind,
            event="progress",
            status="info",
            message=text,
        )

    return progress
