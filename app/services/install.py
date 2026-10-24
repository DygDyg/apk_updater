from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.adb.client import AdbClient, AdbError
from app.db.database import Database
from app.services.install_errors import enrich_install_message

if TYPE_CHECKING:
    from app.services.devices import DeviceService


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _apk_min_sdk(path: Path) -> int | None:
    try:
        from pyaxmlparser import APK as AxmlApk

        raw = AxmlApk(str(path)).get_min_sdk_version()
        if raw is None or raw == "":
            return None
        return int(str(raw).strip())
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class InstallResult:
    serial: str
    success: bool
    message: str
    apk_path: str
    error_code: str = ""
    error_title: str = ""
    error_hint: str = ""
    actions: tuple[str, ...] = ()
    raw_message: str = ""

    def as_progress_fields(self) -> dict:
        return {
            "detail": self.message,
            "raw_detail": self.raw_message or self.message,
            "error_code": self.error_code,
            "error_title": self.error_title,
            "error_hint": self.error_hint,
            "actions": list(self.actions),
            "apk_path": self.apk_path,
        }


def _from_adb_text(
    serial: str,
    *,
    success: bool,
    text: str,
    apk_path: str,
) -> InstallResult:
    raw = text or ("Success" if success else "Install failed")
    if success:
        return InstallResult(
            serial=serial,
            success=True,
            message=raw,
            apk_path=apk_path,
            raw_message=raw,
        )
    display, classified = enrich_install_message(raw)
    if classified is None:
        return InstallResult(
            serial=serial,
            success=False,
            message=display,
            apk_path=apk_path,
            raw_message=raw,
        )
    return InstallResult(
        serial=serial,
        success=False,
        message=display,
        apk_path=apk_path,
        error_code=classified.code,
        error_title=classified.title,
        error_hint=classified.hint,
        actions=classified.actions,
        raw_message=raw,
    )


class InstallService:
    def __init__(
        self,
        db: Database,
        adb: AdbClient,
        devices: DeviceService | None = None,
    ) -> None:
        self.db = db
        self.adb = adb
        self.devices = devices

    def install_on_devices(
        self,
        apk_path: str | Path,
        serials: list[str],
        *,
        item_progress: Callable[[str, str, str], None] | None = None,
        allow_downgrade: bool = False,
        split_apks: list[str | Path] | tuple[str | Path, ...] | None = None,
        obb_files: list[str | Path] | tuple[str | Path, ...] | None = None,
        package_name: str = "",
    ) -> list[InstallResult]:
        path = Path(apk_path)
        if not path.is_file():
            raise AdbError(f"APK file not found: {path}")
        if path.suffix.lower() != ".apk":
            raise AdbError("Selected file must be an .apk")
        if not serials:
            raise AdbError("No target devices selected")

        extras = [Path(p) for p in (split_apks or ()) if Path(p).is_file()]
        all_apks = [path, *extras] if extras else [path]
        for extra in extras:
            if extra.suffix.lower() != ".apk":
                raise AdbError(f"Split must be an .apk: {extra}")

        apk_min_sdk = _apk_min_sdk(path)
        obbs = [Path(p) for p in (obb_files or ()) if Path(p).is_file()]
        pkg = (package_name or "").strip()
        results: list[InstallResult] = []
        for serial in serials:
            if item_progress:
                detail = (
                    "Установка с даунгрейдом…"
                    if allow_downgrade
                    else (
                        f"Установка {len(all_apks)} APK…"
                        if len(all_apks) > 1
                        else "Установка на устройство…"
                    )
                )
                item_progress(serial, "installing", detail)
            candidates = (
                self.devices.transport_candidates(serial) if self.devices is not None else [serial]
            )
            if not candidates:
                candidates = [serial]

            last: InstallResult | None = None
            for cand in candidates:
                try:
                    try:
                        ready = self.adb.prepare_for_work(cand, hold_awake=True)
                    except Exception:  # noqa: BLE001
                        ready = False
                    if not ready:
                        last = _from_adb_text(
                            serial,
                            success=False,
                            text=f"Устройство не отвечает после пробуждения [tried {cand}]",
                            apk_path=str(path),
                        )
                        continue
                    try:
                        device_sdk = self._device_sdk(serial, cand)
                        if (
                            apk_min_sdk is not None
                            and device_sdk > 0
                            and device_sdk < apk_min_sdk
                        ):
                            last = _from_adb_text(
                                serial,
                                success=False,
                                text=(
                                    f"Failure [INSTALL_FAILED_OLDER_SDK]: "
                                    f"minSdk={apk_min_sdk} deviceSdk={device_sdk}"
                                ),
                                apk_path=str(path),
                            )
                            break
                        if len(all_apks) > 1:
                            adb_result = self.adb.install_multiple(
                                cand,
                                all_apks,
                                replace=True,
                                allow_downgrade=allow_downgrade,
                            )
                        else:
                            adb_result = self.adb.install(
                                cand, path, replace=True, allow_downgrade=allow_downgrade
                            )
                        text = adb_result.combined
                        lowered = text.lower()
                        success = (
                            adb_result.ok
                            and "success" in lowered
                            and "failure" not in lowered
                            and "failed" not in lowered
                        )
                        if success and obbs and pkg:
                            if item_progress:
                                item_progress(serial, "installing", "Копирование OBB…")
                            self._push_obb_files(cand, pkg, obbs)
                        note = ""
                        if cand != serial and len(candidates) > 1:
                            note = f" [via {cand}]"
                        message = (text or ("Success" if success else "Install failed")) + note
                        last = _from_adb_text(
                            serial, success=success, text=message, apk_path=str(path)
                        )
                        if success:
                            break
                    finally:
                        try:
                            self.adb.release_after_work(cand)
                        except Exception:  # noqa: BLE001
                            pass
                except AdbError as exc:
                    last = _from_adb_text(
                        serial,
                        success=False,
                        text=f"{exc} [tried {cand}]",
                        apk_path=str(path),
                    )
            if last is None:
                last = _from_adb_text(
                    serial,
                    success=False,
                    text="Устройство не отвечает (пропуск)",
                    apk_path=str(path),
                )
            results.append(last)
            self._record_history(last)
        return results

    def _push_obb_files(self, serial: str, package_name: str, obb_files: list[Path]) -> None:
        remote_dir = f"/sdcard/Android/obb/{package_name}"
        self.adb.shell(serial, f"mkdir -p {remote_dir}", check=False, timeout=30.0)
        for obb in obb_files:
            remote = f"{remote_dir}/{obb.name}"
            self.adb.push(serial, obb, remote, timeout=600.0)

    def uninstall_on_device(self, serial: str, package_name: str) -> InstallResult:
        candidates = (
            self.devices.transport_candidates(serial) if self.devices is not None else [serial]
        )
        if not candidates:
            candidates = [serial]
        last: InstallResult | None = None
        for cand in candidates:
            try:
                try:
                    ready = self.adb.prepare_for_work(cand, hold_awake=True)
                except Exception:  # noqa: BLE001
                    ready = False
                if not ready:
                    last = InstallResult(
                        serial=serial,
                        success=False,
                        message=f"Устройство не отвечает после пробуждения [tried {cand}]",
                        apk_path="",
                        raw_message="",
                    )
                    continue
                try:
                    adb_result = self.adb.uninstall(cand, package_name)
                    text = adb_result.combined
                    lowered = text.lower()
                    success = adb_result.ok and "success" in lowered and "failure" not in lowered
                    note = f" [via {cand}]" if cand != serial and len(candidates) > 1 else ""
                    last = InstallResult(
                        serial=serial,
                        success=success,
                        message=(text or ("Success" if success else "Uninstall failed")) + note,
                        apk_path="",
                        raw_message=(text or "") + note,
                    )
                    if success:
                        break
                finally:
                    try:
                        self.adb.release_after_work(cand)
                    except Exception:  # noqa: BLE001
                        pass
            except AdbError as exc:
                last = InstallResult(
                    serial=serial,
                    success=False,
                    message=f"{exc} [tried {cand}]",
                    apk_path="",
                    raw_message=str(exc),
                )
        if last is None:
            last = InstallResult(
                serial=serial,
                success=False,
                message="Устройство не отвечает (пропуск)",
                apk_path="",
                raw_message="",
            )
        return last

    def _device_sdk(self, serial: str, transport: str) -> int:
        """Best-effort API level for the target device (getprop, then DB profile)."""
        for target in (transport, serial):
            try:
                raw = (self.adb.getprop(target, "ro.build.version.sdk") or "").strip()
                if raw.isdigit():
                    return int(raw)
            except Exception:  # noqa: BLE001
                pass
        if self.devices is not None:
            try:
                for rec in self.devices.list_known():
                    if rec.serial == serial or transport in (rec.transports or []):
                        sdk = str(rec.sdk or "").strip()
                        if sdk.isdigit():
                            return int(sdk)
                        break
            except Exception:  # noqa: BLE001
                pass
        return 0

    def _record_history(self, result: InstallResult) -> None:
        self.db.execute(
            "INSERT INTO install_history(device_serial, package_name, apk_path, success, message, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                result.serial,
                None,
                result.apk_path,
                1 if result.success else 0,
                result.message,
                _utc_now(),
            ),
        )
