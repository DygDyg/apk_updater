from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.adb.client import AdbClient, AdbError
from app.scrcpy.bootstrap import resolve_scrcpy


def launch_scrcpy(
    adb: AdbClient,
    serial: str,
    *,
    download: bool = True,
    title: str = "",
) -> tuple[bool, str]:
    """Ensure scrcpy exists (download if needed) and open a mirror window."""
    ser = (serial or "").strip()
    if not ser:
        raise AdbError("serial обязателен")

    # Prefer our bundled/resolved adb so scrcpy does not pick an ancient PATH copy
    adb_path = (adb.adb_path or "").strip()
    if not adb_path or not Path(adb_path).is_file():
        raise AdbError("ADB не настроен — скачайте ADB в Настройках")

    if not adb.prepare_for_work(ser, hold_awake=True):
        raise AdbError("Устройство не отвечает по ADB")

    progress_msgs: list[str] = []

    def progress(m: str) -> None:
        progress_msgs.append(m)

    scrcpy_exe, status = resolve_scrcpy(download=download, progress=progress)
    win_title = (title or f"APK Updater · {ser}").strip()

    env = os.environ.copy()
    env["ADB"] = str(Path(adb_path).resolve())
    adb_dir = str(Path(adb_path).resolve().parent)
    env["PATH"] = adb_dir + os.pathsep + env.get("PATH", "")

    args = [
        scrcpy_exe,
        "-s",
        ser,
        "--window-title",
        win_title,
        # Mild limits help Wi‑Fi ADB stay usable
        "--max-size",
        "1280",
        "--video-bit-rate",
        "4M",
    ]

    # Do NOT use CREATE_NO_WINDOW — scrcpy needs a visible GUI window.
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )

    try:
        subprocess.Popen(
            args,
            cwd=str(Path(scrcpy_exe).resolve().parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise AdbError(f"Не удалось запустить scrcpy: {exc}") from exc

    extra = f" ({progress_msgs[-1]})" if progress_msgs else ""
    return True, f"scrcpy запущен для {ser}. {status}{extra}"
