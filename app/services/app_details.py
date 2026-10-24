from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.adb.client import AdbClient, AdbError, AdbResult
from app.services.devices import DeviceService
from app.services.install import InstallResult, InstallService

_DANGEROUS_PERMS = {
    "android.permission.READ_CALENDAR",
    "android.permission.WRITE_CALENDAR",
    "android.permission.CAMERA",
    "android.permission.READ_CONTACTS",
    "android.permission.WRITE_CONTACTS",
    "android.permission.GET_ACCOUNTS",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.RECORD_AUDIO",
    "android.permission.READ_PHONE_STATE",
    "android.permission.CALL_PHONE",
    "android.permission.READ_CALL_LOG",
    "android.permission.WRITE_CALL_LOG",
    "android.permission.ADD_VOICEMAIL",
    "android.permission.USE_SIP",
    "android.permission.PROCESS_OUTGOING_CALLS",
    "android.permission.BODY_SENSORS",
    "android.permission.SEND_SMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_SMS",
    "android.permission.RECEIVE_WAP_PUSH",
    "android.permission.RECEIVE_MMS",
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
    "android.permission.ACCESS_MEDIA_LOCATION",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_MEDIA_AUDIO",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.NEARBY_WIFI_DEVICES",
    "android.permission.BLUETOOTH_CONNECT",
    "android.permission.BLUETOOTH_SCAN",
    "android.permission.BLUETOOTH_ADVERTISE",
    "android.permission.ACTIVITY_RECOGNITION",
}

_PKG_HEADER = re.compile(r"^Package \[([\w.]+)\]")
_USER_ID = re.compile(r"userId=(\d+)")
_PERM_GRANTED = re.compile(
    r"(android\.permission\.[\w.]+):\s*granted=(true|false)",
    re.I,
)
_REQUESTED_PERM = re.compile(r"^\s+(android\.permission\.[\w.]+)\s*$")
_CODE_PATH = re.compile(r"codePath=(\S+)")
_DATA_DIR = re.compile(r"dataDir=(\S+)")
_VERSION_NAME = re.compile(r"versionName=(\S+)")
_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_INSTALLER = re.compile(r"installerPackageName=(\S*)")
_APP_LABEL = re.compile(r"applicationLabel=(.+)$")
_TARGET_SDK = re.compile(r"targetSdk(?:Version)?=(\d+)")
_MIN_SDK = re.compile(r"minSdk(?:Version)?=(\d+)")
_ENABLED = re.compile(r"\benabled=(\d+)")
_TIME_MS = re.compile(r"(firstInstallTime|lastUpdateTime)=(\d+)")


@dataclass
class AppPermission:
    name: str
    granted: bool
    dangerous: bool = False


@dataclass
class AppStorage:
    apk_bytes: int | None = None
    data_bytes: int | None = None
    cache_bytes: int | None = None

    @property
    def total_bytes(self) -> int | None:
        parts = [self.apk_bytes, self.data_bytes, self.cache_bytes]
        known = [p for p in parts if p is not None]
        if not known:
            return None
        return sum(known)


@dataclass
class AppDetails:
    package_name: str
    label: str = ""
    version_name: str = ""
    version_code: str = ""
    installer: str = ""
    enabled: bool | None = None
    target_sdk: int | None = None
    min_sdk: int | None = None
    uid: int | None = None
    first_install_time: str = ""
    last_update_time: str = ""
    apk_path: str = ""
    data_dir: str = ""
    storage: AppStorage = field(default_factory=AppStorage)
    permissions: list[AppPermission] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _fmt_ms(ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(ms)


def _parse_du_bytes(text: str) -> int | None:
    line = (text or "").strip().splitlines()
    if not line:
        return None
    first = line[0].strip().split()
    if not first:
        return None
    try:
        val = int(first[0])
    except ValueError:
        return None
    # Android toybox `du -s` reports KiB
    return val * 1024


def _parse_stat_size(text: str) -> int | None:
    for line in (text or "").splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
        m = re.search(r"Size:\s*(\d+)", line, re.I)
        if m:
            return int(m.group(1))
    return None


def details_to_dict(details: AppDetails) -> dict:
    return {
        "package_name": details.package_name,
        "label": details.label,
        "version_name": details.version_name,
        "version_code": details.version_code,
        "installer": details.installer,
        "enabled": details.enabled,
        "target_sdk": details.target_sdk,
        "min_sdk": details.min_sdk,
        "uid": details.uid,
        "first_install_time": details.first_install_time,
        "last_update_time": details.last_update_time,
        "apk_path": details.apk_path,
        "data_dir": details.data_dir,
        "storage": {
            "apk_bytes": details.storage.apk_bytes,
            "data_bytes": details.storage.data_bytes,
            "cache_bytes": details.storage.cache_bytes,
            "total_bytes": details.storage.total_bytes,
        },
        "permissions": [
            {"name": p.name, "granted": p.granted, "dangerous": p.dangerous}
            for p in details.permissions
        ],
        "notes": list(details.notes),
    }


class AppDetailsService:
    def __init__(
        self,
        adb: AdbClient,
        devices: DeviceService | None = None,
        install: InstallService | None = None,
    ) -> None:
        self.adb = adb
        self.devices = devices
        self.install = install

    def _working_serial(self, serial: str) -> str:
        if self.devices is not None:
            return self.devices.resolve_online(serial)
        return serial

    def _candidates(self, serial: str) -> list[str]:
        if self.devices is not None:
            cands = self.devices.transport_candidates(serial)
            if cands:
                return cands
        return [serial]

    def _shell(self, serial: str, command: str, *, timeout: float = 30.0) -> AdbResult:
        last: AdbResult | None = None
        for cand in self._candidates(serial):
            try:
                last = self.adb.shell(cand, command, check=False, timeout=timeout)
                if last.ok or (last.stdout or "").strip():
                    return last
            except AdbError:
                continue
        if last is None:
            raise AdbError(f"ADB shell failed: {command}")
        return last

    def get_details(self, serial: str, package_name: str) -> AppDetails:
        pkg = (package_name or "").strip()
        if not pkg:
            raise AdbError("package_name required")
        working = self._working_serial(serial)
        dump = self._shell(working, f"dumpsys package {pkg}", timeout=45.0)
        text = dump.stdout or ""
        if not text.strip() or "Unable to find package" in text:
            raise AdbError(f"Пакет не найден на устройстве: {pkg}")

        details = self._parse_dumpsys(pkg, text)
        self._fill_storage(working, details)
        return details

    def _parse_dumpsys(self, pkg: str, text: str) -> AppDetails:
        details = AppDetails(package_name=pkg)
        in_pkg = f"Package [{pkg}]" not in text  # whole dump is this package
        in_requested = False
        requested: set[str] = set()
        granted_map: dict[str, bool] = {}

        for raw in text.splitlines():
            line = raw.rstrip()
            header = _PKG_HEADER.match(line)
            if header:
                in_pkg = header.group(1) == pkg
                in_requested = False
                continue
            if not in_pkg:
                continue

            stripped = line.strip()
            low = stripped.lower()
            if low.startswith("requested permissions:"):
                in_requested = True
                continue
            if stripped.endswith("permissions:") and not low.startswith("requested"):
                in_requested = False
                continue

            if in_requested:
                rm = _REQUESTED_PERM.match(line)
                if rm:
                    requested.add(rm.group(1))
                    continue
                if stripped.startswith("android.permission."):
                    requested.add(stripped.split(":")[0].strip())
                    continue

            if "versionName=" in line and not details.version_name:
                m = _VERSION_NAME.search(line)
                if m:
                    details.version_name = m.group(1)
            if "versionCode=" in line and not details.version_code:
                m = _VERSION_CODE.search(line)
                if m:
                    details.version_code = m.group(1)
            if "installerPackageName=" in line:
                m = _INSTALLER.search(line)
                if m:
                    details.installer = (m.group(1) or "").strip() or "unknown"
            if "applicationLabel=" in line:
                m = _APP_LABEL.search(line)
                if m:
                    details.label = m.group(1).strip()
            if details.enabled is None:
                m = _ENABLED.search(line)
                if m:
                    details.enabled = m.group(1) not in ("0", "2", "3")
            if details.target_sdk is None:
                m = _TARGET_SDK.search(line)
                if m:
                    details.target_sdk = int(m.group(1))
            if details.min_sdk is None:
                m = _MIN_SDK.search(line)
                if m:
                    details.min_sdk = int(m.group(1))
            if details.uid is None:
                m = _USER_ID.search(line)
                if m:
                    details.uid = int(m.group(1))
            if not details.apk_path:
                m = _CODE_PATH.search(line)
                if m:
                    details.apk_path = m.group(1)
            if not details.data_dir:
                m = _DATA_DIR.search(line)
                if m:
                    details.data_dir = m.group(1)
            for key, val in _TIME_MS.findall(line):
                try:
                    stamp = _fmt_ms(int(val))
                except ValueError:
                    continue
                if key == "firstInstallTime" and not details.first_install_time:
                    details.first_install_time = stamp
                if key == "lastUpdateTime" and not details.last_update_time:
                    details.last_update_time = stamp

        for m in _PERM_GRANTED.finditer(text):
            granted_map[m.group(1)] = m.group(2).lower() == "true"

        names = set(granted_map) | {p for p in requested if p in _DANGEROUS_PERMS}
        perms: list[AppPermission] = []
        for name in sorted(names):
            dangerous = name in _DANGEROUS_PERMS
            if not dangerous and name not in granted_map:
                continue
            if not dangerous and not granted_map:
                continue
            # Show dangerous always; other only if we know grant state
            if not dangerous and name not in granted_map:
                continue
            perms.append(
                AppPermission(
                    name=name,
                    granted=bool(granted_map.get(name, False)),
                    dangerous=dangerous,
                )
            )
        # Prefer: all with grant state that are dangerous, else all granted_map dangerous
        if not perms and granted_map:
            for name, granted in sorted(granted_map.items()):
                if name in _DANGEROUS_PERMS:
                    perms.append(
                        AppPermission(name=name, granted=granted, dangerous=True)
                    )
        perms.sort(key=lambda p: (not p.dangerous, p.name))
        details.permissions = perms
        if not details.label:
            details.label = pkg
        return details

    def _fill_storage(self, serial: str, details: AppDetails) -> None:
        notes: list[str] = []
        path = details.apk_path
        if not path:
            r = self._shell(serial, f"pm path {details.package_name}", timeout=15.0)
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    path = line[len("package:") :].strip()
                    details.apk_path = path
                    break
        if path:
            first = path.splitlines()[0].strip()
            st = self._shell(
                serial,
                f"stat -c %s '{first}' 2>/dev/null || stat '{first}'",
                timeout=15.0,
            )
            size = _parse_stat_size(st.stdout)
            if size is not None:
                details.storage.apk_bytes = size
            else:
                notes.append("Размер APK недоступен")

        data_dir = details.data_dir or f"/data/user/0/{details.package_name}"
        details.data_dir = data_dir

        r_data = self._shell(serial, f"du -s '{data_dir}' 2>/dev/null", timeout=20.0)
        data_size = _parse_du_bytes(r_data.stdout)
        r_cache = self._shell(
            serial, f"du -s '{data_dir}/cache' 2>/dev/null", timeout=20.0
        )
        cache_size = _parse_du_bytes(r_cache.stdout)

        if cache_size is not None:
            details.storage.cache_bytes = cache_size
        else:
            notes.append("Размер «cache» недоступен без root")

        if data_size is not None:
            if cache_size is not None and data_size >= cache_size:
                details.storage.data_bytes = data_size - cache_size
            else:
                details.storage.data_bytes = data_size
        else:
            notes.append("Размер «data» недоступен без root")

        seen: set[str] = set()
        for n in notes:
            if n not in seen:
                seen.add(n)
                details.notes.append(n)

    def force_stop(self, serial: str, package_name: str) -> tuple[bool, str]:
        working = self._working_serial(serial)
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.force_stop(cand, package_name)
                if last.ok:
                    return True, "Приложение остановлено"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        if last and last.ok:
            return True, "Приложение остановлено"
        return False, (last.combined if last else "force-stop failed") or "force-stop failed"

    def clear_cache(self, serial: str, package_name: str) -> tuple[bool, str]:
        working = self._working_serial(serial)
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.clear_package(cand, package_name, cache_only=True)
                text = (last.combined or "").lower()
                if "unknown option" in text or (
                    "cache-only" in text and ("error" in text or "exception" in text)
                ):
                    return (
                        False,
                        "Устройство не поддерживает очистку только кеша "
                        "(нужен Android с pm clear --cache-only)",
                    )
                if "success" in text:
                    return True, "Кеш очищен"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        msg = (last.combined if last else "clear cache failed") or "clear cache failed"
        if "unknown option" in msg.lower():
            return (
                False,
                "Устройство не поддерживает очистку только кеша "
                "(нужен Android с pm clear --cache-only)",
            )
        return False, msg

    def clear_data(self, serial: str, package_name: str) -> tuple[bool, str]:
        working = self._working_serial(serial)
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.clear_package(cand, package_name, cache_only=False)
                if "success" in (last.combined or "").lower():
                    return True, "Данные приложения очищены"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        return False, (last.combined if last else "clear data failed") or "clear data failed"

    def uninstall(self, serial: str, package_name: str) -> tuple[bool, str]:
        if self.install is not None:
            result: InstallResult = self.install.uninstall_on_device(serial, package_name)
            return result.success, result.message
        working = self._working_serial(serial)
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.uninstall(cand, package_name)
                text = (last.combined or "").lower()
                if last.ok and "success" in text and "failure" not in text:
                    return True, last.combined or "Success"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        return False, (last.combined if last else "uninstall failed") or "uninstall failed"

    def open_settings(self, serial: str, package_name: str) -> tuple[bool, str]:
        pkg = (package_name or "").strip()
        working = self._working_serial(serial)
        cmd = (
            "am start -a android.settings.APPLICATION_DETAILS_SETTINGS "
            f"-d package:{pkg}"
        )
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.shell(cand, cmd, check=False, timeout=20.0)
                text = (last.combined or "").lower()
                if last.ok or "error" not in text:
                    return True, "Открыты настройки приложения на устройстве"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        return False, (last.combined if last else "am start failed") or "am start failed"

    def set_permission(
        self, serial: str, package_name: str, permission: str, *, granted: bool
    ) -> tuple[bool, str]:
        pkg = (package_name or "").strip()
        perm = (permission or "").strip()
        if not pkg or not perm:
            raise AdbError("package_name и permission обязательны")
        working = self._working_serial(serial)
        op = "grant" if granted else "revoke"
        cmd = f"pm {op} {pkg} {perm}"
        last: AdbResult | None = None
        for cand in self._candidates(working):
            try:
                last = self.adb.shell(cand, cmd, check=False, timeout=30.0)
                err = (last.combined or "").strip()
                if last.returncode == 0 and "Exception" not in err:
                    return True, f"{'Выдано' if granted else 'Отозвано'}: {perm}"
            except AdbError as exc:
                last = AdbResult(1, "", str(exc))
        return False, (last.combined if last else f"pm {op} failed") or f"pm {op} failed"
