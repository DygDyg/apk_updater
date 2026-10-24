from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.providers.stores import STORE_ORDER, STORE_TITLES

if TYPE_CHECKING:
    from app.adb.client import AdbClient
    from app.db.database import Database
    from app.services.devices import DeviceService

log = logging.getLogger("apk_updater.package_sources")

# Android installerPackageName → our store id
INSTALLER_TO_STORE: dict[str, str] = {
    "ru.vk.store": "rustore",
    "ru.vk.store.tv": "rustore",
    "cm.aptoide.pt": "aptoide",
    "com.apkpure.aegon": "apkpure",
    "org.fdroid.fdroid": "fdroid",
    "org.fdroid.fdroid.privileged": "fdroid",
}

DEVICE_MARKER_PATHS = (
    "/sdcard/Android/data/apk_updater/package_sources.json",
    "/sdcard/apk_updater/package_sources.json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def installer_to_store(installer: str | None) -> str:
    raw = (installer or "").strip()
    if not raw or raw.lower() in {"null", "unknown", "adb", "com.android.shell", "com.android.packageinstaller"}:
        return ""
    return INSTALLER_TO_STORE.get(raw, "")


def source_label(source: str) -> str:
    if not source:
        return ""
    return STORE_TITLES.get(source, source)


def get_stored_source(db: Database, serial: str, package_name: str) -> str:
    row = db.fetchone(
        "SELECT source FROM package_sources WHERE device_serial = ? AND package_name = ?",
        (serial, package_name),
    )
    if row is None:
        return ""
    return str(row["source"] or "")


def resolve_preferred_source(
    db: Database,
    serial: str,
    package_name: str,
    *,
    installer: str = "",
) -> str:
    """DB row first, then installerPackageName heuristic."""
    stored = get_stored_source(db, serial, package_name)
    if stored:
        return stored
    return installer_to_store(installer)


def set_package_source(
    db: Database,
    serial: str,
    package_name: str,
    source: str,
    *,
    installer: str = "",
    manual: bool = False,
) -> None:
    src = (source or "").strip()
    if src and src not in STORE_ORDER:
        raise ValueError(f"Unknown store: {src}")
    db.execute(
        "INSERT INTO package_sources(device_serial, package_name, source, installer, manual, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(device_serial, package_name) DO UPDATE SET "
        "source=excluded.source, installer=excluded.installer, "
        "manual=excluded.manual, updated_at=excluded.updated_at",
        (serial, package_name, src, installer or "", 1 if manual else 0, _utc_now()),
    )


def delete_package_source(db: Database, serial: str, package_name: str) -> None:
    db.execute(
        "DELETE FROM package_sources WHERE device_serial = ? AND package_name = ?",
        (serial, package_name),
    )


def list_sources_for_device(db: Database, serial: str) -> dict[str, str]:
    rows = db.fetchall(
        "SELECT package_name, source FROM package_sources WHERE device_serial = ?",
        (serial,),
    )
    out: dict[str, str] = {}
    for row in rows:
        pkg = str(row["package_name"] or "")
        src = str(row["source"] or "")
        if pkg and src:
            out[pkg] = src
    return out


def install_source_tooltip(source: str, installer: str = "") -> str:
    if source:
        return f"Установлено из: {source_label(source)}"
    inst = (installer or "").strip()
    if inst and inst.lower() not in {"null", "unknown"}:
        mapped = installer_to_store(inst)
        if mapped:
            return f"Установлено из: {source_label(mapped)} (по installer)"
        return f"Установлено через: {inst}"
    return "Источник неизвестен"


def _push_text(adb: AdbClient, serial: str, remote: str, text: str) -> bool:
    parent = remote.rsplit("/", 1)[0]
    try:
        adb.shell(serial, f"mkdir -p '{parent}'", check=False, timeout=15.0)
    except Exception:  # noqa: BLE001
        pass
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as tmp:
        tmp.write(text)
        local = Path(tmp.name)
    try:
        # adb push
        result = adb.run(
            ["push", str(local.resolve()), remote],
            serial=serial,
            check=False,
            timeout=60.0,
        )
        return result.ok
    except Exception:  # noqa: BLE001
        log.exception("Failed to push package sources to %s", serial)
        return False
    finally:
        try:
            local.unlink(missing_ok=True)
        except OSError:
            pass


def _pull_text(adb: AdbClient, serial: str, remote: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="apkupd_src_") as tmp:
        local = Path(tmp) / "package_sources.json"
        try:
            result = adb.pull(serial, remote, local, timeout=60.0)
        except Exception:  # noqa: BLE001
            return None
        if not result.ok or not local.is_file() or local.stat().st_size == 0:
            return None
        try:
            return local.read_text(encoding="utf-8")
        except OSError:
            return None


def write_device_markers(
    adb: AdbClient,
    devices: DeviceService | None,
    serial: str,
    mapping: dict[str, str],
) -> None:
    """Push full source map for this device to phone storage."""
    working = serial
    if devices is not None:
        try:
            working = devices.resolve_online(serial) or serial
        except Exception:  # noqa: BLE001
            working = serial
    payload = json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True)
    for remote in DEVICE_MARKER_PATHS:
        if _push_text(adb, working, remote, payload):
            return
    log.warning("Could not write package source markers on %s", serial)


def read_device_markers(
    adb: AdbClient,
    devices: DeviceService | None,
    serial: str,
) -> dict[str, str]:
    working = serial
    if devices is not None:
        try:
            working = devices.resolve_online(serial) or serial
        except Exception:  # noqa: BLE001
            working = serial
    for remote in DEVICE_MARKER_PATHS:
        raw = _pull_text(adb, working, remote)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        out: dict[str, str] = {}
        for pkg, src in data.items():
            pkg_s = str(pkg or "").strip()
            src_s = str(src or "").strip()
            if pkg_s and src_s in STORE_ORDER:
                out[pkg_s] = src_s
        return out
    return {}


def sync_markers_from_device(
    db: Database,
    adb: AdbClient,
    devices: DeviceService | None,
    serial: str,
) -> int:
    """Import phone markers into DB where local row is missing. Returns import count."""
    remote_map = read_device_markers(adb, devices, serial)
    if not remote_map:
        return 0
    imported = 0
    for pkg, src in remote_map.items():
        existing = get_stored_source(db, serial, pkg)
        if existing:
            continue
        set_package_source(db, serial, pkg, src, manual=False)
        imported += 1
    return imported


def sync_markers_to_device(
    db: Database,
    adb: AdbClient,
    devices: DeviceService | None,
    serial: str,
) -> None:
    """Push current DB map to the phone (DB is source of truth after local edits)."""
    mapping = list_sources_for_device(db, serial)
    write_device_markers(adb, devices, serial, mapping)


def remember_install_source(
    db: Database,
    adb: AdbClient | None,
    devices: DeviceService | None,
    serial: str,
    package_name: str,
    source: str,
    *,
    installer: str = "",
) -> None:
    """After successful install: save to DB and refresh on-device mirror."""
    if not source:
        return
    set_package_source(
        db, serial, package_name, source, installer=installer, manual=False
    )
    if adb is None:
        return
    try:
        sync_markers_to_device(db, adb, devices, serial)
    except Exception:  # noqa: BLE001
        log.exception("Failed to sync source markers to device %s", serial)
