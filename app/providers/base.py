from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemoteVersion:
    package_name: str
    version_name: str
    version_code: int
    source: str
    download_url: str = ""
    app_id: str = ""
    unsupported_reason: str = ""


@dataclass(frozen=True)
class ApkArtifact:
    package_name: str
    version_name: str
    version_code: int
    source: str
    file_path: Path
    download_url: str = ""


@dataclass(frozen=True)
class StoreSearchHit:
    package_name: str
    title: str
    source: str
    version_name: str = ""
    version_code: int = 0
    icon_url: str = ""
    developer: str = ""
    short_description: str = ""
    app_id: str = ""


@dataclass(frozen=True)
class StoreAppDetails:
    package_name: str
    title: str
    source: str
    description: str = ""
    short_description: str = ""
    developer: str = ""
    icon_url: str = ""
    version_name: str = ""
    version_code: int = 0
    app_id: str = ""
    download_url: str = ""
    unsupported_reason: str = ""


@dataclass(frozen=True)
class StoreVersion:
    version_name: str
    version_code: int
    source: str
    download_url: str = ""
    app_id: str = ""
    unsupported_reason: str = ""


class ApkProvider(ABC):
    """Source of APK metadata and downloads."""

    name: str = "base"

    @abstractmethod
    def get_latest(self, package_name: str) -> RemoteVersion | None:
        """Return latest known version in this store, or None if missing."""

    def find(self, package_name: str) -> ApkArtifact | None:
        """Compatibility shim — prefer get_latest."""
        _ = package_name
        return None

    def search(self, query: str, *, limit: int = 20) -> list[StoreSearchHit]:
        """Live search in this store (default: empty)."""
        _ = query, limit
        return []

    def get_details(self, package_name: str) -> StoreAppDetails | None:
        """App card details for package, or None if missing."""
        remote = self.get_latest(package_name)
        if remote is None:
            return None
        return StoreAppDetails(
            package_name=package_name,
            title=package_name,
            source=self.name,
            version_name=remote.version_name,
            version_code=remote.version_code,
            app_id=remote.app_id,
            download_url=remote.download_url,
            unsupported_reason=remote.unsupported_reason,
        )

    def list_versions(self, package_name: str, *, limit: int = 20) -> list[StoreVersion]:
        """Known versions (latest-only stores return a single item)."""
        remote = self.get_latest(package_name)
        if remote is None:
            return []
        return [
            StoreVersion(
                version_name=remote.version_name,
                version_code=remote.version_code,
                source=self.name,
                download_url=remote.download_url,
                app_id=remote.app_id,
                unsupported_reason=remote.unsupported_reason,
            )
        ]

    def resolve_version(
        self, package_name: str, *, version_code: int | None = None
    ) -> RemoteVersion | None:
        """Resolve RemoteVersion for install (specific code or latest)."""
        if version_code is None or version_code <= 0:
            return self.get_latest(package_name)
        for ver in self.list_versions(package_name, limit=50):
            if ver.version_code == version_code:
                return RemoteVersion(
                    package_name=package_name,
                    version_name=ver.version_name,
                    version_code=ver.version_code,
                    source=self.name,
                    download_url=ver.download_url,
                    app_id=ver.app_id,
                    unsupported_reason=ver.unsupported_reason,
                )
        remote = self.get_latest(package_name)
        if remote is not None and remote.version_code == version_code:
            return remote
        return None

    @abstractmethod
    def download(self, package_name: str, dest_dir: Path, *, remote: RemoteVersion | None = None) -> ApkArtifact:
        """Download APK into dest_dir and return artifact info."""
