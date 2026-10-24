from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from app.adb.client import AdbError
from app.adb.winproc import hidden_run_kwargs
from app.paths import LOCAL_ADB, PLATFORM_TOOLS_DIR, TOOLS_DIR, ensure_data_dirs

# Official Google Windows platform-tools (always latest)
PLATFORM_TOOLS_ZIP_URL = (
    "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
)

# Wireless debugging / adb pair needs a modern adb (ancient PATH copies like 1.0.31 fail)
MIN_ADB_VERSION = (1, 0, 40)

_VERSION_RE = re.compile(r"version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)

ProgressCb = Callable[[str], None]


def local_adb_path() -> Path:
    return LOCAL_ADB


def parse_adb_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def probe_adb(adb_exe: str | Path) -> tuple[bool, str, tuple[int, int, int] | None]:
    """Return (ok, version_line_or_error, version_tuple)."""
    exe = Path(adb_exe)
    if not exe.is_file():
        return False, f"File not found: {exe}", None
    try:
        completed = subprocess.run(
            [str(exe), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            **hidden_run_kwargs(),
        )
    except OSError as exc:
        return False, str(exc), None
    except subprocess.TimeoutExpired:
        return False, "adb version timed out", None

    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        return False, text or f"adb version failed ({completed.returncode})", None
    version = parse_adb_version(text)
    first = text.splitlines()[0] if text else str(exe)
    return True, first, version


def is_version_ok(version: tuple[int, int, int] | None) -> bool:
    if version is None:
        return False
    return version >= MIN_ADB_VERSION


def find_path_adb() -> Path | None:
    found = shutil.which("adb")
    return Path(found) if found else None


def download_platform_tools(
    *,
    progress: ProgressCb | None = None,
    timeout: float = 180.0,
) -> Path:
    """Download and extract platform-tools; return path to adb.exe."""
    ensure_data_dirs()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = TOOLS_DIR / "platform-tools-windows.zip"

    def report(msg: str) -> None:
        if progress:
            progress(msg)

    report("Downloading Android platform-tools…")
    try:
        with httpx.stream("GET", PLATFORM_TOOLS_ZIP_URL, follow_redirects=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with zip_path.open("wb") as out:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done * 100 / total)
                        report(f"Downloading platform-tools… {pct}%")
    except httpx.HTTPError as exc:
        raise AdbError(f"Failed to download platform-tools: {exc}") from exc

    report("Extracting platform-tools…")
    extract_root = TOOLS_DIR
    # Remove previous extract so we get a clean copy
    if PLATFORM_TOOLS_DIR.is_dir():
        shutil.rmtree(PLATFORM_TOOLS_DIR, ignore_errors=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Archive contains top-level "platform-tools/"
            zf.extractall(extract_root)
    except zipfile.BadZipFile as exc:
        raise AdbError(f"Downloaded platform-tools archive is corrupt: {exc}") from exc
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass

    adb = LOCAL_ADB
    ok, msg, version = probe_adb(adb)
    if not ok:
        raise AdbError(f"Downloaded adb does not work: {msg}")
    if not is_version_ok(version):
        raise AdbError(f"Downloaded adb is unexpectedly old: {msg}")
    report(f"Local adb ready: {msg}")
    return adb


def resolve_adb(
    preferred: str | None = None,
    *,
    download: bool = True,
    progress: ProgressCb | None = None,
) -> tuple[str, str]:
    """
    Resolve adb executable path.

    Order:
      1) preferred / settings path (if modern)
      2) PATH (if modern)
      3) already downloaded local platform-tools
      4) download local platform-tools (if download=True)

    Returns (adb_path, status_message).
    """
    ensure_data_dirs()

    candidates: list[tuple[str, Path | None]] = []
    if preferred and preferred.strip():
        candidates.append(("settings", Path(preferred.strip())))
    candidates.append(("PATH", find_path_adb()))
    candidates.append(("local", LOCAL_ADB if LOCAL_ADB.is_file() else None))

    rejected_old: list[str] = []
    for source, path in candidates:
        if path is None:
            continue
        ok, msg, version = probe_adb(path)
        if not ok:
            continue
        if not is_version_ok(version):
            rejected_old.append(f"{source}: {msg}")
            continue
        label = {
            "settings": "из настроек",
            "PATH": "из PATH",
            "local": "локальная копия",
        }.get(source, source)
        return str(path.resolve()), f"{msg} ({label})"

    if not download:
        detail = "; ".join(rejected_old) if rejected_old else "adb not found"
        raise AdbError(
            f"Suitable adb not found (need >= {MIN_ADB_VERSION[0]}.{MIN_ADB_VERSION[1]}.{MIN_ADB_VERSION[2]}). {detail}"
        )

    if rejected_old and progress:
        progress("PATH/settings adb is too old; downloading modern platform-tools…")

    adb = download_platform_tools(progress=progress)
    ok, msg, _version = probe_adb(adb)
    return str(adb.resolve()), f"{msg} (скачана локально)"
