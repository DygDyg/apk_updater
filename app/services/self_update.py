"""Check / download / apply portable app updates from GitHub Releases."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.build_info import BUILD_ID
from app.paths import DATA_DIR, ROOT_DIR

log = logging.getLogger("apk_updater.self_update")

DEFAULT_GITHUB_REPO = "DygDyg/apk_updater"
ASSET_NAME = "APKUpdater-windows-x64.zip"
TAG_PREFIX = "build-"
_USER_AGENT = "apk-updater-self-update"


@dataclass
class RemoteRelease:
    build_id: str
    tag: str
    download_url: str
    name: str = ""


@dataclass
class CheckResult:
    ok: bool
    local_build: str
    latest_build: str = ""
    update_available: bool = False
    download_url: str = ""
    message: str = ""
    frozen: bool = False


@dataclass
class ApplyResult:
    ok: bool
    message: str
    restarting: bool = False


def github_repo() -> str:
    raw = (os.environ.get("APK_UPDATER_GITHUB_REPO") or DEFAULT_GITHUB_REPO).strip()
    return raw or DEFAULT_GITHUB_REPO


def local_build_id() -> str:
    return (BUILD_ID or "dev").strip() or "dev"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def parse_build_from_tag(tag: str) -> str:
    t = (tag or "").strip()
    if t.startswith(TAG_PREFIX):
        return t[len(TAG_PREFIX) :]
    return t


def is_newer(remote: str, local: str) -> bool:
    r = (remote or "").strip()
    loc = (local or "").strip()
    if not r:
        return False
    if not loc or loc == "dev":
        return True
    return r > loc


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = (os.environ.get("APK_UPDATER_GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_latest_release(timeout: float = 30.0) -> RemoteRelease:
    repo = github_repo()
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_headers()) as client:
        r = client.get(url)
        if r.status_code == 404:
            raise RuntimeError(f"Релизов ещё нет в {repo}")
        r.raise_for_status()
        data = r.json()
    tag = str(data.get("tag_name") or "")
    build_id = parse_build_from_tag(tag)
    if not build_id:
        raise RuntimeError(f"Пустой tag_name в latest release ({repo})")
    assets = data.get("assets") or []
    download_url = ""
    for asset in assets:
        name = str(asset.get("name") or "")
        if name == ASSET_NAME or name.endswith(".zip"):
            download_url = str(asset.get("browser_download_url") or "")
            if name == ASSET_NAME:
                break
    if not download_url:
        raise RuntimeError(f"В релизе {tag} нет ZIP-ассета ({ASSET_NAME})")
    return RemoteRelease(
        build_id=build_id,
        tag=tag,
        download_url=download_url,
        name=str(data.get("name") or tag),
    )


def check_for_update(*, persist_db: Any | None = None) -> CheckResult:
    local = local_build_id()
    frozen = is_frozen()
    try:
        remote = fetch_latest_release()
    except Exception as exc:  # noqa: BLE001
        msg = f"Не удалось проверить обновление: {exc}"
        log.warning(msg)
        if persist_db is not None:
            persist_db.set_setting("app_update_last_check", str(time.time()))
            persist_db.set_setting("app_update_last_error", str(exc)[:500])
        return CheckResult(ok=False, local_build=local, message=msg, frozen=frozen)

    available = is_newer(remote.build_id, local)
    if persist_db is not None:
        persist_db.set_setting("app_update_last_check", str(time.time()))
        persist_db.set_setting("app_update_last_error", "")
        persist_db.set_setting("app_update_latest_build", remote.build_id)
        persist_db.set_setting("app_update_download_url", remote.download_url)
        persist_db.set_setting(
            "app_update_available", "1" if available else "0"
        )

    if available:
        msg = f"Доступна сборка {remote.build_id} (сейчас {local})"
    else:
        msg = f"Установлена актуальная сборка {local}"
    return CheckResult(
        ok=True,
        local_build=local,
        latest_build=remote.build_id,
        update_available=available,
        download_url=remote.download_url if available else "",
        message=msg,
        frozen=frozen,
    )


def _staging_dir() -> Path:
    d = DATA_DIR / "cache" / "self_update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_zip(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300.0, follow_redirects=True, headers=_headers()) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes(1024 * 256):
                    fh.write(chunk)


def _extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    # Prefer folder that contains APKUpdater.exe
    exe_hits = list(dest_dir.rglob("APKUpdater.exe"))
    if exe_hits:
        return exe_hits[0].parent
    # Flat extract
    if (dest_dir / "APKUpdater.exe").is_file():
        return dest_dir
    children = [p for p in dest_dir.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    raise RuntimeError("В архиве не найден APKUpdater.exe")


def _write_apply_helper(*, staging_payload: Path, delay_sec: int) -> Path:
    """ASCII+CRLF bat: wait, copy over install dir (skip data), start exe, cleanup."""
    root = ROOT_DIR.resolve()
    exe = (root / "APKUpdater.exe").resolve()
    staging = staging_payload.resolve()
    helper = DATA_DIR / "_self_update_apply.bat"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # robocopy exit codes 0-7 are success-ish
    lines = [
        "@echo off",
        "setlocal EnableExtensions EnableDelayedExpansion",
        f'cd /d "{root}"',
        f"timeout /t {max(2, int(delay_sec))} /nobreak >nul",
        f'if not exist "{staging}\\APKUpdater.exe" (',
        "  echo ERROR: staging missing APKUpdater.exe",
        "  exit /b 1",
        ")",
        f'robocopy "{staging}" "{root}" /E /XD data /NFL /NDL /NJH /NJS /nc /ns /np',
        "set RC=!ERRORLEVEL!",
        "if !RC! GEQ 8 (",
        "  echo ERROR: robocopy failed !RC!",
        "  exit /b 1",
        ")",
        f'if exist "{exe}" (',
        f'  start "APK Updater" /D "{root}" "{exe}" --no-browser',
        ") else (",
        "  echo ERROR: APKUpdater.exe missing after copy",
        "  exit /b 1",
        ")",
        f'rmdir /s /q "{staging.parent}" >nul 2>&1',
        'del "%~f0" >nul 2>&1',
        "",
    ]
    helper.write_bytes(("\r\n".join(lines)).encode("ascii", errors="ignore"))
    return helper


def _spawn_helper(helper: Path) -> None:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    subprocess.Popen(
        ["cmd.exe", "/c", str(helper)],
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def apply_update(
    *,
    download_url: str | None = None,
    delay_sec: int = 5,
    db: Any | None = None,
) -> ApplyResult:
    if not is_frozen():
        repo = github_repo()
        return ApplyResult(
            ok=False,
            message=(
                "Автоустановка доступна только для портативной сборки (EXE). "
                f"Скачайте ZIP с https://github.com/{repo}/releases/latest"
            ),
        )

    url = (download_url or "").strip()
    if not url and db is not None:
        url = (db.get_setting("app_update_download_url", "") or "").strip()
    if not url:
        check = check_for_update(persist_db=db)
        if not check.update_available or not check.download_url:
            return ApplyResult(
                ok=False,
                message=check.message or "Обновление не найдено",
            )
        url = check.download_url

    staging_root = _staging_dir()
    zip_path = staging_root / ASSET_NAME
    extract_to = staging_root / "extract"
    payload = staging_root / "payload"

    try:
        log.info("Downloading update from %s", url)
        _download_zip(url, zip_path)
        extracted = _extract_zip(zip_path, extract_to)
        if payload.exists():
            shutil.rmtree(payload, ignore_errors=True)
        shutil.copytree(extracted, payload)
        helper = _write_apply_helper(staging_payload=payload, delay_sec=delay_sec)
        _spawn_helper(helper)
    except Exception as exc:  # noqa: BLE001
        log.exception("apply_update failed")
        return ApplyResult(ok=False, message=f"Не удалось применить обновление: {exc}")

    from app.runtime_control import RUNTIME

    # Self-update helper starts the new EXE; do not spawn normal restart.
    RUNTIME.request_shutdown(grace_sec=1.0)

    return ApplyResult(
        ok=True,
        message="Обновление скачано, перезапуск…",
        restarting=True,
    )


def status_snapshot(db: Any | None = None) -> dict[str, Any]:
    local = local_build_id()
    latest = ""
    available = False
    last_check = ""
    last_error = ""
    if db is not None:
        latest = (db.get_setting("app_update_latest_build", "") or "").strip()
        available = (db.get_setting("app_update_available", "0") or "0").strip() in {
            "1",
            "true",
            "yes",
        }
        last_check = (db.get_setting("app_update_last_check", "") or "").strip()
        last_error = (db.get_setting("app_update_last_error", "") or "").strip()
        if latest:
            available = is_newer(latest, local)
        dismissed = (db.get_setting("app_update_dismissed_build", "") or "").strip()
        if available and dismissed and dismissed == latest:
            available = False
    return {
        "build_id": local,
        "latest_build": latest,
        "update_available": available,
        "last_check": last_check,
        "last_error": last_error,
        "frozen": is_frozen(),
        "github_repo": github_repo(),
    }


_SAFE_BUILD_RE = re.compile(r"^\d{8}-\d{6}$")


def valid_build_id(value: str) -> bool:
    return bool(_SAFE_BUILD_RE.match((value or "").strip()))
