from __future__ import annotations

import sys
from pathlib import Path


def _bundle_dir() -> Path:
    """Read-only assets (web/dist) — PyInstaller _MEIPASS or project root."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _runtime_dir() -> Path:
    """Writable root for data/ — next to exe when frozen, else project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BUNDLE_DIR = _bundle_dir()
ROOT_DIR = _runtime_dir()
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache" / "apk"
ICON_CACHE_DIR = DATA_DIR / "cache" / "icons"
DEVICE_CACHE_DIR = DATA_DIR / "cache" / "devices"
SCREENCAP_CACHE_DIR = DATA_DIR / "cache" / "screencaps"
LOG_DIR = DATA_DIR / "logs"
TOOLS_DIR = DATA_DIR / "tools"
PLATFORM_TOOLS_DIR = TOOLS_DIR / "platform-tools"
LOCAL_ADB = PLATFORM_TOOLS_DIR / "adb.exe"
DB_PATH = DATA_DIR / "apk_updater.db"
WEB_DIST_DIR = BUNDLE_DIR / "web" / "dist"
PLAY_ALARM_DEX = BUNDLE_DIR / "app" / "resources" / "play_alarm.dex"


def play_alarm_dex_path() -> Path | None:
    """Dex that beeps on USAGE_ALARM (audible on secure lock screens)."""
    for cand in (
        PLAY_ALARM_DEX,
        BUNDLE_DIR / "resources" / "play_alarm.dex",
        Path(__file__).resolve().parent / "resources" / "play_alarm.dex",
    ):
        if cand.is_file():
            return cand
    return None


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SCREENCAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
