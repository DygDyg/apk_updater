from __future__ import annotations

from pathlib import Path

from app.paths import CACHE_DIR, ensure_data_dirs


class ApkCache:
    """Filesystem layout for shared APK cache (multi-device installs)."""

    def __init__(self, root: Path | None = None) -> None:
        ensure_data_dirs()
        self.root = root or CACHE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def package_dir(self, package_name: str) -> Path:
        path = self.root / package_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def apk_path(self, package_name: str, version_code: int | str) -> Path:
        return self.package_dir(package_name) / f"{version_code}.apk"

    def list_cached(self, package_name: str) -> list[Path]:
        directory = self.root / package_name
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.apk"))
