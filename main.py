from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from app.api.app_factory import create_app
from app.paths import DATA_DIR, ROOT_DIR, ensure_data_dirs
from app.runtime_control import RUNTIME
from app.tray import hide_console_window, run_tray

# Listen on all interfaces so phones / PCs on LAN can open the UI.
HOST = "0.0.0.0"
PORT = 8765
PID_FILE = DATA_DIR / "apk_updater.pid"
ERROR_LOG = DATA_DIR / "startup_error.log"


def _primary_lan_ip() -> str:
    """Best-effort LAN IPv4 for tray / console hints (not 127.x)."""
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        import socket

        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return ""


def _browser_url(port: int) -> str:
    # Browsers cannot usefully open http://0.0.0.0/ — always use loopback locally.
    return f"http://127.0.0.1:{port}/"


def _silence_noisy_loggers() -> None:
    """pyaxmlparser spam (res0 / invalid string length) floods console and error log."""
    import logging

    for name in (
        "pyaxmlparser",
        "pyaxmlparser.arscutil",
        "pyaxmlparser.stringblock",
        "pyaxmlparser.axmlparser",
        "pyaxmlparser.core",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def _ensure_stdio_for_pythonw() -> None:
    """pythonw.exe has sys.stdout/stderr = None; uvicorn logging crashes on isatty()."""
    ensure_data_dirs()
    # Truncate previous spam so run_apk_updater.bat does not dump multi-MB logs
    try:
        if ERROR_LOG.is_file() and ERROR_LOG.stat().st_size > 256_000:
            ERROR_LOG.write_text("", encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(ERROR_LOG, "a", encoding="utf-8")  # noqa: SIM115
    _silence_noisy_loggers()


def _write_pid() -> None:
    ensure_data_dirs()
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")


def _clear_pid() -> None:
    try:
        if PID_FILE.is_file() and PID_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def _log_error(exc: BaseException) -> None:
    ensure_data_dirs()
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        ERROR_LOG.write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"{exc}\n\nDetails: {ERROR_LOG}",
            "APK Updater — startup error",
            0x10,
        )
    except Exception:  # noqa: BLE001
        print(text, file=sys.stderr)


def _server_already_up(port: int) -> bool:
    try:
        import httpx

        # Probe loopback even when uvicorn binds 0.0.0.0
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=1.5)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


_MUTEX_HANDLE = None


def _acquire_singleton(port: int) -> bool:
    """Windows named mutex — prevents two pythonw racing on the same port."""
    global _MUTEX_HANDLE
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        name = f"Local\\APKUpdater_port_{port}"
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:  # noqa: BLE001
        return True


def _open_browser_on_start_enabled(*, force_no_browser: bool) -> bool:
    """Setting open_browser_on_start (default off). --no-browser always wins."""
    if force_no_browser:
        return False
    try:
        from app.db.database import Database

        db = Database()
        try:
            raw = db.get_setting("open_browser_on_start", "0") or "0"
        finally:
            db.close()
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="APK Updater (local web UI)")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser on start")
    parser.add_argument("--no-tray", action="store_true", help="Run in console without tray icon")
    parser.add_argument("--console", action="store_true", help="Keep console window visible")
    args = parser.parse_args()

    ensure_data_dirs()
    _ensure_stdio_for_pythonw()
    _silence_noisy_loggers()
    url = _browser_url(args.port)
    lan_ip = _primary_lan_ip()
    open_browser = _open_browser_on_start_enabled(force_no_browser=bool(args.no_browser))

    # Second launch: just focus UI, do not spawn another server/tray
    if _server_already_up(args.port):
        if open_browser:
            webbrowser.open(url)
        return 0
    if not _acquire_singleton(args.port):
        # During HTTP restart the dying process may still hold the mutex briefly.
        became_primary = False
        for _ in range(40):
            time.sleep(0.25)
            if _server_already_up(args.port):
                if open_browser:
                    webbrowser.open(url)
                return 0
            if _acquire_singleton(args.port):
                became_primary = True
                break
        if not became_primary:
            if open_browser:
                webbrowser.open(url)
            return 0

    _write_pid()
    atexit.register(_clear_pid)

    app = create_app()

    if not args.console and not args.no_tray:
        hide_console_window()

    # log_config=None avoids ColorFormatter touching stdout under pythonw
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning" if not args.console else "info",
        log_config=None,
    )
    server = uvicorn.Server(config)
    RUNTIME.server = server
    RUNTIME.host = args.host
    RUNTIME.port = args.port
    RUNTIME.no_tray = bool(args.no_tray)
    RUNTIME.console = bool(args.console)
    RUNTIME.root_dir = ROOT_DIR

    def open_ui() -> None:
        webbrowser.open(url)

    if args.no_tray:
        if open_browser:
            threading.Thread(target=lambda: (time.sleep(0.8), open_ui()), daemon=True).start()
        try:
            server.run()
        finally:
            _clear_pid()
        return 0

    boot_error: list[BaseException] = []

    def run_server() -> None:
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001
            boot_error.append(exc)

    server_thread = threading.Thread(target=run_server, name="uvicorn", daemon=True)
    server_thread.start()

    for _ in range(80):
        if server.started:
            break
        if boot_error:
            raise boot_error[0]
        if not server_thread.is_alive():
            raise RuntimeError(
                f"Server exited before start (is port {args.port} busy?). "
                f"See {ERROR_LOG} or run: stop.bat"
            )
        time.sleep(0.1)
    else:
        if not server.started:
            raise RuntimeError(f"Server did not start on {args.host}:{args.port}")

    if open_browser:
        open_ui()

    def on_quit() -> None:
        server.should_exit = True

    def on_icon(icon: object) -> None:
        RUNTIME.tray_icon = icon

    try:
        tray_host = lan_ip or args.host
        run_tray(
            title=f"APK Updater · {tray_host}:{args.port}",
            on_open=open_ui,
            on_quit=on_quit,
            on_icon=on_icon,
        )
    except BaseException as exc:
        server.should_exit = True
        raise RuntimeError(f"Tray icon failed: {exc}") from exc

    server.should_exit = True
    server_thread.join(timeout=3.0)
    _clear_pid()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _log_error(exc)
        raise SystemExit(1) from exc
