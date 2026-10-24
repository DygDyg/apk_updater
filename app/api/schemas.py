from __future__ import annotations

from pydantic import BaseModel, Field


class StatusOut(BaseModel):
    adb_available: bool
    adb_path: str
    adb_message: str
    host: str = "0.0.0.0"
    port: int = 8765
    job_running: bool = False
    job_label: str = ""
    job_age_sec: float = 0.0
    build_id: str = "dev"
    latest_build: str = ""
    update_available: bool = False
    app_frozen: bool = False
    github_repo: str = ""


class AdbPathIn(BaseModel):
    path: str = ""


class AppSettingsOut(BaseModel):
    bg_updates_enabled: bool = False
    bg_updates_interval_hours: float = 6.0
    update_check_ttl_hours: float = 1.0
    auto_install_enabled: bool = False
    auto_install_interval_hours: float = 24.0
    notify_updates_enabled: bool = True
    store_rustore_enabled: bool = True
    store_apkpure_enabled: bool = True
    store_aptoide_enabled: bool = True
    store_fdroid_enabled: bool = True
    prefer_release_updates: bool = True
    open_browser_on_start: bool = False
    hide_play_store: bool = True
    third_party_only: bool = True
    apk_cache_keep_days: float = 2.0
    bg_updates_last_run: str = ""
    auto_install_last_run: str = ""
    app_update_check_enabled: bool = True
    app_auto_update_enabled: bool = False
    app_update_check_interval_hours: float = 1.0
    app_update_last_check: str = ""
    build_id: str = "dev"


class AppSettingsIn(BaseModel):
    bg_updates_enabled: bool | None = None
    bg_updates_interval_hours: float | None = None
    update_check_ttl_hours: float | None = None
    auto_install_enabled: bool | None = None
    auto_install_interval_hours: float | None = None
    notify_updates_enabled: bool | None = None
    store_rustore_enabled: bool | None = None
    store_apkpure_enabled: bool | None = None
    store_aptoide_enabled: bool | None = None
    store_fdroid_enabled: bool | None = None
    prefer_release_updates: bool | None = None
    open_browser_on_start: bool | None = None
    hide_play_store: bool | None = None
    third_party_only: bool | None = None
    apk_cache_keep_days: float | None = None
    app_update_check_enabled: bool | None = None
    app_auto_update_enabled: bool | None = None
    app_update_check_interval_hours: float | None = None


class AppUpdateCheckOut(BaseModel):
    ok: bool = True
    local_build: str = "dev"
    latest_build: str = ""
    update_available: bool = False
    download_url: str = ""
    message: str = ""
    frozen: bool = False
    github_repo: str = ""
    last_check: str = ""


class AppUpdateApplyOut(BaseModel):
    ok: bool = True
    message: str = ""
    restarting: bool = False


class DeviceOut(BaseModel):
    serial: str
    label: str
    description: str
    channels_text: str
    state: str
    online: bool
    model: str = ""
    brand: str = ""
    market_name: str = ""
    android_version: str = ""
    sdk: str = ""
    preview_url: str | None = None
    transports: list[str] = Field(default_factory=list)
    device_uid: str = ""


class DiscoverOut(BaseModel):
    key: str
    kind: str
    name: str
    ip: str
    port: int
    host_port: str
    label: str = ""


class PairIn(BaseModel):
    ip: str
    port: str
    code: str


class ConnectIn(BaseModel):
    ip: str
    port: str


class SerialIn(BaseModel):
    serial: str


class DeviceActionIn(BaseModel):
    serial: str
    action: str  # wake | play_timer | play_alarm | mute | unmute | volume_up | volume_down | stay_awake_on | stay_awake_off


class DeviceLiveOut(BaseModel):
    battery_pct: int | None = None
    charging: bool | None = None
    screen: str = ""
    density: str = ""
    ringer: str = ""
    ip: str = ""
    form_factor: str = "mobile"
    sdk_int: int = 0
    is_tv: bool = False


class DeviceInfoOut(BaseModel):
    device: DeviceOut
    live: DeviceLiveOut
    screenshot_url: str = ""


class DeviceActionOut(BaseModel):
    ok: bool
    message: str


class DeviceScreenshotOut(BaseModel):
    ok: bool
    message: str
    url: str = ""


class DeviceScrcpyOut(BaseModel):
    ok: bool
    message: str


class DiscoverActionIn(BaseModel):
    key: str
    kind: str
    ip: str
    port: int
    code: str = ""


class MessageOut(BaseModel):
    message: str
    ok: bool = True


class ClearCacheOut(BaseModel):
    ok: bool = True
    message: str
    files: int = 0
    total_bytes: int = 0
    by_kind: dict[str, dict[str, int]] = Field(default_factory=dict)


class BgActivityEntryOut(BaseModel):
    id: int
    created_at: float
    kind: str
    event: str
    status: str = "info"
    message: str = ""
    package_name: str = ""
    app_label: str = ""
    serial: str = ""
    device_label: str = ""
    detail: str = ""


class BgActivityLogOut(BaseModel):
    entries: list[BgActivityEntryOut] = Field(default_factory=list)


class StoreHitOut(BaseModel):
    source: str
    version_name: str = ""
    version_code: int = 0
    unsupported_reason: str = ""
    chosen: bool = False


class AppOut(BaseModel):
    package_name: str
    label: str
    version_name: str
    version_code: str
    installer: str = ""
    icon_url: str | None = None
    update_status: str = ""
    update_badge: str = ""
    has_update: bool = False
    manual_update: bool = False
    update_ignored: bool = False
    install_flag: str = ""
    install_flag_label: str = ""
    install_flag_source: str = ""
    remote_version: str = ""
    remote_source: str = ""
    stores: list[StoreHitOut] = Field(default_factory=list)
    stores_tooltip: str = ""
    install_source: str = ""
    install_source_label: str = ""
    install_source_tooltip: str = ""


class SetAppSourceIn(BaseModel):
    serial: str
    package_name: str
    source: str = ""  # empty = clear manual preference


class SetUpdateIgnoreIn(BaseModel):
    serial: str
    package_name: str
    ignored: bool = True


class UpdateIgnoreOut(BaseModel):
    ok: bool = True
    message: str
    app: AppOut | None = None


class AppActionIn(BaseModel):
    serial: str
    package_name: str
    action: str  # force_stop | clear_cache | clear_data | uninstall | open_settings


class AppPermissionIn(BaseModel):
    serial: str
    package_name: str
    permission: str
    granted: bool


class AppActionOut(BaseModel):
    ok: bool
    message: str
    details: dict | None = None


class CheckUpdatesIn(BaseModel):
    serial: str
    hide_play_store: bool = True
    force: bool = False


class CheckUpdatesBatchIn(BaseModel):
    serials: list[str] | None = None
    hide_play_store: bool = True
    force: bool = False


class InstallUpdateIn(BaseModel):
    serial: str
    package_name: str


class ReinstallUpdateIn(BaseModel):
    serial: str
    package_name: str


class InstallAllIn(BaseModel):
    serials: list[str] | None = None


class InstallCachedIn(BaseModel):
    serial: str
    package_name: str = ""
    apk_path: str
    allow_downgrade: bool = False


class StoreInstallIn(BaseModel):
    package_name: str
    source: str
    version_code: int | None = None
    serials: list[str] = Field(default_factory=list)


class QrStartOut(BaseModel):
    png_base64: str
    service_name: str
    message: str = "Сканируйте QR в Беспроводной отладке"
