from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeControl:
    """Process-wide hooks so HTTP can restart the running app."""

    server: Any = None
    tray_icon: Any = None
    host: str = "0.0.0.0"
    port: int = 8765
    no_tray: bool = False
    console: bool = False
    root_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _restarting: bool = False
    _delay_sec: int = 4

    def request_restart(self, *, delay_sec: int = 4) -> bool:
        """Spawn a delayed replacement process, then shut this one down.

        ``delay_sec`` is how long the helper waits before starting the new
        process — use a larger value when deploy will copy files after stop.

        Returns False if a restart is already in progress.
        """
        delay = max(2, min(120, int(delay_sec)))
        with self._lock:
            if self._restarting:
                return False
            self._restarting = True
            self._delay_sec = delay
        threading.Thread(target=self._restart_worker, name="restart", daemon=True).start()
        return True

    def request_shutdown(self, *, grace_sec: float = 1.0) -> bool:
        """Exit without spawning a replacement (e.g. self-update helper starts EXE)."""
        with self._lock:
            if self._restarting:
                return False
            self._restarting = True
        threading.Thread(
            target=self._shutdown_worker,
            kwargs={"grace_sec": grace_sec},
            name="shutdown",
            daemon=True,
        ).start()
        return True

    def _restart_worker(self) -> None:
        try:
            self._spawn_delayed(self._delay_sec)
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            self._log(f"spawn failed: {exc}")
            with self._lock:
                self._restarting = False
            return
        self._shutdown()

    def _shutdown_worker(self, *, grace_sec: float = 1.0) -> None:
        time.sleep(max(0.2, float(grace_sec)))
        self._shutdown()

    def _log(self, message: str) -> None:
        try:
            log = self.root_dir / "data" / "restart.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass

    def _pick_python(self) -> Path:
        py = Path(sys.executable).resolve()
        # Tray mode should use pythonw (no console), matching run_apk_updater.bat
        if not self.console and not self.no_tray:
            cand = py.with_name("pythonw.exe")
            if cand.is_file():
                return cand
        return py

    def _spawn_delayed(self, delay_sec: int) -> None:
        """Write a one-shot .bat and launch it detached (survives our exit)."""
        root = self.root_dir.resolve()
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        flags = f"--host {self.host} --port {self.port} --no-browser"
        if self.no_tray:
            flags += " --no-tray"
        if self.console:
            flags += " --console"

        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            exe = Path(sys.executable).resolve()
            start_line = f'start "APK Updater" /D "{root}" "{exe}" {flags}'
            will = f"{exe} {flags}"
        else:
            py = self._pick_python()
            main_py = (root / "main.py").resolve()
            start_line = f'start "APK Updater" /D "{root}" "{py}" "{main_py}" {flags}'
            will = f"{py} {main_py} {flags}"

        helper = data_dir / "_restart_once.bat"
        # ASCII-only + CRLF — cmd.exe is picky
        lines = [
            "@echo off",
            "setlocal EnableExtensions",
            f'cd /d "{root}"',
            f"timeout /t {delay_sec} /nobreak >nul",
            start_line,
            'del "%~f0" >nul 2>&1',
            "",
        ]
        helper.write_bytes(("\r\n".join(lines)).encode("ascii", errors="ignore"))
        self._log(f"helper written: {helper} delay={delay_sec}s frozen={frozen}")
        self._log(f"will start: {will}")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        subprocess.Popen(
            ["cmd.exe", "/c", str(helper)],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        self._log("helper process spawned")

    def _shutdown(self) -> None:
        self._log("shutdown begin")
        server = self.server
        if server is not None:
            try:
                server.should_exit = True
            except Exception:  # noqa: BLE001
                pass
        icon = self.tray_icon
        if icon is not None:
            try:
                icon.visible = False
            except Exception:  # noqa: BLE001
                pass
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.6)
        self._log("os._exit(0)")
        os._exit(0)


RUNTIME = RuntimeControl()
