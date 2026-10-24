from __future__ import annotations

import json
from typing import Any

from app.db.database import Database
from app.providers.base import RemoteVersion
from app.services.apps import AppInfo
from app.services.updates import UpdateInfo


def _remote_to_dict(remote: RemoteVersion | None) -> dict[str, Any] | None:
    if remote is None:
        return None
    return {
        "package_name": remote.package_name,
        "version_name": remote.version_name,
        "version_code": int(remote.version_code),
        "source": remote.source,
        "download_url": remote.download_url,
        "app_id": remote.app_id,
        "unsupported_reason": remote.unsupported_reason,
    }


def _remote_from_dict(data: Any) -> RemoteVersion | None:
    if not data or not isinstance(data, dict):
        return None
    try:
        code = int(data.get("version_code") or 0)
    except (TypeError, ValueError):
        code = 0
    pkg = str(data.get("package_name") or "")
    src = str(data.get("source") or "")
    if not pkg or not src:
        return None
    return RemoteVersion(
        package_name=pkg,
        version_name=str(data.get("version_name") or ""),
        version_code=code,
        source=src,
        download_url=str(data.get("download_url") or ""),
        app_id=str(data.get("app_id") or ""),
        unsupported_reason=str(data.get("unsupported_reason") or ""),
    )


def _app_to_dict(app: AppInfo) -> dict[str, Any]:
    return {
        "package_name": app.package_name,
        "label": app.label,
        "version_name": app.version_name,
        "version_code": app.version_code,
        "installer": app.installer,
        "apk_path": app.apk_path,
        "icon_path": app.icon_path,
    }


def _app_from_dict(data: Any) -> AppInfo | None:
    if not data or not isinstance(data, dict):
        return None
    pkg = str(data.get("package_name") or "")
    if not pkg:
        return None
    return AppInfo(
        package_name=pkg,
        label=str(data.get("label") or ""),
        version_name=str(data.get("version_name") or ""),
        version_code=str(data.get("version_code") or ""),
        installer=str(data.get("installer") or "unknown"),
        apk_path=str(data.get("apk_path") or ""),
        icon_path=str(data.get("icon_path") or ""),
    )


def _update_to_dict(upd: UpdateInfo) -> dict[str, Any]:
    return {
        "package_name": upd.package_name,
        "label": upd.label,
        "local_code": int(upd.local_code),
        "local_name": upd.local_name,
        "remote": _remote_to_dict(upd.remote),
        "skipped_play": bool(upd.skipped_play),
        "status": upd.status,
        "error": upd.error,
        "stores": [_remote_to_dict(s) for s in upd.stores if s is not None],
        "checked_sources": list(upd.checked_sources),
    }


def _update_from_dict(data: Any) -> UpdateInfo | None:
    if not data or not isinstance(data, dict):
        return None
    pkg = str(data.get("package_name") or "")
    if not pkg:
        return None
    stores_raw = data.get("stores") or []
    stores: list[RemoteVersion] = []
    if isinstance(stores_raw, list):
        for item in stores_raw:
            remote = _remote_from_dict(item)
            if remote is not None:
                stores.append(remote)
    checked_raw = data.get("checked_sources") or []
    checked: tuple[str, ...] = ()
    if isinstance(checked_raw, list):
        checked = tuple(str(x) for x in checked_raw if x)
    try:
        local_code = int(data.get("local_code") or 0)
    except (TypeError, ValueError):
        local_code = 0
    return UpdateInfo(
        package_name=pkg,
        label=str(data.get("label") or ""),
        local_code=local_code,
        local_name=str(data.get("local_name") or ""),
        remote=_remote_from_dict(data.get("remote")),
        skipped_play=bool(data.get("skipped_play")),
        status=str(data.get("status") or ""),
        error=str(data.get("error") or ""),
        stores=tuple(stores),
        checked_sources=checked,
    )


def save_device_apps(db: Database, serial: str, apps: list[AppInfo]) -> None:
    payload = json.dumps([_app_to_dict(a) for a in apps], ensure_ascii=False)
    db.execute(
        "INSERT INTO device_ui_cache(serial, apps_json, updates_json, checked_at) "
        "VALUES(?, ?, '[]', 0) "
        "ON CONFLICT(serial) DO UPDATE SET apps_json = excluded.apps_json",
        (serial, payload),
    )


def save_device_updates(
    db: Database,
    serial: str,
    mapping: dict[str, UpdateInfo],
    checked_at: float,
) -> None:
    payload = json.dumps(
        {pkg: _update_to_dict(u) for pkg, u in mapping.items()},
        ensure_ascii=False,
    )
    db.execute(
        "INSERT INTO device_ui_cache(serial, apps_json, updates_json, checked_at) "
        "VALUES(?, '[]', ?, ?) "
        "ON CONFLICT(serial) DO UPDATE SET "
        "updates_json = excluded.updates_json, checked_at = excluded.checked_at",
        (serial, payload, float(checked_at)),
    )


def load_device_caches(
    db: Database,
) -> tuple[
    dict[str, list[AppInfo]],
    dict[str, dict[str, UpdateInfo]],
    dict[str, float],
]:
    apps_out: dict[str, list[AppInfo]] = {}
    updates_out: dict[str, dict[str, UpdateInfo]] = {}
    checked_out: dict[str, float] = {}
    for row in db.fetchall(
        "SELECT serial, apps_json, updates_json, checked_at FROM device_ui_cache"
    ):
        serial = str(row["serial"] or "")
        if not serial:
            continue
        try:
            apps_raw = json.loads(row["apps_json"] or "[]")
        except json.JSONDecodeError:
            apps_raw = []
        apps: list[AppInfo] = []
        if isinstance(apps_raw, list):
            for item in apps_raw:
                app = _app_from_dict(item)
                if app is not None:
                    apps.append(app)
        if apps:
            apps_out[serial] = apps

        try:
            upd_raw = json.loads(row["updates_json"] or "{}")
        except json.JSONDecodeError:
            upd_raw = {}
        mapping: dict[str, UpdateInfo] = {}
        if isinstance(upd_raw, dict):
            for pkg, item in upd_raw.items():
                upd = _update_from_dict(item)
                if upd is not None:
                    mapping[str(pkg)] = upd
        if mapping:
            updates_out[serial] = mapping
        try:
            checked = float(row["checked_at"] or 0)
        except (TypeError, ValueError):
            checked = 0.0
        if checked > 0:
            checked_out[serial] = checked
    return apps_out, updates_out, checked_out


def save_remotes(
    db: Database,
    remotes: dict[str, RemoteVersion | None],
    stores: dict[str, tuple[RemoteVersion, ...]],
    *,
    fetched_at: float,
    replace: bool,
) -> None:
    if replace:
        db.execute("DELETE FROM remotes_ui_cache")
    rows: list[tuple[str, str | None, str, float]] = []
    packages = set(remotes) | set(stores)
    for pkg in packages:
        best = remotes.get(pkg)
        store_hits = stores.get(pkg, ())
        rows.append(
            (
                pkg,
                json.dumps(_remote_to_dict(best), ensure_ascii=False)
                if best is not None
                else None,
                json.dumps(
                    [_remote_to_dict(s) for s in store_hits if s is not None],
                    ensure_ascii=False,
                ),
                float(fetched_at),
            )
        )
    if rows:
        db.executemany(
            "INSERT INTO remotes_ui_cache(package_name, best_json, stores_json, fetched_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(package_name) DO UPDATE SET "
            "best_json = excluded.best_json, stores_json = excluded.stores_json, "
            "fetched_at = excluded.fetched_at",
            rows,
        )
    db.set_setting("remotes_fetched_at", str(float(fetched_at)))


def load_remotes(
    db: Database,
) -> tuple[
    dict[str, RemoteVersion | None],
    dict[str, tuple[RemoteVersion, ...]],
    float,
]:
    remotes: dict[str, RemoteVersion | None] = {}
    stores: dict[str, tuple[RemoteVersion, ...]] = {}
    fetched_at = 0.0
    raw_ts = db.get_setting("remotes_fetched_at", "0") or "0"
    try:
        fetched_at = float(raw_ts)
    except ValueError:
        fetched_at = 0.0
    for row in db.fetchall(
        "SELECT package_name, best_json, stores_json, fetched_at FROM remotes_ui_cache"
    ):
        pkg = str(row["package_name"] or "")
        if not pkg:
            continue
        best_raw = row["best_json"]
        if best_raw:
            try:
                remotes[pkg] = _remote_from_dict(json.loads(best_raw))
            except json.JSONDecodeError:
                remotes[pkg] = None
        else:
            remotes[pkg] = None
        try:
            stores_raw = json.loads(row["stores_json"] or "[]")
        except json.JSONDecodeError:
            stores_raw = []
        hits: list[RemoteVersion] = []
        if isinstance(stores_raw, list):
            for item in stores_raw:
                remote = _remote_from_dict(item)
                if remote is not None:
                    hits.append(remote)
        stores[pkg] = tuple(hits)
        try:
            ts = float(row["fetched_at"] or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > fetched_at:
            fetched_at = ts
    return remotes, stores, fetched_at
