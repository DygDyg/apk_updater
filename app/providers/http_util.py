from __future__ import annotations

from pathlib import Path

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=15.0)


def download_file(url: str, dest: Path, *, timeout: httpx.Timeout | None = None, headers: dict | None = None) -> Path:
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
        with tmp.open("wb") as out:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                out.write(chunk)
    tmp.replace(dest)
    return dest
