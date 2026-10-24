from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.adb.winproc import hidden_run_kwargs


class AdbError(Exception):
    """Raised when an ADB command fails or ADB is unavailable."""


@dataclass(frozen=True)
class AdbResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined(self) -> str:
        parts = [p.strip() for p in (self.stdout, self.stderr) if p and p.strip()]
        return "\n".join(parts)


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    product: str = ""
    model: str = ""
    device: str = ""
    transport_id: str = ""

    @property
    def online(self) -> bool:
        return self.state == "device"

    @property
    def display_name(self) -> str:
        if self.model:
            return self.model
        if self.product:
            return self.product
        return self.serial


_DEVICE_EXTRA = re.compile(r"(\w+):(\S+)")
_DEVICE_STATES = frozenset(
    {
        "device",
        "offline",
        "unauthorized",
        "authorizing",
        "connecting",
        "no",
        "recovery",
        "sideload",
        "rescue",
        "bootloader",
        "host",
    }
)

# Own ADB daemon port so Android Studio / old PATH adb (5037) cannot kill our sessions
_APP_ADB_SERVER_SOCKET = "tcp:localhost:5038"


class AdbClient:
    def __init__(self, adb_path: str | None = None, timeout: float = 60.0) -> None:
        self._adb_path = adb_path
        self.timeout = timeout
        # serials where we turned on `svc power stayon` for a work session
        self._stayon_held: set[str] = set()

    @property
    def adb_path(self) -> str:
        from app.paths import LOCAL_ADB

        if self._adb_path:
            candidate = Path(self._adb_path)
            if candidate.is_file():
                return str(candidate)
            # Allow bare "adb" resolved via PATH
            found = shutil.which(self._adb_path)
            if found:
                return found
            raise AdbError(f"ADB path does not exist: {self._adb_path}")
        # Prefer bundled platform-tools over ancient PATH copies (they fight on :5037)
        if LOCAL_ADB.is_file():
            return str(LOCAL_ADB)
        found = shutil.which("adb")
        if found:
            return found
        raise AdbError(
            "ADB not found in PATH or local tools. "
            "Use Ensure ADB to download platform-tools automatically."
        )

    def set_adb_path(self, path: str | None) -> None:
        self._adb_path = path.strip() if path else None

    def is_available(self) -> tuple[bool, str]:
        try:
            path = self.adb_path
        except AdbError as exc:
            return False, str(exc)
        result = self.run(["version"], check=False)
        if not result.ok:
            return False, result.combined or "Failed to run adb version"
        first = (result.stdout or result.stderr).strip().splitlines()
        return True, first[0] if first else path

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Required on modern platform-tools so `adb mdns services` works
        env["ADB_MDNS_OPENSCREEN"] = "1"
        # Isolate from system/Android Studio adb on the default port
        env["ADB_SERVER_SOCKET"] = _APP_ADB_SERVER_SOCKET
        return env

    def run(
        self,
        args: list[str],
        *,
        serial: str | None = None,
        check: bool = True,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> AdbResult:
        cmd = [self.adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout if timeout is not None else self.timeout,
                input=input_text,
                check=False,
                env=self._env(),
                **hidden_run_kwargs(),
            )
        except FileNotFoundError as exc:
            raise AdbError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB command timed out: {' '.join(cmd)}") from exc

        result = AdbResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        if check and not result.ok:
            raise AdbError(result.combined or f"ADB failed ({result.returncode}): {' '.join(cmd)}")
        return result

    def pair(self, host_port: str, pairing_code: str) -> AdbResult:
        host_port = host_port.strip()
        pairing_code = pairing_code.strip()
        if not host_port or not pairing_code:
            raise AdbError("Pairing requires IP:port and pairing code")
        # adb pair accepts the code as a second argument (non-interactive)
        return self.run(["pair", host_port, pairing_code], check=False, timeout=30.0)

    def connect(self, host_port: str) -> AdbResult:
        host_port = host_port.strip()
        if not host_port:
            raise AdbError("Connect requires IP:port")
        return self.run(["connect", host_port], check=False, timeout=20.0)

    def disconnect(self, host_port: str | None = None) -> AdbResult:
        args = ["disconnect"]
        if host_port:
            args.append(host_port.strip())
        return self.run(args, check=False, timeout=15.0)

    def devices(self) -> list[AdbDevice]:
        result = self.run(["devices", "-l"], check=True)
        devices: list[AdbDevice] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            # Serial may contain spaces in rare broken listings; find real state token
            state_i = 1
            for i, tok in enumerate(parts[1:], start=1):
                if tok.lower() in _DEVICE_STATES:
                    state_i = i
                    break
            serial = " ".join(parts[:state_i]).strip()
            state = parts[state_i]
            # Console/wrapping artifacts: spaces or "(2)" before ._adb-tls-…
            serial = serial.replace(" ", "")
            serial = re.sub(r"\(\d+\)(?=\._adb)", "", serial)
            extras = dict(_DEVICE_EXTRA.findall(line))
            devices.append(
                AdbDevice(
                    serial=serial,
                    state=state,
                    product=extras.get("product", ""),
                    model=extras.get("model", "").replace("_", " "),
                    device=extras.get("device", ""),
                    transport_id=extras.get("transport_id", ""),
                )
            )
        return devices

    def shell(self, serial: str, command: str, *, check: bool = True, timeout: float | None = None) -> AdbResult:
        return self.run(["shell", command], serial=serial, check=check, timeout=timeout)

    def _probe_shell(self, serial: str, *, timeout: float = 5.0) -> bool:
        try:
            result = self.shell(serial, "echo apk_upd_ok", check=False, timeout=timeout)
        except AdbError:
            return False
        return "apk_upd_ok" in (result.stdout or "")

    def detect_form_factor(self, serial: str) -> tuple[str, int]:
        """Return (form_factor, sdk_int) where form_factor is ``tv`` or ``mobile``."""
        sdk = 0
        try:
            sdk = int((self.getprop(serial, "ro.build.version.sdk") or "0").strip() or "0")
        except ValueError:
            sdk = 0
        chars = (self.getprop(serial, "ro.build.characteristics") or "").lower()
        model = (self.getprop(serial, "ro.product.model") or "").lower()
        name = (self.getprop(serial, "ro.product.name") or "").lower()
        device = (self.getprop(serial, "ro.product.device") or "").lower()
        blob = f"{chars} {model} {name} {device}"

        leanback = False
        television = False
        try:
            r = self.shell(
                serial,
                "pm has-feature android.software.leanback ; "
                "pm has-feature android.hardware.type.television",
                check=False,
                timeout=8.0,
            )
            text = (r.stdout or "").lower()
            # pm prints true/false per line
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                leanback = lines[0] == "true"
            if len(lines) > 1:
                television = lines[1] == "true"
            if "true" in text and ("leanback" in blob or not lines):
                # Some shells echo feature name — treat any true + tv-ish name as TV
                pass
        except Exception:  # noqa: BLE001
            pass

        tv_name = any(
            x in blob
            for x in (
                "tv",
                "mitv",
                "mssp",
                "androidtv",
                "aft",  # Fire TV
                "bravia",
                "chromecast",
            )
        )
        if leanback or television or "tv" in chars or tv_name:
            return "tv", sdk
        return "mobile", sdk

    def nudge_awake(self, serial: str, *, form_factor: str | None = None) -> None:
        """Wake the display briefly.

        MIUI / HyperOS (Redmi Pad и др.) часто «замораживает» Wi‑Fi ADB, пока
        экран погашен: shell/pull висят до таймаута. KEYCODE_WAKEUP не снимает
        PIN, но обычно возвращает отклик adb.

        On Android TV also send HOME so the launcher becomes visible.
        """
        ff = form_factor
        if ff is None:
            try:
                ff, _sdk = self.detect_form_factor(serial)
            except Exception:  # noqa: BLE001
                ff = "mobile"

        for cmd in (
            "input keyevent KEYCODE_WAKEUP",
            "input keyevent 224",
        ):
            try:
                self.shell(serial, cmd, check=False, timeout=4.0)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.35)
        # Second pulse — HyperOS sometimes ignores the first
        try:
            self.shell(serial, "input keyevent KEYCODE_WAKEUP", check=False, timeout=4.0)
        except Exception:  # noqa: BLE001
            pass
        if ff == "tv":
            for cmd in (
                "input keyevent KEYCODE_HOME",
                "input keyevent 3",
            ):
                try:
                    self.shell(serial, cmd, check=False, timeout=4.0)
                except Exception:  # noqa: BLE001
                    pass

    def _reconnect_transport(self, serial: str) -> None:
        """Re-open Wi‑Fi ADB when the daemon stopped answering after sleep."""
        try:
            self.run(["reconnect"], serial=serial, check=False, timeout=8.0)
        except Exception:  # noqa: BLE001
            pass
        if ":" not in (serial or ""):
            return
        try:
            self.disconnect(serial)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
        try:
            self.connect(serial)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)

    def set_stay_awake(self, serial: str, enabled: bool) -> None:
        """Keep screen on via `svc power stayon` (helps MIUI Wi‑Fi ADB)."""
        flag = "true" if enabled else "false"
        try:
            self.shell(serial, f"svc power stayon {flag}", check=False, timeout=5.0)
        except Exception:  # noqa: BLE001
            return
        if enabled:
            self._stayon_held.add(serial)
        else:
            self._stayon_held.discard(serial)

    def prepare_for_work(
        self,
        serial: str,
        *,
        hold_awake: bool = True,
        probe_timeout: float = 4.0,
    ) -> bool:
        """Ensure shell responds before inventory/install.

        1) Fast probe — if device answers, do not wake the screen.
        2) Only on timeout/failure — aggressive wake + Wi‑Fi reconnect.
        3) If still silent — return False (caller must skip the device).

        Returns True only when a short shell probe succeeds.
        """
        serial = (serial or "").strip()
        if not serial:
            return False

        # Happy path: device already responsive (screen on / non-MIUI sleep)
        if self._probe_shell(serial, timeout=probe_timeout):
            if hold_awake:
                self.set_stay_awake(serial, True)
            return True

        # Silent — try wake (MIUI/HyperOS Wi‑Fi ADB freeze)
        self.nudge_awake(serial)
        time.sleep(0.5)
        if self._probe_shell(serial, timeout=6.0):
            if hold_awake:
                self.set_stay_awake(serial, True)
            return True

        self._reconnect_transport(serial)
        self.nudge_awake(serial)
        time.sleep(0.5)
        ok = self._probe_shell(serial, timeout=6.0)
        if ok and hold_awake:
            self.set_stay_awake(serial, True)
        return ok

    def release_after_work(self, serial: str) -> None:
        """Turn off stay-on after a work session (best-effort)."""
        serial = (serial or "").strip()
        if not serial:
            return
        self.set_stay_awake(serial, False)

    def getprop(self, serial: str, prop: str) -> str:
        result = self.shell(serial, f"getprop {prop}", check=False)
        return (result.stdout or "").strip()

    @staticmethod
    def _coerce_png(data: bytes) -> bytes | None:
        """Accept raw PNG or CRLF-corrupted / prefixed screencap output."""
        if not data:
            return None
        if data.startswith(b"\x89PNG"):
            return data
        # Classic Windows `adb shell` CRLF corruption of PNG stream
        fixed = data.replace(b"\r\n", b"\n")
        if fixed.startswith(b"\x89PNG"):
            return fixed
        idx = data.find(b"\x89PNG")
        if idx > 0:
            chunk = data[idx:]
            if chunk.startswith(b"\x89PNG"):
                return chunk
            fixed2 = chunk.replace(b"\r\n", b"\n")
            if fixed2.startswith(b"\x89PNG"):
                return fixed2
        return None

    def screencap_png(self, serial: str, *, timeout: float = 45.0) -> bytes:
        """Capture device screen as PNG bytes.

        Tries ``exec-out screencap -p`` first, then remote file + ``adb pull``
        (more reliable on Android TV / Xiaomi MiTV / flaky Wi‑Fi ADB).
        """
        errors: list[str] = []

        # 1) exec-out (fast path on phones/tablets)
        cmd = [self.adb_path, "-s", serial, "exec-out", "screencap", "-p"]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=self._env(),
                **hidden_run_kwargs(),
            )
            png = self._coerce_png(completed.stdout or b"")
            if png:
                return png
            err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            errors.append(err or "exec-out: not a PNG")
        except FileNotFoundError as exc:
            raise AdbError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired:
            errors.append("exec-out: timeout")

        # 2) screencap to file on device, then pull (TV / OEM friendly)
        remote_candidates = (
            "/data/local/tmp/apk_upd_screencap.png",
            "/sdcard/apk_upd_screencap.png",
            "/storage/emulated/0/apk_upd_screencap.png",
        )
        for remote in remote_candidates:
            try:
                self.shell(serial, f"rm -f {remote}", check=False, timeout=8.0)
                cap = self.shell(
                    serial,
                    f"screencap -p {remote}",
                    check=False,
                    timeout=timeout,
                )
                # Verify file exists and non-empty
                ls = self.shell(
                    serial,
                    f"ls -l {remote}",
                    check=False,
                    timeout=8.0,
                )
                ls_out = (ls.stdout or "").strip()
                if "No such file" in ls_out or not ls_out:
                    detail = ((cap.stderr or "") + " " + (cap.stdout or "")).strip()
                    errors.append(f"{remote}: empty ({detail or 'no file'})")
                    continue
                with tempfile.TemporaryDirectory(prefix="apk_upd_cap_") as tmp:
                    local = Path(tmp) / "cap.png"
                    pull = self.pull(serial, remote, local, timeout=min(60.0, timeout + 15.0))
                    if not local.is_file():
                        errors.append(
                            f"{remote}: pull failed "
                            f"({(pull.stderr or pull.stdout or '').strip()})"
                        )
                        continue
                    data = local.read_bytes()
                png = self._coerce_png(data)
                try:
                    self.shell(serial, f"rm -f {remote}", check=False, timeout=8.0)
                except Exception:  # noqa: BLE001
                    pass
                if png:
                    return png
                errors.append(f"{remote}: pulled but not PNG ({len(data)} bytes)")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{remote}: {exc}")

        hint = "; ".join(errors[:3]) if errors else "unknown"
        raise AdbError(
            "Скриншот недоступен на этом устройстве "
            f"(часто на Android TV / MiTV). {hint}"
        )

    def _nudge_audible(self, serial: str, *, form_factor: str = "mobile") -> None:
        """Best-effort: leave DND, raise music/alarm/ringer volumes."""
        cmds = [
            "settings put global zen_mode 0",
            # STREAM_MUSIC settings keys vary by OEM / API; try several
            "settings put system volume_music_speaker 10",
            "settings put system volume_music_hdmi 10",
            "settings put system volume_music_headphone 10",
            "settings put system volume_music 10",
            # Android 11+
            "cmd media volume --stream 3 --set 12",
            "media volume --stream 3 --set 12",
        ]
        if form_factor != "tv":
            cmds[1:1] = [
                "settings put global mode_ringer 2",
                "settings put system mode_ringer 2",
                # STREAM_RING=2, ALARM=4, NOTIFICATION=5 — alarm often plays on lock
                "cmd media volume --stream 4 --set 15",
                "cmd media volume --stream 5 --set 12",
                "cmd media volume --stream 2 --set 12",
                "media volume --stream 4 --set 15",
                "settings put system volume_alarm 15",
                "settings put system volume_ring 12",
                "settings put system sound_effects_enabled 1",
            ]
        else:
            # Unmute TV media path with a couple of volume-ups (works on API 28)
            cmds.extend(
                [
                    "input keyevent KEYCODE_VOLUME_UP",
                    "input keyevent KEYCODE_VOLUME_UP",
                ]
            )
        for cmd in cmds:
            try:
                self.shell(serial, cmd, check=False, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass

    def _prepare_audible_screen(self, serial: str, *, form_factor: str) -> None:
        """Wake display / soft-unlock so alarm & media are not deferred until unlock.

        Wi‑Fi ADB often works with the screen off; SET_TIMER then only rings after
        the user unlocks. Turn the screen on and try dismiss-keyguard first.
        """
        self.nudge_awake(serial, form_factor=form_factor)
        if form_factor == "tv":
            return
        try:
            self.shell(serial, "wm dismiss-keyguard", check=False, timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        # Keep display on so timer/media actually ring (not deferred until unlock)
        try:
            self.set_stay_awake(serial, True)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.45)

    def _alarm_sound_uris(self, serial: str) -> list[str]:
        """Default alarm/ringtone URIs — more likely to use the alarm stream."""
        uris: list[str] = []
        for key in (
            "system alarm_alert",
            "secure alarm_alert",
            "system ringtone",
            "system notification_sound",
        ):
            try:
                r = self.shell(
                    serial, f"settings get {key}", check=False, timeout=5.0
                )
            except Exception:  # noqa: BLE001
                continue
            val = (r.stdout or "").strip()
            if not val or val.lower() in ("null", "none", "-1"):
                continue
            if val not in uris:
                uris.append(val)
        return uris

    def _list_system_ui_sounds(self, serial: str) -> list[str]:
        """Collect short UI/notification ogg/wav paths that usually exist."""
        preferred = (
            "/system/media/audio/ui/Effect_Tick.ogg",
            "/system/media/audio/ui/KeypressStandard.ogg",
            "/system/media/audio/ui/KeypressSpacebar.ogg",
            "/system/media/audio/ui/VideoRecord.ogg",
            "/system/media/audio/ui/camera_click.ogg",
            "/system/media/audio/ui/Lock.ogg",
            "/system/media/audio/ui/Unlock.ogg",
            "/system/media/audio/notifications/pixiedust.ogg",
            "/system/media/audio/notifications/Twinkle.ogg",
            "/system/product/media/audio/ui/Effect_Tick.ogg",
            "/product/media/audio/ui/Effect_Tick.ogg",
            "/system/media/audio/ui/Dock.ogg",
            "/system/media/audio/ui/Undock.ogg",
        )
        found: list[str] = []
        try:
            ls = self.shell(
                serial,
                "ls /system/media/audio/ui /system/media/audio/notifications "
                "/system/product/media/audio/ui /product/media/audio/ui 2>/dev/null",
                check=False,
                timeout=10.0,
            )
            for line in (ls.stdout or "").splitlines():
                line = line.strip()
                if not line or "No such" in line or "Permission" in line:
                    continue
                # `ls dir` may print bare filenames when one dir works
                if "/" not in line and line.lower().endswith((".ogg", ".wav", ".mp3")):
                    # ambiguous which dir — skip bare names
                    continue
                low = line.lower()
                if low.endswith((".ogg", ".wav", ".mp3")) and line.startswith("/"):
                    found.append(line)
        except Exception:  # noqa: BLE001
            pass
        # Prefer known short ticks first, then whatever ls found
        ordered: list[str] = []
        for p in preferred:
            if p not in ordered:
                ordered.append(p)
        for p in found:
            if p not in ordered:
                ordered.append(p)
        return ordered

    def _try_media_play(self, serial: str, path: str) -> bool:
        """Play a file via cmd media / toybox — no Activity chooser."""
        uri = path if path.startswith("file:") else f"file://{path}"
        variants = (
            f"cmd media play {uri}",
            f"cmd media play {path}",
            # Some builds expose `media` without cmd
            f"media play {uri}",
        )
        for cmd in variants:
            try:
                result = self.shell(serial, cmd, check=False, timeout=10.0)
            except Exception:  # noqa: BLE001
                continue
            out = ((result.stdout or "") + (result.stderr or "")).lower()
            if any(
                x in out
                for x in (
                    "unknown command",
                    "not found",
                    "no such file",
                    "unable to",
                    "error:",
                    "exception",
                )
            ):
                continue
            # cmd media play often returns 0 with empty output on success
            if result.returncode == 0:
                return True
        return False

    def _ensure_play_alarm_dex(self, serial: str) -> str | None:
        """Push USAGE_ALARM beep dex to the device; return remote path or None."""
        from app.paths import play_alarm_dex_path

        local = play_alarm_dex_path()
        if local is None:
            return None
        remote = "/data/local/tmp/apk_updater_play_alarm.dex"
        need_push = True
        try:
            size = local.stat().st_size
            probe = self.shell(
                serial,
                f"toybox stat -c %s {remote} 2>/dev/null || "
                f"ls -l {remote} 2>/dev/null",
                check=False,
                timeout=6.0,
            )
            blob = (probe.stdout or "") + (probe.stderr or "")
            if str(size) in blob and "No such" not in blob:
                need_push = False
        except Exception:  # noqa: BLE001
            need_push = True
        if need_push:
            pushed = self.push(serial, local, remote, timeout=30.0)
            if not pushed.ok:
                return None
            try:
                self.shell(serial, f"chmod 644 {remote}", check=False, timeout=4.0)
            except Exception:  # noqa: BLE001
                pass
        return remote

    def _play_alarm_stream_beep(
        self, serial: str, *, duration_ms: int = 8000
    ) -> bool:
        """Beep on USAGE_ALARM via pushed dex — works with PIN/fingerprint lock.

        SET_TIMER / cmd media (MUSIC) are often deferred until unlock on MIUI etc.
        Alarm stream AudioTrack usually is not.
        """
        remote = self._ensure_play_alarm_dex(serial)
        if not remote:
            return False
        ms = max(1000, min(30000, int(duration_ms)))
        # Quote-free; app_process reads CLASSPATH from the shell environment.
        cmd = f"CLASSPATH={remote} app_process /data/local/tmp PlayAlarm {ms}"
        try:
            result = self.shell(serial, cmd, check=False, timeout=max(15.0, ms / 1000.0 + 8.0))
        except Exception:  # noqa: BLE001
            return False
        out = ((result.stdout or "") + (result.stderr or "")).lower()
        if any(
            x in out
            for x in (
                "class not found",
                "could not find",
                "unable to locate",
                "error: ",
                "exception",
                "androidruntime",
            )
        ):
            return False
        # app_process often exits 0 with empty output on success
        return result.returncode == 0

    def _resolve_form_factor(
        self, serial: str, form_factor: str | None
    ) -> tuple[str, int]:
        if form_factor is None:
            try:
                return self.detect_form_factor(serial)
            except Exception:  # noqa: BLE001
                return "mobile", 0
        try:
            sdk = int(
                (self.getprop(serial, "ro.build.version.sdk") or "0").strip() or "0"
            )
        except ValueError:
            sdk = 0
        return form_factor, sdk

    def play_set_timer(
        self, serial: str, *, form_factor: str | None = None
    ) -> None:
        """Start a 1s DeskClock SET_TIMER (no volume changes).

        On secure lock screens the ring is often deferred until unlock.
        """
        ff, _sdk = self._resolve_form_factor(serial, form_factor)
        self._prepare_audible_screen(serial, form_factor=ff)

        if ff == "tv":
            raise AdbError("Таймер недоступен на ТВ — используйте «Alarm»")

        cmds = (
            "am start -a android.intent.action.SET_TIMER "
            "--ei android.intent.extra.alarm.LENGTH 1 "
            "--ez android.intent.extra.alarm.SKIP_UI true "
            '--es android.intent.extra.alarm.MESSAGE "APK Updater" '
            "-f 0x10000000",
            "am start -a android.intent.action.SET_TIMER "
            "--ei length 1 --ez skipUi true "
            '--es message "APK Updater" -f 0x10000000',
        )
        last_err = ""
        any_ok = False
        for cmd in cmds:
            try:
                result = self.shell(serial, cmd, check=False, timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
            out = ((result.stdout or "") + (result.stderr or "")).lower()
            if "error" in out and any(
                x in out for x in ("unknown", "unable", "not found", "exception")
            ):
                last_err = (result.stderr or result.stdout or "").strip()
                continue
            if result.returncode == 0 or "starting" in out or not out.strip():
                any_ok = True
                break
        if not any_ok:
            raise AdbError(last_err or "Не удалось запустить таймер")
        time.sleep(1.0)
        try:
            self.nudge_awake(serial, form_factor=ff)
        except Exception:  # noqa: BLE001
            pass

    def play_alarm_tone(
        self, serial: str, *, form_factor: str | None = None, duration_ms: int = 8000
    ) -> None:
        """Play USAGE_ALARM beep (or TV media keys). Does not change volume."""
        ff, sdk = self._resolve_form_factor(serial, form_factor)
        self._prepare_audible_screen(serial, form_factor=ff)

        if ff != "tv":
            if self._play_alarm_stream_beep(serial, duration_ms=duration_ms):
                return
            # Fallback: system alarm URI / UI sound — still no volume nudge
            for uri in self._alarm_sound_uris(serial):
                if self._try_media_play(serial, uri):
                    time.sleep(0.5)
                    return
            for path in self._list_system_ui_sounds(serial):
                try:
                    probe = self.shell(serial, f"ls {path}", check=False, timeout=4.0)
                    if "No such" in (probe.stdout or "") + (probe.stderr or ""):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                if self._try_media_play(serial, path):
                    time.sleep(0.35)
                    return
            raise AdbError("Не удалось воспроизвести alarm-сигнал")

        # TV / very old: media keys only (no VOLUME_UP)
        tv_cmds = (
            "input keyevent KEYCODE_MEDIA_PLAY",
            "input keyevent KEYCODE_MEDIA_PLAY_PAUSE",
            "input keyevent 126",
            "input keyevent 85",
        )
        if sdk and sdk < 29:
            pass  # same path
        any_ok = False
        for cmd in tv_cmds:
            try:
                result = self.shell(serial, cmd, check=False, timeout=6.0)
            except Exception:  # noqa: BLE001
                continue
            if result.returncode == 0:
                any_ok = True
        if not any_ok:
            raise AdbError("Не удалось отправить media-сигнал на ТВ")

    def play_notification_sound(
        self, serial: str, *, form_factor: str | None = None
    ) -> None:
        """Deprecated alias — alarm tone (no volume nudge)."""
        self.play_alarm_tone(serial, form_factor=form_factor)

    def set_ringer_mode(self, serial: str, mode: str) -> None:
        """Set phone ringer mode: silent | vibrate | normal."""
        key = (mode or "").strip().lower()
        mapping = {"silent": "0", "vibrate": "1", "normal": "2"}
        if key not in mapping:
            raise AdbError(f"Unknown ringer mode: {mode}")
        value = mapping[key]
        for scope in ("global", "system", "secure"):
            try:
                self.shell(
                    serial,
                    f"settings put {scope} mode_ringer {value}",
                    check=False,
                    timeout=5.0,
                )
            except Exception:  # noqa: BLE001
                pass
        if key == "silent":
            try:
                self.shell(serial, "settings put global zen_mode 1", check=False, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
        elif key == "normal":
            try:
                self.shell(serial, "settings put global zen_mode 0", check=False, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass

    def set_media_muted(self, serial: str, muted: bool) -> None:
        """Mute/unmute media stream — for Android TV and devices without ringer."""
        if muted:
            cmds = (
                "cmd media volume --stream 3 --set 0",
                "media volume --stream 3 --set 0",
                "settings put system volume_music_speaker 0",
                "settings put system volume_music_hdmi 0",
                "settings put system volume_music 0",
                "input keyevent KEYCODE_VOLUME_MUTE",
            )
        else:
            cmds = (
                "settings put system volume_music_speaker 8",
                "settings put system volume_music_hdmi 8",
                "settings put system volume_music 8",
                "cmd media volume --stream 3 --set 10",
                "media volume --stream 3 --set 10",
                "input keyevent KEYCODE_VOLUME_UP",
                "input keyevent KEYCODE_VOLUME_UP",
            )
        for cmd in cmds:
            try:
                self.shell(serial, cmd, check=False, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass

    def adjust_volume(self, serial: str, direction: str, *, steps: int = 1) -> None:
        """Volume keyevents: up | down | mute. ``steps`` helps old TVs hear a change."""
        key = (direction or "").strip().lower()
        codes = {
            "up": "KEYCODE_VOLUME_UP",
            "down": "KEYCODE_VOLUME_DOWN",
            "mute": "KEYCODE_VOLUME_MUTE",
        }
        if key not in codes:
            raise AdbError(f"Unknown volume direction: {direction}")
        n = max(1, min(5, int(steps)))
        last: AdbResult | None = None
        for _ in range(n):
            last = self.shell(
                serial, f"input keyevent {codes[key]}", check=False, timeout=5.0
            )
            time.sleep(0.05)
        if last is not None and last.returncode != 0:
            raise AdbError((last.stderr or last.stdout or "volume failed").strip())

    def pull(self, serial: str, remote_path: str, local_path: str | Path, *, timeout: float = 60.0) -> AdbResult:
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        return self.run(
            ["pull", remote_path, str(local.resolve())],
            serial=serial,
            check=False,
            timeout=timeout,
        )

    def push(
        self,
        serial: str,
        local_path: str | Path,
        remote_path: str,
        *,
        timeout: float = 60.0,
    ) -> AdbResult:
        path = Path(local_path)
        if not path.is_file():
            raise AdbError(f"File not found: {path}")
        return self.run(
            ["push", str(path.resolve()), remote_path],
            serial=serial,
            check=False,
            timeout=timeout,
        )

    def install(
        self,
        serial: str,
        apk_path: str | Path,
        *,
        replace: bool = True,
        allow_downgrade: bool = False,
    ) -> AdbResult:
        path = Path(apk_path)
        if not path.is_file():
            raise AdbError(f"APK file not found: {path}")
        args = ["install"]
        if replace:
            args.append("-r")
        if allow_downgrade:
            args.append("-d")
        args.append(str(path.resolve()))
        # Large APKs may take a while
        return self.run(args, serial=serial, check=False, timeout=300.0)

    def install_multiple(
        self,
        serial: str,
        apk_paths: list[str | Path],
        *,
        replace: bool = True,
        allow_downgrade: bool = False,
    ) -> AdbResult:
        paths = [Path(p) for p in apk_paths]
        if not paths:
            raise AdbError("No APK files for install-multiple")
        for path in paths:
            if not path.is_file():
                raise AdbError(f"APK file not found: {path}")
        args = ["install-multiple"]
        if replace:
            args.append("-r")
        if allow_downgrade:
            args.append("-d")
        args.extend(str(p.resolve()) for p in paths)
        # Split packages / XAPK can be hundreds of MB
        return self.run(args, serial=serial, check=False, timeout=900.0)

    def uninstall(self, serial: str, package_name: str) -> AdbResult:
        pkg = (package_name or "").strip()
        if not pkg:
            raise AdbError("Package name required for uninstall")
        return self.run(
            ["uninstall", pkg],
            serial=serial,
            check=False,
            timeout=120.0,
        )

    def force_stop(self, serial: str, package_name: str) -> AdbResult:
        pkg = (package_name or "").strip()
        if not pkg:
            raise AdbError("Package name required for force-stop")
        return self.shell(serial, f"am force-stop {pkg}", check=False, timeout=30.0)

    def clear_package(
        self, serial: str, package_name: str, *, cache_only: bool = False
    ) -> AdbResult:
        pkg = (package_name or "").strip()
        if not pkg:
            raise AdbError("Package name required for clear")
        if cache_only:
            return self.shell(
                serial, f"pm clear --cache-only {pkg}", check=False, timeout=60.0
            )
        return self.shell(serial, f"pm clear {pkg}", check=False, timeout=60.0)

    def mdns_services(self) -> list[tuple[str, str, str, int]]:
        """
        Parse `adb mdns services`.
        Returns list of (instance_name, service_type, ip, port).
        """
        result = self.run(["mdns", "services"], check=False, timeout=15.0)
        if not result.ok and not result.stdout.strip():
            return []
        rows: list[tuple[str, str, str, int]] = []
        # Example: adb-XXXX  _adb-tls-connect._tcp  192.168.1.10:37123
        line_re = re.compile(
            r"^(?P<name>\S+)\s+(?P<stype>_adb(?:-tls-(?:connect|pairing))?._tcp)\s+(?P<ip>\d+\.\d+\.\d+\.\d+):(?P<port>\d+)\s*$"
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("list of"):
                continue
            match = line_re.match(line)
            if not match:
                # Broader fallback split
                parts = line.split()
                if len(parts) >= 3 and ":" in parts[-1]:
                    host, _, port_s = parts[-1].rpartition(":")
                    if host.count(".") == 3 and port_s.isdigit():
                        rows.append((parts[0], parts[1], host, int(port_s)))
                continue
            rows.append(
                (
                    match.group("name"),
                    match.group("stype"),
                    match.group("ip"),
                    int(match.group("port")),
                )
            )
        return rows
