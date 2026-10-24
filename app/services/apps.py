from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.adb.client import AdbClient, AdbError
from app.paths import ICON_CACHE_DIR, ensure_data_dirs

ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class AppInfo:
    package_name: str
    label: str
    version_name: str
    version_code: str
    installer: str = "unknown"
    apk_path: str = ""
    icon_path: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.package_name

    def has_real_label(self) -> bool:
        lab = (self.label or "").strip()
        return bool(lab) and lab != self.package_name


def _is_real_label(label: str, package: str) -> bool:
    lab = (label or "").strip()
    return bool(lab) and lab != package


_PKG_VERSION = re.compile(
    r"^package:(?P<pkg>[\w.]+)(?:\s+versionCode:(?P<code>\d+))?\s*$"
)
_PKG_PATH = re.compile(r"^package:(?P<path>.+?)=(?P<pkg>[\w.]+)\s*$")
_DUMP_PACKAGE = re.compile(r"^Package \[([\w.]+)\]")
_VERSION_NAME = re.compile(r"versionName=(\S+)")
_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_INSTALLER = re.compile(r"installerPackageName=(\S*)")
_APP_LABEL = re.compile(r"applicationLabel=(.+)$")

_DENSITY_ORDER = (
    "xxxhdpi",
    "xxhdpi",
    "xhdpi",
    "hdpi",
    "mdpi",
    "anydpi",
    "nodpi",
)


class AppInventoryService:
    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb
        self._store_provider = None
        ensure_data_dirs()

    def _store(self):
        if self._store_provider is None:
            from app.providers.hybrid import HybridApkProvider

            self._store_provider = HybridApkProvider()
        return self._store_provider

    def list_packages(
        self,
        serial: str,
        *,
        third_party_only: bool = True,
        enrich: bool = True,
        max_workers: int = 4,
        progress: ProgressCb | None = None,
        meta_timeout: float = 90.0,
    ) -> list[AppInfo]:
        def report(msg: str) -> None:
            if progress:
                progress(msg)

        report("Reading package list…")
        packages = self._list_package_names(serial, third_party_only=third_party_only)
        if not packages:
            return []

        report("Reading versions…")
        meta = self._bulk_package_meta(serial, timeout=meta_timeout)
        paths = self._list_package_paths(serial, third_party_only=third_party_only)

        apps: list[AppInfo] = []
        for pkg, code_hint in packages.items():
            info = meta.get(pkg, {})
            version_code = str(info.get("version_code") or code_hint or "?")
            version_name = str(info.get("version_name") or "?")
            installer = str(info.get("installer") or "unknown")
            label = str(info.get("label") or "")
            apps.append(
                AppInfo(
                    package_name=pkg,
                    label=label or pkg,
                    version_name=version_name,
                    version_code=version_code,
                    installer=installer,
                    apk_path=paths.get(pkg, ""),
                )
            )

        # dumpsys often omits applicationLabel on HyperOS / bulk dump — restore
        # names (and icons) from local icon cache before optional full enrich.
        apps = self._hydrate_from_cache(apps)

        if enrich:
            report(f"Loading names and icons (0/{len(apps)})…")
            apps = self._enrich_apps(serial, apps, max_workers=max_workers, progress=progress)

        apps.sort(key=lambda a: a.display_label.lower())
        return apps

    def _label_from_any_cache(self, package: str) -> str:
        """Best label from any cached version of this package."""
        safe_pkg = re.sub(r"[^\w.]+", "_", package)
        try:
            metas = sorted(
                ICON_CACHE_DIR.glob(f"{safe_pkg}__*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return ""
        for meta_path in metas:
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                label = str(data.get("label") or "").strip()
                if _is_real_label(label, package):
                    return label
            except (OSError, json.JSONDecodeError):
                continue
        return ""

    def _hydrate_from_cache(self, apps: list[AppInfo]) -> list[AppInfo]:
        """Fill missing human labels / icons from ICON_CACHE without ADB pulls."""
        out: list[AppInfo] = []
        for app in apps:
            label = app.label
            icon = app.icon_path
            if not _is_real_label(label, app.package_name) or not icon:
                cached = self._load_cached(app.package_name, app.version_code)
                if cached:
                    c_label, c_icon = cached
                    if not _is_real_label(label, app.package_name) and _is_real_label(
                        c_label, app.package_name
                    ):
                        label = c_label
                    if not icon and c_icon:
                        icon = c_icon
            if not _is_real_label(label, app.package_name):
                alt = self._label_from_any_cache(app.package_name)
                if alt:
                    label = alt
            out.append(
                AppInfo(
                    package_name=app.package_name,
                    label=label or app.package_name,
                    version_name=app.version_name,
                    version_code=app.version_code,
                    installer=app.installer,
                    apk_path=app.apk_path,
                    icon_path=icon or "",
                )
            )
        return out

    def _cache_stem(self, package: str, version_code: str) -> str:
        safe_pkg = re.sub(r"[^\w.]+", "_", package)
        safe_ver = re.sub(r"[^\w]+", "_", version_code or "0")
        return f"{safe_pkg}__{safe_ver}"

    def _icon_file(self, package: str, version_code: str) -> Path:
        return ICON_CACHE_DIR / f"{self._cache_stem(package, version_code)}.png"

    def _meta_file(self, package: str, version_code: str) -> Path:
        return ICON_CACHE_DIR / f"{self._cache_stem(package, version_code)}.json"

    def _icon_looks_ok(self, icon_path: Path) -> bool:
        if not icon_path.is_file():
            return False
        try:
            with Image.open(icon_path) as img:
                w, h = img.size
            if w < 8 or h < 8:
                return False
            if max(w, h) / min(w, h) > 1.35:
                # Cached strip from older builds — force re-extract
                return False
            return True
        except OSError:
            return False

    def _load_cached(self, package: str, version_code: str) -> tuple[str, str] | None:
        meta_path = self._meta_file(package, version_code)
        icon_path = self._icon_file(package, version_code)
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            label = str(data.get("label") or "")
            icon = ""
            if self._icon_looks_ok(icon_path):
                icon = str(icon_path)
            elif icon_path.is_file():
                try:
                    icon_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if label or icon:
                return label, icon
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _normalize_icon(self, icon_bytes: bytes, *, size: int = 96) -> Image.Image | None:
        """Decode icon into a square RGBA plate; skip banners / strips."""
        try:
            img = Image.open(io.BytesIO(icon_bytes))
            img = img.convert("RGBA")
        except OSError:
            return None
        w, h = img.size
        if w < 8 or h < 8:
            return None
        # Adaptive / wrong mipmap hits are often wide bars or 1×N strips
        if max(w, h) / min(w, h) > 2.5:
            return None
        # Reject nearly empty / fully transparent
        alpha = img.getchannel("A") if "A" in img.getbands() else None
        if alpha is not None:
            extrema = alpha.getextrema()
            if extrema and extrema[1] < 8:
                return None
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        tw, th = img.size
        canvas.paste(img, ((size - tw) // 2, (size - th) // 2), img)
        return canvas

    def _save_cache(self, package: str, version_code: str, label: str, icon_bytes: bytes | None) -> str:
        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        meta_path = self._meta_file(package, version_code)
        icon_path = self._icon_file(package, version_code)
        icon_saved = ""
        if icon_bytes:
            try:
                img = self._normalize_icon(icon_bytes)
                if img is not None:
                    img.save(icon_path, format="PNG")
                    icon_saved = str(icon_path)
                elif icon_path.is_file():
                    icon_path.unlink(missing_ok=True)
            except OSError:
                icon_saved = ""
        meta_path.write_text(
            json.dumps({"package": package, "version_code": version_code, "label": label}, ensure_ascii=False),
            encoding="utf-8",
        )
        return icon_saved

    def _icon_from_store(self, package: str, version_code: str, label: str) -> str:
        """Download app icon from an enabled store when APK extract failed."""
        try:
            import httpx

            from app.providers.http_util import DEFAULT_TIMEOUT

            details = self._store().get_details(package)
            icon_url = (details.icon_url if details else "") or ""
            if not icon_url.strip():
                return ""
            with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(icon_url.strip())
                resp.raise_for_status()
                icon_bytes = resp.content
            if not icon_bytes:
                return ""
            return self._save_cache(package, version_code, label or package, icon_bytes)
        except Exception:  # noqa: BLE001
            return ""

    def _enrich_apps(
        self,
        serial: str,
        apps: list[AppInfo],
        *,
        max_workers: int,
        progress: ProgressCb | None,
    ) -> list[AppInfo]:
        total = len(apps)
        done = 0
        enriched: dict[str, AppInfo] = {a.package_name: a for a in apps}

        def one(app: AppInfo) -> AppInfo:
            cached = self._load_cached(app.package_name, app.version_code)
            if cached:
                label, icon = cached
                if icon:
                    return AppInfo(
                        package_name=app.package_name,
                        label=label or app.label,
                        version_name=app.version_name,
                        version_code=app.version_code,
                        installer=app.installer,
                        apk_path=app.apk_path,
                        icon_path=icon,
                    )
                # Label cached but icon missing/bad — re-pull icon only
                new_label, new_icon = self._extract_from_device(serial, app)
                label = new_label or label or app.label
                if not new_icon:
                    new_icon = self._icon_from_store(
                        app.package_name, app.version_code, label
                    )
                return AppInfo(
                    package_name=app.package_name,
                    label=label,
                    version_name=app.version_name,
                    version_code=app.version_code,
                    installer=app.installer,
                    apk_path=app.apk_path,
                    icon_path=new_icon,
                )
            label, icon = self._extract_from_device(serial, app)
            label = label or app.label
            if not icon:
                icon = self._icon_from_store(app.package_name, app.version_code, label)
            return AppInfo(
                package_name=app.package_name,
                label=label,
                version_name=app.version_name,
                version_code=app.version_code,
                installer=app.installer,
                apk_path=app.apk_path,
                icon_path=icon,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(one, app): app.package_name for app in apps}
            for fut in as_completed(futures):
                pkg = futures[fut]
                done += 1
                if progress:
                    progress(f"Loading names and icons ({done}/{total})…")
                try:
                    enriched[pkg] = fut.result()
                except Exception:  # noqa: BLE001 — keep partial results
                    pass

        return list(enriched.values())

    def _extract_from_device(self, serial: str, app: AppInfo) -> tuple[str, str]:
        """Pull APK (or use dumpsys label) and extract display name + icon."""
        label = ""
        icon_path = ""

        # Fast label from dumpsys for this package
        try:
            dump = self.adb.shell(
                serial,
                f"dumpsys package {app.package_name} | grep -m 3 applicationLabel=",
                check=False,
                timeout=20.0,
            )
            for line in (dump.stdout or "").splitlines():
                m = _APP_LABEL.search(line.strip())
                if m:
                    candidate = m.group(1).strip()
                    if candidate and candidate.lower() not in ("null", "false"):
                        label = candidate
                        break
        except AdbError:
            pass

        remote = app.apk_path
        if not remote:
            return label or app.package_name, ""

        # Prefer base.apk if path points to a split directory entry
        if remote.endswith(".apk"):
            remotes = [remote]
        else:
            remotes = [remote]

        with tempfile.TemporaryDirectory(prefix="apkupd_") as tmp:
            local_apk = Path(tmp) / "app.apk"
            pulled = False
            for remote_path in remotes:
                result = self.adb.pull(serial, remote_path, local_apk, timeout=180.0)
                if result.ok and local_apk.is_file() and local_apk.stat().st_size > 0:
                    pulled = True
                    break
            if not pulled:
                if label:
                    self._save_cache(app.package_name, app.version_code, label, None)
                return label or app.package_name, ""

            parsed_label, icon_bytes = self._parse_apk(local_apk)
            if parsed_label:
                label = parsed_label
            icon_path = self._save_cache(
                app.package_name,
                app.version_code,
                label or app.package_name,
                icon_bytes,
            )
        return label or app.package_name, icon_path

    def _parse_apk(self, apk_path: Path) -> tuple[str, bytes | None]:
        label = ""
        icon_bytes: bytes | None = None
        try:
            import logging

            from pyaxmlparser import APK as AxmlApk

            # Corrupt resources.xml in some APKs spam "res0 is not zero!" thousands of times.
            for _name in ("pyaxmlparser", "pyaxmlparser.arscutil", "pyaxmlparser.stringblock"):
                logging.getLogger(_name).setLevel(logging.ERROR)

            apk = AxmlApk(str(apk_path))
            try:
                label = (apk.get_app_name() or "").strip()
            except Exception:  # noqa: BLE001
                label = ""
            try:
                icon_name = apk.get_app_icon()
                if icon_name and not str(icon_name).endswith(".xml"):
                    data = apk.get_file(icon_name)
                    if data and self._normalize_icon(data) is not None:
                        icon_bytes = data
            except Exception:  # noqa: BLE001
                icon_bytes = None
        except Exception:  # noqa: BLE001
            pass

        if icon_bytes is None:
            icon_bytes = self._icon_from_zip(apk_path)
        return label, icon_bytes

    def _icon_from_zip(self, apk_path: Path) -> bytes | None:
        try:
            with zipfile.ZipFile(apk_path, "r") as zf:
                names = [n for n in zf.namelist() if n.endswith(".png") and "ic_launcher" in n.lower()]
                if not names:
                    names = [
                        n
                        for n in zf.namelist()
                        if n.endswith(".png") and ("mipmap" in n or "drawable" in n) and "launcher" in n.lower()
                    ]
                if not names:
                    return None

                def score(name: str) -> tuple[int, int, int]:
                    density = 0
                    lower = name.lower()
                    for i, d in enumerate(_DENSITY_ORDER):
                        if d in lower:
                            density = len(_DENSITY_ORDER) - i
                            break
                    # Prefer square mipmaps; avoid *-fg / foreground adaptive layers
                    fg_penalty = -2 if ("foreground" in lower or "_fg" in lower) else 0
                    round_bonus = 1 if "round" not in lower else 0
                    return (density + fg_penalty, round_bonus, len(name))

                # Prefer candidates that decode to a near-square bitmap
                ranked = sorted(names, key=score, reverse=True)
                for candidate in ranked:
                    try:
                        raw = zf.read(candidate)
                    except KeyError:
                        continue
                    if self._normalize_icon(raw) is not None:
                        return raw
                return None
        except (OSError, zipfile.BadZipFile, KeyError):
            return None

    def _list_package_names(self, serial: str, *, third_party_only: bool) -> dict[str, str]:
        flag = "-3" if third_party_only else ""
        cmd = f"pm list packages {flag} --show-versioncode".strip()
        result = self.adb.shell(serial, cmd, check=False, timeout=60.0)
        packages: dict[str, str] = {}
        if result.ok and result.stdout.strip():
            for line in result.stdout.splitlines():
                match = _PKG_VERSION.match(line.strip())
                if match:
                    packages[match.group("pkg")] = match.group("code") or ""
            if packages:
                return packages

        cmd = f"pm list packages {flag}".strip()
        result = self.adb.shell(serial, cmd, check=True, timeout=60.0)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages[line.split("package:", 1)[1].strip()] = ""
        return packages

    def _list_package_paths(self, serial: str, *, third_party_only: bool) -> dict[str, str]:
        flag = "-3" if third_party_only else ""
        cmd = f"pm list packages -f {flag}".strip()
        result = self.adb.shell(serial, cmd, check=False, timeout=60.0)
        paths: dict[str, str] = {}
        if not result.ok:
            return paths
        for line in result.stdout.splitlines():
            match = _PKG_PATH.match(line.strip())
            if match:
                paths[match.group("pkg")] = match.group("path")
        return paths

    def _bulk_package_meta(
        self, serial: str, *, timeout: float = 90.0
    ) -> dict[str, dict[str, str]]:
        # Cap dumpsys wait — frozen MIUI Wi‑Fi ADB used to hang here for minutes.
        wait = max(20.0, float(timeout))
        result = self.adb.shell(
            serial, "dumpsys package packages", check=False, timeout=wait
        )
        if not result.ok or not result.stdout:
            result = self.adb.shell(
                serial, "dumpsys package", check=False, timeout=wait
            )
        if not result.ok:
            return {}

        meta: dict[str, dict[str, str]] = {}
        current: str | None = None
        for raw in result.stdout.splitlines():
            stripped = raw.strip()
            pkg_match = _DUMP_PACKAGE.match(stripped)
            if pkg_match:
                current = pkg_match.group(1)
                meta.setdefault(current, {})
                continue
            if not current:
                continue
            if "versionName=" in stripped:
                m = _VERSION_NAME.search(stripped)
                if m and "version_name" not in meta[current]:
                    meta[current]["version_name"] = m.group(1)
            if "versionCode=" in stripped:
                m = _VERSION_CODE.search(stripped)
                if m and "version_code" not in meta[current]:
                    meta[current]["version_code"] = m.group(1)
            if "installerPackageName=" in stripped:
                m = _INSTALLER.search(stripped)
                if m and "installer" not in meta[current]:
                    value = (m.group(1) or "").strip()
                    meta[current]["installer"] = value if value and value != "null" else "unknown"
            if "applicationLabel=" in stripped:
                m = _APP_LABEL.search(stripped)
                if m and "label" not in meta[current]:
                    value = m.group(1).strip()
                    if value and value.lower() not in ("null", "false"):
                        meta[current]["label"] = value
        return meta
