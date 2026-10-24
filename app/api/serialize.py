from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.api.schemas import AppOut, DeviceOut, DiscoverOut
from app.providers.stores import STORE_ORDER
from app.services.apps import AppInfo
from app.services.devices import DeviceRecord
from app.services.discovery import DiscoveredEndpoint
from app.services.updates import UpdateInfo


def device_out(rec: DeviceRecord) -> DeviceOut:
    preview_url = None
    if rec.preview_path and Path(rec.preview_path).is_file():
        preview_url = f"/api/media/file?path={quote(rec.preview_path, safe='')}"
    return DeviceOut(
        serial=rec.serial,
        label=rec.label,
        description=rec.description,
        channels_text=rec.channels_text,
        state=rec.state,
        online=rec.online,
        model=rec.model,
        brand=rec.brand,
        market_name=rec.market_name,
        android_version=rec.android_version,
        sdk=rec.sdk,
        preview_url=preview_url,
        transports=list(rec.transports or [rec.serial]),
        device_uid=rec.device_uid,
    )


def discover_out(ep: DiscoveredEndpoint, *, label: str = "") -> DiscoverOut:
    return DiscoverOut(
        key=ep.key,
        kind=ep.kind.value,
        name=ep.short_name,
        ip=ep.ip,
        port=ep.port,
        host_port=ep.host_port,
        label=label or "",
    )


def app_out(
    app: AppInfo,
    update: UpdateInfo | None = None,
    *,
    install_source: str = "",
    install_source_tooltip: str = "",
    update_ignored: bool = False,
    install_flag: str = "",
    install_flag_source: str = "",
    install_flag_message: str = "",
) -> AppOut:
    from app.api.schemas import StoreHitOut
    from app.services.install_flags import flag_blocks_auto, flag_label
    from app.services.package_sources import install_source_tooltip as default_src_tip
    from app.services.package_sources import source_label

    icon_url = None
    if app.icon_path and Path(app.icon_path).is_file():
        icon_url = f"/api/media/file?path={quote(app.icon_path, safe='')}"
    remote_version = ""
    remote_source = ""
    stores: list[StoreHitOut] = []
    stores_tooltip = ""
    if update and update.remote:
        remote_version = update.remote.version_name or str(update.remote.version_code)
        remote_source = update.remote.source
    if update:
        chosen = update.remote.source if update.remote else ""
        by_src = {s.source: s for s in update.stores}
        order = update.checked_sources or tuple(
            s for s in STORE_ORDER if s in by_src
        )
        for src in order:
            hit = by_src.get(src)
            if hit is None:
                stores.append(
                    StoreHitOut(source=src, version_name="", version_code=0, chosen=False)
                )
            else:
                stores.append(
                    StoreHitOut(
                        source=hit.source,
                        version_name=hit.version_name or str(hit.version_code),
                        version_code=hit.version_code,
                        unsupported_reason=hit.unsupported_reason or "",
                        chosen=hit.source == chosen,
                    )
                )
        for hit in update.stores:
            if hit.source in order:
                continue
            stores.append(
                StoreHitOut(
                    source=hit.source,
                    version_name=hit.version_name or str(hit.version_code),
                    version_code=hit.version_code,
                    unsupported_reason=hit.unsupported_reason or "",
                    chosen=hit.source == chosen,
                )
            )
        stores_tooltip = update.stores_tooltip(chosen_source=chosen)
    tip = install_source_tooltip or default_src_tip(install_source, app.installer)
    raw_has = bool(update and update.has_update)
    raw_manual = bool(update and update.manual_update)
    flag = (install_flag or "").strip()
    flag_src = (install_flag_source or "").strip()
    flag_msg = (install_flag_message or "").strip()
    # Keep first line of message for badge / list (drop adb dump if present)
    flag_msg_short = flag_msg.split("\n", 1)[0].strip() if flag_msg else ""
    blocked = bool(flag) and flag_blocks_auto(flag)
    if update_ignored:
        badge = "не обновлять"
        status = "ignored"
        has_update = False
        manual_update = False
    elif blocked:
        label = flag_label(flag) or flag
        badge = f"{label}: {flag_msg_short}" if flag_msg_short else label
        status = "install_flag"
        # Exclude from auto / «Обновить все» until flag cleared or remote changes
        has_update = False
        manual_update = False
    else:
        badge = update.badge if update else ""
        status = update.status if update else ""
        has_update = raw_has
        manual_update = raw_manual
    return AppOut(
        package_name=app.package_name,
        label=app.display_label,
        version_name=app.version_name,
        version_code=app.version_code,
        installer=app.installer,
        icon_url=icon_url,
        update_status=status,
        update_badge=badge,
        has_update=has_update,
        manual_update=manual_update,
        update_ignored=bool(update_ignored),
        install_flag=flag,
        install_flag_label=flag_label(flag) if flag else "",
        install_flag_source=flag_src,
        install_flag_message=flag_msg_short or flag_msg,
        remote_version=remote_version,
        remote_source=remote_source,
        stores=stores,
        stores_tooltip=stores_tooltip,
        install_source=install_source,
        install_source_label=source_label(install_source) if install_source else "",
        install_source_tooltip=tip,
    )
