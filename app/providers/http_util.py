from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=15.0)

# done_bytes, total_bytes (None if Content-Length unknown)
DownloadProgressCb = Callable[[int, int | None], None]


def format_download_progress(done: int, total: int | None) -> tuple[str, int | None]:
    """Return (human detail, percent 0–100 or None)."""

    def _mb(n: int) -> str:
        return f"{n / (1024 * 1024):.1f} МБ"

    if total and total > 0:
        pct = min(100, int(done * 100 / total))
        return f"{_mb(done)} / {_mb(total)} ({pct}%)", pct
    if done > 0:
        return f"{_mb(done)}…", None
    return "Скачивание…", None


def download_file(
    url: str,
    dest: Path,
    *,
    timeout: httpx.Timeout | None = None,
    headers: dict | None = None,
    progress: DownloadProgressCb | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        timeout=timeout or DEFAULT_TIMEOUT,
        headers=headers or {},
    ) as resp:
        resp.raise_for_status()
        total: int | None = None
        cl = resp.headers.get("content-length")
        if cl and str(cl).isdigit():
            total = int(cl)
        done = 0
        last_emit = -1
        with tmp.open("wb") as out:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                out.write(chunk)
                done += len(chunk)
                if progress is None:
                    continue
                # Throttle: every 2% or every 512 KiB when size unknown
                if total and total > 0:
                    pct = int(done * 100 / total)
                    if pct < 100 and pct // 2 == last_emit // 2 and done < total:
                        continue
                    last_emit = pct
                else:
                    bucket = done // (512 * 1024)
                    if bucket == last_emit and done > 0:
                        continue
                    last_emit = bucket
                try:
                    progress(done, total)
                except Exception:  # noqa: BLE001
                    pass
        if progress is not None:
            try:
                progress(done, total if total and total > 0 else done or total)
            except Exception:  # noqa: BLE001
                pass
    tmp.replace(dest)
    return dest
