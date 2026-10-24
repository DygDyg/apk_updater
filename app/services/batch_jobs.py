from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.adb.client import AdbError
from app.api.serialize import app_out
from app.api.state import AppState
from app.services.updates import PLAY_INSTALLER, UpdateInfo

# str message or structured dict for SSE
ProgressCb = Callable[[str | dict], None]


@dataclass
class BatchCheckResult:
    devices: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pending_serials: list[str] = field(default_factory=list)
    cached: bool = False
    message: str = ""


@dataclass
class BatchInstallResult:
    ok: int = 0
    fail: int = 0
    pending_serials: list[str] = field(default_factory=list)
    message: str = ""
    details: list[str] = field(default_factory=list)


def _hide_play(state: AppState) -> bool:
    return state.hide_play_store()


def _apps_payload(state: AppState, serial: str) -> list[dict[str, Any]]:
    from app.services.install_flags import list_flags
    from app.services.package_sources import (
        install_source_tooltip,
        resolve_preferred_source,
    )
    from app.services.update_ignores import list_ignored

    apps = state.get_apps(serial)
    mapping = state.get_updates(serial)
    ignored = list_ignored(state.db, serial)
    flags = list_flags(state.db, serial)
    hide = _hide_play(state)
    out: list[dict[str, Any]] = []
    for app in apps:
        upd = mapping.get(app.package_name)
        if hide and upd and (upd.skipped_play or upd.status == "play"):
            pass
        src = resolve_preferred_source(
            state.db, serial, app.package_name, installer=app.installer
        )
        tip = install_source_tooltip(src, app.installer)
        fl = flags.get(app.package_name) or {}
        out.append(
            app_out(
                app,
                upd,
                install_source=src,
                install_source_tooltip=tip,
                update_ignored=app.package_name in ignored,
                install_flag=str(fl.get("flag") or ""),
                install_flag_source=str(fl.get("source") or ""),
            ).model_dump()
        )
    return out


def cache_snapshot(state: AppState) -> dict[str, Any]:
    devices: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    # Prefer known device serials from registry + any keys in apps_cache
    serials: set[str] = set()
    try:
        for d in state.devices.list_known():
            serials.add(d.serial)
    except Exception:  # noqa: BLE001
        pass
    serials.update(state.apps_cache.keys())
    # Drop duplicate transport keys that resolve to same logical — keep apps_cache keys that have data
    for serial in sorted(serials):
        apps = state.get_apps(serial)
        if not apps and serial not in state.last_updates:
            continue
        payload = _apps_payload(state, serial)
        if not payload and not apps:
            continue
        devices[serial] = payload
        counts[serial] = sum(1 for a in payload if a.get("has_update"))
    return {
        "devices": devices,
        "update_counts": counts,
        "pending_serials": state.db.list_pending_serials(),
        "bg_updates_last_run": state.db.get_setting("bg_updates_last_run", "") or "",
        "auto_install_last_run": state.db.get_setting("auto_install_last_run", "") or "",
    }


def run_check_batch(
    state: AppState,
    *,
    force: bool = False,
    serials: list[str] | None = None,
    progress: ProgressCb | None = None,
    record_bg_run: bool = False,
) -> BatchCheckResult:
    def prog(msg: str) -> None:
        if progress:
            progress(msg)

    known = state.devices.list_known()
    by_serial = {d.serial: d for d in known}
    if serials is None:
        targets = [d.serial for d in known]
    else:
        targets = list(serials)

    if not targets:
        return BatchCheckResult(message="Нет устройств в реестре")

    online_targets = [s for s in targets if by_serial.get(s) and by_serial[s].online]
    if not force and online_targets and all(state.updates_fresh(s) for s in online_targets):
        devices = {s: _apps_payload(state, s) for s in targets if state.get_apps(s)}
        return BatchCheckResult(
            devices=devices,
            pending_serials=state.db.list_pending_serials(),
            cached=True,
            message="Кеш свежий",
        )

    third_party = state.third_party_only()
    inventoried: list[str] = []

    for serial in targets:
        rec = by_serial.get(serial)
        online = bool(rec and rec.online)
        if not online:
            state.db.enqueue_pending(serial, "check", reason="offline")
            prog(f"Пропуск (offline): {serial}")
            continue
        label = rec.label if rec else serial
        working = ""
        try:
            prog(f"Список приложений: {label}…")
            # Probe first; wake only if silent; skip if still dead (no infinite hang)
            working = state.devices.prepare_online(serial, hold_awake=True)
            try:
                try:
                    from app.services.package_sources import sync_markers_from_device

                    n = sync_markers_from_device(
                        state.db, state.adb, state.devices, serial
                    )
                    if n:
                        prog(f"Метки сторов с устройства: +{n}")
                except Exception:  # noqa: BLE001
                    pass
                # Skip APK pull/icon enrich during update check — versions come from
                # dumpsys. Heavy enrich hangs on locked HyperOS tablets.
                prev_by_pkg = {
                    a.package_name: a for a in (state.get_apps(serial) or [])
                }
                apps = state.apps.list_packages(
                    working,
                    third_party_only=third_party,
                    enrich=False,
                    meta_timeout=60.0,
                )
            finally:
                if working:
                    state.devices.release_online(working)
            from app.services.apps import AppInfo

            merged = []
            for app in apps:
                prev = prev_by_pkg.get(app.package_name)
                if prev is None:
                    merged.append(app)
                    continue
                # Prefer dumpsys label when real; otherwise keep previous human name.
                # (app.label is often == package_name when enrich=False / HyperOS.)
                if app.has_real_label():
                    label = app.label
                elif prev.has_real_label():
                    label = prev.label
                else:
                    label = app.label or prev.label or app.package_name
                icon = app.icon_path or prev.icon_path
                merged.append(
                    AppInfo(
                        package_name=app.package_name,
                        label=label,
                        version_name=app.version_name,
                        version_code=app.version_code,
                        installer=app.installer or prev.installer,
                        apk_path=app.apk_path or prev.apk_path,
                        icon_path=icon,
                    )
                )
            apps = merged
            state.set_apps(serial, apps)
            inventoried.append(serial)
            state.db.clear_pending(serial, kind="check")
        except AdbError as exc:
            if working:
                try:
                    state.devices.release_online(working)
                except Exception:  # noqa: BLE001
                    pass
            state.db.enqueue_pending(serial, "check", reason=f"adb_error: {exc}")
            prog(f"Пропуск {label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            if working:
                try:
                    state.devices.release_online(working)
                except Exception:  # noqa: BLE001
                    pass
            state.db.enqueue_pending(serial, "check", reason=f"error: {exc}")
            prog(f"Пропуск {label}: {exc}")

    need_pkgs: set[str] = set()
    for serial in inventoried:
        for app in state.get_apps(serial):
            installer = (app.installer or "").strip()
            if installer == PLAY_INSTALLER:
                continue
            need_pkgs.add(app.package_name)

    if force:
        fetched = state.updates.fetch_remotes(
            sorted(need_pkgs),
            progress=lambda done, total, pkg: prog(f"Сторы ({done}/{total}): {pkg}"),
        )
        state.set_remotes(fetched.best, stores=fetched.stores, replace=True)
        remotes = fetched.best
        stores_map = fetched.stores
        checked_sources = fetched.checked_sources
    else:
        missing = [p for p in need_pkgs if p not in state.remotes_cache]
        if missing:
            fetched = state.updates.fetch_remotes(
                missing,
                progress=lambda done, total, pkg: prog(f"Сторы ({done}/{total}): {pkg}"),
            )
            state.set_remotes(fetched.best, stores=fetched.stores, replace=False)
            checked_sources = fetched.checked_sources
        else:
            checked_sources = tuple(sorted(state.updates.enabled_store_sources()))
        remotes = {p: state.remotes_cache.get(p) for p in need_pkgs}
        stores_map = {p: state.remotes_stores_cache.get(p, ()) for p in need_pkgs}

    devices_out: dict[str, list[dict[str, Any]]] = {}
    for serial in inventoried:
        apps = state.get_apps(serial)
        mapping = state.updates.check_updates(
            apps,
            remotes=remotes,
            stores=stores_map,
            checked_sources=checked_sources,
        )
        state.set_updates(serial, mapping)
        devices_out[serial] = _apps_payload(state, serial)

    if record_bg_run:
        import time

        state.db.set_setting("bg_updates_last_run", str(time.time()))

    total_updates = 0
    for rows in devices_out.values():
        total_updates += sum(1 for a in rows if a.get("has_update"))
    try:
        from app.notify import maybe_notify_updates_available

        maybe_notify_updates_available(
            state.db, total_updates=total_updates, cached=False
        )
    except Exception:  # noqa: BLE001
        pass

    return BatchCheckResult(
        devices=devices_out,
        pending_serials=state.db.list_pending_serials(),
        cached=False,
        message=f"Проверено устройств: {len(inventoried)}",
    )


def run_install_all(
    state: AppState,
    *,
    serials: list[str] | None = None,
    progress: ProgressCb | None = None,
    same_source_only: bool = False,
) -> BatchInstallResult:
    def prog(msg: str | dict) -> None:
        if progress:
            progress(msg)

    from app.services.install_flags import (
        FLAG_OLDER_SDK,
        FLAG_SIGNATURE,
        clear_flag,
        list_flags,
        set_flag,
    )

    _AUTO_SKIP_FLAGS = {FLAG_SIGNATURE, FLAG_OLDER_SDK}
    from app.services.package_sources import resolve_preferred_source
    from app.services.update_ignores import list_ignored

    known = {d.serial: d for d in state.devices.list_known()}

    def device_label(serial: str) -> str:
        rec = known.get(serial)
        return rec.label if rec else serial

    if serials is None:
        targets = list(known.keys())
    else:
        targets = list(serials)

    # package -> (UpdateInfo, serials needing it)
    groups: dict[str, tuple[UpdateInfo, list[str]]] = {}
    skipped_source = 0
    skipped_offline = 0
    skipped_ignored = 0
    skipped_flag = 0
    has_update_n = 0
    for serial in targets:
        rec = known.get(serial)
        online = bool(rec and rec.online)
        mapping = state.get_updates(serial)
        apps_by_pkg = {a.package_name: a for a in state.get_apps(serial)}
        ignored = list_ignored(state.db, serial)
        flags = list_flags(state.db, serial) if same_source_only else {}
        for pkg, upd in mapping.items():
            if not upd.has_update:
                continue
            if pkg in ignored:
                skipped_ignored += 1
                continue
            has_update_n += 1
            remote_src = upd.remote.source if upd.remote else ""
            if same_source_only:
                fl = flags.get(pkg) or {}
                fl_code = fl.get("flag") or ""
                if fl_code in _AUTO_SKIP_FLAGS:
                    # Permanent-ish install fail — don't loop in auto/catch-up
                    skipped_flag += 1
                    fl_name = (
                        "подпись"
                        if fl_code == FLAG_SIGNATURE
                        else "старый Android"
                        if fl_code == FLAG_OLDER_SDK
                        else fl_code
                    )
                    prog(
                        f"Пропуск (пометка «{fl_name}»): {upd.label or pkg} "
                        f"на {device_label(serial)} "
                        f"(flag_src={fl.get('source') or '—'}, remote={remote_src or '—'})"
                    )
                    continue
                app = apps_by_pkg.get(pkg)
                preferred = resolve_preferred_source(
                    state.db,
                    serial,
                    pkg,
                    installer=app.installer if app else "",
                )
                if preferred:
                    # Explicit store: only that store
                    if not remote_src or remote_src != preferred:
                        skipped_source += 1
                        prog(
                            f"Пропуск (другой стор): {upd.label or pkg} "
                            f"на {device_label(serial)} "
                            f"(pref={preferred}, remote={remote_src or '—'})"
                        )
                        continue
                else:
                    # Auto: install from chosen remote (best/first from check)
                    if not remote_src:
                        skipped_source += 1
                        prog(
                            f"Пропуск (нет remote-стора): {upd.label or pkg} "
                            f"на {device_label(serial)}"
                        )
                        continue
                    prog(
                        f"Авто-стор: {upd.label or pkg} на {device_label(serial)} "
                        f"→ {remote_src}"
                    )
            if not online:
                skipped_offline += 1
                state.db.enqueue_pending(
                    serial, "install", package_name=pkg, reason="offline"
                )
                continue
            if pkg not in groups:
                groups[pkg] = (upd, [serial])
            else:
                _, ser_list = groups[pkg]
                if serial not in ser_list:
                    ser_list.append(serial)

    if not groups:
        pending = state.db.list_pending_serials()
        bits = [f"найдено обновлений={has_update_n}"]
        if skipped_ignored:
            bits.append(f"пропуск «не обновлять»={skipped_ignored}")
        if skipped_flag:
            bits.append(f"пропуск «подпись»={skipped_flag}")
        if same_source_only:
            bits.append(f"пропуск из‑за стора={skipped_source}")
        if skipped_offline:
            bits.append(f"offline={skipped_offline}")
        return BatchInstallResult(
            message=(
                "Нет доступных обновлений для online-устройств"
                + (
                    f" ({'; '.join(bits)})"
                    if has_update_n
                    or skipped_source
                    or skipped_ignored
                    or skipped_flag
                    else ""
                )
            ),
            pending_serials=pending,
        )

    # Announce plan so UI can show rows immediately
    plan_items: list[dict] = []
    for pkg, (upd, ser_list) in groups.items():
        for s in ser_list:
            plan_items.append(
                {
                    "id": f"{pkg}::{s}",
                    "package": pkg,
                    "app_label": upd.label or pkg,
                    "serial": s,
                    "device_label": device_label(s),
                    "status": "queued",
                    "detail": "В очереди",
                }
            )
    prog(
        {
            "type": "install_plan",
            "items": plan_items,
            "message": f"К установке: {len(plan_items)}",
        }
    )

    ok = 0
    fail = 0
    details: list[str] = []
    total_pkgs = len(groups)

    for i, (pkg, (upd, ser_list)) in enumerate(groups.items(), start=1):
        app_label = upd.label or pkg
        for s in ser_list:
            prog(
                {
                    "type": "install_item",
                    "id": f"{pkg}::{s}",
                    "package": pkg,
                    "app_label": app_label,
                    "serial": s,
                    "device_label": device_label(s),
                    "status": "downloading",
                    "detail": f"Скачивание ({i}/{total_pkgs})…",
                }
            )
        prog(f"Пакет {i}/{total_pkgs}: {app_label} → {len(ser_list)} устр.")

        def dl_prog(m: str, _pkg=pkg, _label=app_label, _serials=ser_list) -> None:
            for s in _serials:
                prog(
                    {
                        "type": "install_item",
                        "id": f"{_pkg}::{s}",
                        "package": _pkg,
                        "app_label": _label,
                        "serial": s,
                        "device_label": device_label(s),
                        "status": "downloading",
                        "detail": m,
                    }
                )

        try:
            for s in ser_list:
                prog(
                    {
                        "type": "install_item",
                        "id": f"{pkg}::{s}",
                        "package": pkg,
                        "app_label": app_label,
                        "serial": s,
                        "device_label": device_label(s),
                        "status": "installing",
                        "detail": "Установка…",
                    }
                )
            _artifact, results = state.updates.download_and_install(
                upd,
                ser_list,
                progress=dl_prog,
                item_progress=lambda serial, phase, detail, _pkg=pkg, _label=app_label: prog(
                    {
                        "type": "install_item",
                        "id": f"{_pkg}::{serial}",
                        "package": _pkg,
                        "app_label": _label,
                        "serial": serial,
                        "device_label": device_label(serial),
                        "status": phase,
                        "detail": detail,
                    }
                ),
            )
        except Exception as exc:  # noqa: BLE001
            fail += len(ser_list)
            details.append(f"{pkg}: download/install error: {exc}")
            for s in ser_list:
                state.db.enqueue_pending(
                    s, "install", package_name=pkg, reason=f"install_fail: {exc}"
                )
                prog(
                    {
                        "type": "install_item",
                        "id": f"{pkg}::{s}",
                        "package": pkg,
                        "app_label": app_label,
                        "serial": s,
                        "device_label": device_label(s),
                        "status": "fail",
                        "detail": str(exc),
                    }
                )
            continue

        for r in results:
            if r.success:
                ok += 1
                try:
                    clear_flag(state.db, r.serial, pkg)
                except Exception:  # noqa: BLE001
                    pass
                mapping = dict(state.get_updates(r.serial))
                old = mapping.get(pkg)
                if old:
                    mapping[pkg] = UpdateInfo(
                        package_name=old.package_name,
                        label=old.label,
                        local_code=old.remote.version_code if old.remote else old.local_code,
                        local_name=(old.remote.version_name if old.remote else old.local_name),
                        remote=old.remote,
                        skipped_play=old.skipped_play,
                        status="up_to_date",
                        error="",
                        stores=old.stores,
                        checked_sources=old.checked_sources,
                    )
                    state.set_updates(r.serial, mapping)
                state.db.clear_pending(r.serial, kind="install", package_name=pkg)
                details.append(f"{pkg} @ {r.serial}: ok")
                prog(
                    {
                        "type": "install_item",
                        "id": f"{pkg}::{r.serial}",
                        "package": pkg,
                        "app_label": app_label,
                        "serial": r.serial,
                        "device_label": device_label(r.serial),
                        "status": "ok",
                        "detail": "Готово",
                    }
                )
            else:
                fail += 1
                src_tried = upd.remote.source if upd.remote else ""
                if r.error_code in _AUTO_SKIP_FLAGS:
                    try:
                        set_flag(
                            state.db,
                            r.serial,
                            pkg,
                            flag=r.error_code,
                            source=src_tried,
                            message=r.message or r.error_title or "",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    # Don't enqueue endless retries for permanent incompatibility
                    state.db.clear_pending(r.serial, kind="install", package_name=pkg)
                    if r.error_code == FLAG_SIGNATURE:
                        prog(
                            f"Пометка «подпись не совпала»: {app_label} "
                            f"на {device_label(r.serial)} (стор={src_tried or '—'})"
                        )
                    else:
                        prog(
                            f"Пометка «нужен новее Android»: {app_label} "
                            f"на {device_label(r.serial)} (стор={src_tried or '—'})"
                        )
                else:
                    state.db.enqueue_pending(
                        r.serial,
                        "install",
                        package_name=pkg,
                        reason=f"install_fail: {r.message}",
                    )
                details.append(f"{pkg} @ {r.serial}: {r.message}")
                prog(
                    {
                        "type": "install_item",
                        "id": f"{pkg}::{r.serial}",
                        "package": pkg,
                        "app_label": app_label,
                        "serial": r.serial,
                        "device_label": device_label(r.serial),
                        "status": "fail",
                        "detail": r.message,
                        **r.as_progress_fields(),
                    }
                )

    return BatchInstallResult(
        ok=ok,
        fail=fail,
        pending_serials=state.db.list_pending_serials(),
        message=f"Установка: ok={ok}, fail={fail}",
        details=details,
    )


def run_catch_up_pending(state: AppState, *, progress: ProgressCb | None = None) -> str:
    """Process due pending jobs for devices that are currently online."""
    def prog(msg: str) -> None:
        if progress:
            progress(msg)

    auto_install = (state.db.get_setting("auto_install_enabled", "0") or "0").strip() in {
        "1",
        "true",
        "yes",
        "on",
    }

    known = {d.serial: d for d in state.devices.list_known()}
    due = state.db.list_pending(due_only=True)
    if not due:
        return ""

    # Group by serial
    by_serial: dict[str, list] = defaultdict(list)
    for row in due:
        by_serial[str(row["serial"])].append(row)

    messages: list[str] = []
    for serial, rows in by_serial.items():
        rec = known.get(serial)
        if not rec or not rec.online:
            continue
        kinds = {str(r["kind"]) for r in rows}
        prog(f"Догон {serial}: {', '.join(sorted(kinds))}")
        try:
            result = run_check_batch(
                state, force=True, serials=[serial], progress=progress
            )
            messages.append(result.message)
        except Exception as exc:  # noqa: BLE001
            for r in rows:
                state.db.bump_pending_attempt(
                    serial, str(r["kind"]), str(r["package_name"] or "")
                )
            messages.append(f"catch-up check fail {serial}: {exc}")
            continue

        need_install = auto_install or ("install" in kinds)
        if need_install:
            # Pending manual install retries: any source. Pure auto: same source only.
            same_src = bool(auto_install) and "install" not in kinds
            try:
                inst = run_install_all(
                    state,
                    serials=[serial],
                    progress=progress,
                    same_source_only=same_src,
                )
                messages.append(inst.message)
                if inst.fail == 0:
                    state.db.clear_pending(serial)
                else:
                    for r in rows:
                        if str(r["kind"]) == "install":
                            state.db.bump_pending_attempt(
                                serial, "install", str(r["package_name"] or "")
                            )
            except Exception as exc:  # noqa: BLE001
                for r in rows:
                    state.db.bump_pending_attempt(
                        serial, str(r["kind"]), str(r["package_name"] or "")
                    )
                messages.append(f"catch-up install fail {serial}: {exc}")
        else:
            state.db.clear_pending(serial, kind="check")

    return "; ".join(messages)
