from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from app.adb.client import AdbError
from app.adb.winproc import hidden_run_kwargs
from app.paths import TOOLS_DIR, ensure_data_dirs

SCRCPY_DIR = TOOLS_DIR / "scrcpy"
LOCAL_SCRCPY = SCRCPY_DIR / "scrcpy.exe"
GITHUB_LATEST = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
# Fallback if GitHub API is rate-limited / unreachable
FALLBACK_ZIP_URL = (
    "https://github.com/Genymobile/scrcpy/releases/download/v3.3.3/"
    "scrcpy-win64-v3.3.3.zip"
)

ProgressCb = Callable[[str], None]


def local_scrcpy_path() -> Path:
    return LOCAL_SCRCPY


def find_path_scrcpy() -> Path | None:
    found = shutil.which("scrcpy")
    return Path(found) if found else None


def probe_scrcpy(exe: str | Path) -> tuple[bool, str]:
    path = Path(exe)
    if not path.is_file():
        return False, f"File not found: {path}"
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            **hidden_run_kwargs(),
        )
    except OSError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, "scrcpy --version timed out"
    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    first = text.splitlines()[0] if text else str(path)
    if completed.returncode != 0 and "scrcpy" not in text.lower():
        return False, text or f"scrcpy failed ({completed.returncode})"
    return True, first or str(path)


def _pick_win64_asset(payload: dict) -> str | None:
    assets = payload.get("assets") or []
    preferred: list[str] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url or not name.lower().endswith(".zip"):
            continue
        low = name.lower()
        if "win64" in low and "macos" not in low and "linux" not in low:
            preferred.append(url)
    return preferred[0] if preferred else None


def resolve_download_url(*, timeout: float = 30.0) -> str:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "apk-updater",
    }
    try:
        resp = httpx.get(
            GITHUB_LATEST, headers=headers, follow_redirects=True, timeout=timeout
        )
        resp.raise_for_status()
        url = _pick_win64_asset(resp.json())
        if url:
            return url
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return FALLBACK_ZIP_URL


def download_scrcpy(
    *,
    progress: ProgressCb | None = None,
    timeout: float = 300.0,
) -> Path:
    """Download scrcpy win64 zip into data/tools/scrcpy/."""
    ensure_data_dirs()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    def report(msg: str) -> None:
        if progress:
            progress(msg)

    url = resolve_download_url()
    report(f"Скачивание scrcpy… ({url.rsplit('/', 1)[-1]})")
    zip_path = TOOLS_DIR / "scrcpy-win64.zip"
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with zip_path.open("wb") as out:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        report(f"Скачивание scrcpy… {int(done * 100 / total)}%")
    except httpx.HTTPError as exc:
        raise AdbError(f"Не удалось скачать scrcpy: {exc}") from exc

    report("Распаковка scrcpy…")
    if SCRCPY_DIR.is_dir():
        shutil.rmtree(SCRCPY_DIR, ignore_errors=True)
    SCRCPY_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(SCRCPY_DIR)
    except zipfile.BadZipFile as exc:
        raise AdbError(f"Архив scrcpy повреждён: {exc}") from exc
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass

    exe = _locate_extracted_exe(SCRCPY_DIR)
    if exe is None:
        raise AdbError("После распаковки scrcpy.exe не найден")
    # Flatten: keep tools/scrcpy/scrcpy.exe (+ dlls next to it)
    if exe.parent.resolve() != SCRCPY_DIR.resolve():
        for item in exe.parent.iterdir():
            dest = SCRCPY_DIR / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest, ignore_errors=True)
                else:
                    dest.unlink(missing_ok=True)
            shutil.move(str(item), str(dest))
        # Remove emptied nested folder(s)
        for child in list(SCRCPY_DIR.iterdir()):
            if child.is_dir() and child.name.startswith("scrcpy"):
                shutil.rmtree(child, ignore_errors=True)

    ok, msg = probe_scrcpy(LOCAL_SCRCPY)
    if not ok:
        raise AdbError(f"Скачанный scrcpy не запускается: {msg}")
    report(f"scrcpy готов: {msg}")
    return LOCAL_SCRCPY


def _locate_extracted_exe(root: Path) -> Path | None:
    direct = root / "scrcpy.exe"
    if direct.is_file():
        return direct
    for path in root.rglob("scrcpy.exe"):
        return path
    return None


def resolve_scrcpy(
    *,
    download: bool = True,
    progress: ProgressCb | None = None,
) -> tuple[str, str]:
    """Return (scrcpy_path, status_message), downloading if needed."""
    ensure_data_dirs()
    candidates: list[tuple[str, Path | None]] = [
        ("local", LOCAL_SCRCPY if LOCAL_SCRCPY.is_file() else None),
        ("PATH", find_path_scrcpy()),
    ]
    for source, path in candidates:
        if path is None:
            continue
        ok, msg = probe_scrcpy(path)
        if ok:
            label = "локальная копия" if source == "local" else "из PATH"
            return str(path.resolve()), f"{msg} ({label})"
    if not download:
        raise AdbError("scrcpy не найден")
    path = download_scrcpy(progress=progress)
    ok, msg = probe_scrcpy(path)
    return str(path.resolve()), f"{msg} (скачан локально)"
