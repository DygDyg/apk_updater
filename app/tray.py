from __future__ import annotations

import threading
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from app.notify import set_notifier

try:
    import pystray
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install pystray: pip install pystray") from exc


def make_tray_icon(size: int = 64) -> Image.Image:
    """
    High-contrast tray icon: solid teal rounded square with white "AU".
    Opaque pixels work more reliably in the Windows notification area.
    """
    img = Image.new("RGBA", (size, size), (12, 17, 23, 255))
    draw = ImageDraw.Draw(img)
    margin = 2
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=14,
        fill=(45, 212, 191, 255),
    )
    try:
        font = ImageFont.truetype("arialbd.ttf", 28)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
    text = "AU"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1] - 1),
        text,
        fill=(4, 47, 46, 255),
        font=font,
    )
    return img


def run_tray(
    *,
    title: str,
    on_open: Callable[[], None],
    on_quit: Callable[[], None],
    on_icon: Callable[[pystray.Icon], None] | None = None,
) -> None:
    """Block on the tray event loop (call from main thread on Windows)."""

    icon = pystray.Icon("apk_updater", make_tray_icon(), title)

    def quit_item(icon: pystray.Icon, _item: object) -> None:
        try:
            on_quit()
        finally:
            icon.visible = False
            icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Открыть APK Updater", lambda _i, _item: on_open(), default=True),
        pystray.MenuItem("Выход", quit_item),
    )

    def setup(icon: pystray.Icon) -> None:
        icon.visible = True
        if on_icon is not None:
            try:
                on_icon(icon)
            except Exception:  # noqa: BLE001
                pass

        def _notify(title: str, message: str) -> None:
            icon.notify(message, title)

        set_notifier(_notify)

    try:
        icon.run(setup=setup)
    finally:
        set_notifier(None)


def hide_console_window() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass
