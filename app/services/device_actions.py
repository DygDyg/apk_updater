from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.adb.client import AdbClient, AdbError
from app.paths import SCREENCAP_CACHE_DIR, ensure_data_dirs

_ACTIONS = frozenset(
    {
        "wake",
        "play_timer",
        "play_alarm",
        "play_sound",  # alias → play_alarm
        "mute",
        "unmute",
        "volume_up",
        "volume_down",
        "stay_awake_on",
        "stay_awake_off",
    }
)

_BATTERY_LEVEL = re.compile(r"level:\s*(\d+)", re.I)
_BATTERY_STATUS = re.compile(r"status:\s*(\d+)", re.I)
_BATTERY_PLUGGED = re.compile(r"plugged:\s*(\d+)", re.I)
_WM_SIZE = re.compile(r"Physical size:\s*(\S+)", re.I)
_WM_DENSITY = re.compile(r"Physical density:\s*(\S+)", re.I)


@dataclass
class DeviceLiveInfo:
    battery_pct: int | None = None
    charging: bool | None = None
    screen: str = ""
    density: str = ""
    ringer: str = ""
    ip: str = ""
    form_factor: str = "mobile"  # mobile | tv
    sdk_int: int = 0
    is_tv: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "battery_pct": self.battery_pct,
            "charging": self.charging,
            "screen": self.screen,
            "density": self.density,
            "ringer": self.ringer,
            "ip": self.ip,
            "form_factor": self.form_factor,
            "sdk_int": self.sdk_int,
            "is_tv": self.is_tv,
        }


def _safe_serial_name(serial: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", (serial or "").strip()) or "device"


def screenshot_cache_path(serial: str) -> Path:
    return SCREENCAP_CACHE_DIR / f"{_safe_serial_name(serial)}.png"


def cached_screenshot_url(*serials: str) -> str:
    """Return media URL for the newest existing screencap among serial aliases."""
    ensure_data_dirs()
    SCREENCAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    best: Path | None = None
    best_mtime = -1.0
    seen: set[str] = set()
    for ser in serials:
        key = (ser or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        path = screenshot_cache_path(key)
        try:
            if path.is_file() and path.stat().st_size > 32:
                mtime = path.stat().st_mtime
                if mtime > best_mtime:
                    best = path
                    best_mtime = mtime
        except OSError:
            continue
    if best is None:
        return ""
    ts = int(best_mtime * 1000)
    return f"/api/media/file?path={quote(str(best.resolve()), safe='')}&t={ts}"


class DeviceActionsService:
    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb

    def snapshot(self, serial: str) -> DeviceLiveInfo:
        info = DeviceLiveInfo()
        try:
            ff, sdk = self.adb.detect_form_factor(serial)
            info.form_factor = ff
            info.sdk_int = sdk
            info.is_tv = ff == "tv"
        except Exception:  # noqa: BLE001
            pass

        try:
            bat = self.adb.shell(serial, "dumpsys battery", check=False, timeout=12.0)
            text = bat.stdout or ""
            m = _BATTERY_LEVEL.search(text)
            if m:
                info.battery_pct = int(m.group(1))
            plugged = _BATTERY_PLUGGED.search(text)
            status = _BATTERY_STATUS.search(text)
            if plugged and int(plugged.group(1)) > 0:
                info.charging = True
            elif status and int(status.group(1)) in (2, 5):  # charging / full
                info.charging = True
            elif plugged or status:
                info.charging = False
        except Exception:  # noqa: BLE001
            pass

        try:
            size = self.adb.shell(serial, "wm size", check=False, timeout=6.0)
            m = _WM_SIZE.search(size.stdout or "")
            if m:
                info.screen = m.group(1)
        except Exception:  # noqa: BLE001
            pass

        try:
            dens = self.adb.shell(serial, "wm density", check=False, timeout=6.0)
            m = _WM_DENSITY.search(dens.stdout or "")
            if m:
                info.density = m.group(1)
        except Exception:  # noqa: BLE001
            pass

        if not info.is_tv:
            info.ringer = self._read_ringer(serial)
        info.ip = self._read_ip(serial)
        return info

    def _read_ringer(self, serial: str) -> str:
        for scope in ("global", "system", "secure"):
            try:
                r = self.adb.shell(
                    serial,
                    f"settings get {scope} mode_ringer",
                    check=False,
                    timeout=5.0,
                )
                raw = (r.stdout or "").strip().lower()
                if raw in ("0", "1", "2"):
                    return {"0": "silent", "1": "vibrate", "2": "normal"}[raw]
                if raw and raw not in ("null", "none"):
                    return raw
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _read_ip(self, serial: str) -> str:
        try:
            r = self.adb.shell(serial, "ip route", check=False, timeout=6.0)
            for line in (r.stdout or "").splitlines():
                if "src " in line and ("wlan" in line or "eth" in line or "default" in line):
                    parts = line.split()
                    if "src" in parts:
                        idx = parts.index("src")
                        if idx + 1 < len(parts):
                            return parts[idx + 1]
        except Exception:  # noqa: BLE001
            pass
        for prop in (
            "dhcp.wlan0.ipaddress",
            "dhcp.eth0.ipaddress",
        ):
            try:
                val = self.adb.getprop(serial, prop)
                if val and val.count(".") == 3:
                    return val
            except Exception:  # noqa: BLE001
                continue
        return ""

    def ensure_ready(self, serial: str) -> str:
        ser = (serial or "").strip()
        if not ser:
            raise AdbError("serial обязателен")
        if not self.adb.prepare_for_work(ser, hold_awake=True):
            raise AdbError("Устройство не отвечает по ADB (пробудите экран вручную)")
        return ser

    def _form_factor(self, serial: str) -> tuple[str, int]:
        try:
            return self.adb.detect_form_factor(serial)
        except Exception:  # noqa: BLE001
            return "mobile", 0

    def run_action(self, serial: str, action: str) -> tuple[bool, str]:
        act = (action or "").strip().lower()
        if act not in _ACTIONS:
            return False, f"Неизвестное действие: {action}"
        ser = self.ensure_ready(serial)
        ff, sdk = self._form_factor(ser)
        is_tv = ff == "tv"
        legacy = bool(sdk and sdk < 29)
        try:
            if act == "wake":
                self.adb.nudge_awake(ser, form_factor=ff)
                return True, "ТВ разбужен" if is_tv else "Экран разбужен"
            if act in ("play_alarm", "play_sound"):
                self.adb.play_alarm_tone(ser, form_factor=ff)
                if is_tv:
                    return True, "Media-сигнал на ТВ"
                return True, "Alarm (~8 с), без смены громкости"
            if act == "play_timer":
                self.adb.play_set_timer(ser, form_factor=ff)
                return True, "Таймер 1 с (может зазвонить после разблокировки)"
            if act == "mute":
                if is_tv or legacy:
                    self.adb.set_media_muted(ser, True)
                    return True, "Медиа без звука" if is_tv else "Без звука"
                self.adb.set_ringer_mode(ser, "silent")
                return True, "Беззвучный режим"
            if act == "unmute":
                if is_tv or legacy:
                    self.adb.set_media_muted(ser, False)
                    return True, "Медиа звук вкл." if is_tv else "Звук включён"
                self.adb.set_ringer_mode(ser, "normal")
                return True, "Звук включён"
            if act == "volume_up":
                steps = 2 if (is_tv or legacy) else 1
                self.adb.adjust_volume(ser, "up", steps=steps)
                return True, "Громкость +"
            if act == "volume_down":
                steps = 2 if (is_tv or legacy) else 1
                self.adb.adjust_volume(ser, "down", steps=steps)
                return True, "Громкость −"
            if act == "stay_awake_on":
                self.adb.set_stay_awake(ser, True)
                return True, "Экран не гаснет (stayon)"
            if act == "stay_awake_off":
                self.adb.set_stay_awake(ser, False)
                return True, "Обычный сон экрана"
        except AdbError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return False, "Не выполнено"

    def capture_screenshot(
        self, serial: str, *, also_as: str | None = None
    ) -> tuple[Path, str]:
        """Capture PNG to cache; return (path, media_url).

        Writes under the working serial and optionally ``also_as`` (UI serial),
        so reopen-by-alias still finds the last shot.
        """
        ser = self.ensure_ready(serial)
        ff, _sdk = self._form_factor(ser)
        self.adb.nudge_awake(ser, form_factor=ff)
        time.sleep(0.35 if ff == "tv" else 0.25)
        data = self.adb.screencap_png(ser, timeout=60.0 if ff == "tv" else 45.0)
        ensure_data_dirs()
        SCREENCAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = screenshot_cache_path(ser)
        path.write_bytes(data)
        alias = (also_as or "").strip()
        if alias and _safe_serial_name(alias) != _safe_serial_name(ser):
            try:
                screenshot_cache_path(alias).write_bytes(data)
            except OSError:
                pass
        url = cached_screenshot_url(ser, alias)
        return path, url or f"/api/media/file?path={quote(str(path.resolve()), safe='')}&t={int(time.time() * 1000)}"
