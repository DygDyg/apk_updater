from __future__ import annotations

from pathlib import Path

import httpx

from .base import (
    ApkArtifact,
    ApkProvider,
    RemoteVersion,
    StoreAppDetails,
    StoreSearchHit,
    StoreVersion,
)
from .http_util import DEFAULT_TIMEOUT, download_file

META_URL = "https://ws2.aptoide.com/api/7/app/getMeta/package_name={package}"
META_VERCODE_URL = (
    "https://ws2.aptoide.com/api/7/app/getMeta/package_name={package}/vercode={code}"
)
SEARCH_URL = "https://ws75.aptoide.com/api/7/apps/search"
VERSIONS_URL = "https://ws2.aptoide.com/api/7/listAppVersions/package_name={package}/limit={limit}"


class AptoideProvider(ApkProvider):
    name = "aptoide"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json",
        }

    def _meta(self, package_name: str, *, version_code: int | None = None) -> dict | None:
        if version_code and version_code > 0:
            url = META_VERCODE_URL.format(package=package_name, code=version_code)
        else:
            url = META_URL.format(package=package_name)
        try:
            resp = self._client.get(url, headers=self._headers())
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        info = data.get("info") or {}
        if str(info.get("status") or "").upper() != "OK":
            return None
        body = data.get("data") or {}
        return body if isinstance(body, dict) and body else None

    def get_latest(self, package_name: str) -> RemoteVersion | None:
        body = self._meta(package_name)
        if not body:
            return None
        file_info = body.get("file") or {}
        try:
            version_code = int(file_info.get("vercode") or 0)
        except (TypeError, ValueError):
            version_code = 0
        download_url = str(file_info.get("path") or file_info.get("path_alt") or "")
        if version_code <= 0:
            return None
        return RemoteVersion(
            package_name=package_name,
            version_name=str(file_info.get("vername") or ""),
            version_code=version_code,
            source=self.name,
            download_url=download_url,
            app_id=str(body.get("id") or ""),
            unsupported_reason="" if download_url else "Aptoide: no APK download URL",
        )

    def search(self, query: str, *, limit: int = 20) -> list[StoreSearchHit]:
        q = (query or "").strip()
        if not q:
            return []
        page_size = max(1, min(int(limit or 20), 50))
        try:
            resp = self._client.get(
                SEARCH_URL,
                params={"query": q, "limit": page_size},
                headers=self._headers(),
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        if str((data.get("info") or {}).get("status") or "").upper() != "OK":
            return []
        items = ((data.get("datalist") or {}).get("list")) or []
        hits: list[StoreSearchHit] = []
        for item in items:
            package = str(item.get("package") or "").strip()
            if not package:
                continue
            file_info = item.get("file") or {}
            try:
                version_code = int(file_info.get("vercode") or 0)
            except (TypeError, ValueError):
                version_code = 0
            developer = item.get("developer") or {}
            hits.append(
                StoreSearchHit(
                    package_name=package,
                    title=str(item.get("name") or package),
                    source=self.name,
                    version_name=str(file_info.get("vername") or ""),
                    version_code=version_code,
                    icon_url=str(item.get("icon") or ""),
                    developer=str(developer.get("name") or "")
                    if isinstance(developer, dict)
                    else str(developer or ""),
                    app_id=str(item.get("id") or ""),
                )
            )
            if len(hits) >= page_size:
                break
        return hits

    def get_details(self, package_name: str) -> StoreAppDetails | None:
        body = self._meta(package_name)
        if not body:
            return None
        file_info = body.get("file") or {}
        media = body.get("media") or {}
        developer = body.get("developer") or {}
        try:
            version_code = int(file_info.get("vercode") or 0)
        except (TypeError, ValueError):
            version_code = 0
        download_url = str(file_info.get("path") or file_info.get("path_alt") or "")
        description = str(media.get("description") or body.get("description") or "")
        return StoreAppDetails(
            package_name=package_name,
            title=str(body.get("name") or package_name),
            source=self.name,
            description=description,
            short_description=description[:280] if description else "",
            developer=str(developer.get("name") or "")
            if isinstance(developer, dict)
            else str(developer or ""),
            icon_url=str(body.get("icon") or ""),
            version_name=str(file_info.get("vername") or ""),
            version_code=version_code,
            app_id=str(body.get("id") or ""),
            download_url=download_url,
            unsupported_reason="" if download_url else "Aptoide: no APK download URL",
        )

    def list_versions(self, package_name: str, *, limit: int = 20) -> list[StoreVersion]:
        page_size = max(1, min(int(limit or 20), 50))
        url = VERSIONS_URL.format(package=package_name, limit=page_size)
        try:
            resp = self._client.get(url, headers=self._headers())
            if resp.status_code != 200:
                return super().list_versions(package_name, limit=limit)
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return super().list_versions(package_name, limit=limit)
        if str((data.get("info") or {}).get("status") or "").upper() != "OK":
            return super().list_versions(package_name, limit=limit)
        items = data.get("list") or []
        out: list[StoreVersion] = []
        for item in items:
            file_info = item.get("file") or {}
            try:
                code = int(file_info.get("vercode") or 0)
            except (TypeError, ValueError):
                continue
            if code <= 0:
                continue
            # Path often missing in list — resolve via getMeta when downloading.
            out.append(
                StoreVersion(
                    version_name=str(file_info.get("vername") or ""),
                    version_code=code,
                    source=self.name,
                    download_url=str(file_info.get("path") or file_info.get("path_alt") or ""),
                    app_id=str(item.get("id") or ""),
                )
            )
            if len(out) >= page_size:
                break
        return out or super().list_versions(package_name, limit=limit)

    def resolve_version(
        self, package_name: str, *, version_code: int | None = None
    ) -> RemoteVersion | None:
        if version_code is None or version_code <= 0:
            return self.get_latest(package_name)
        body = self._meta(package_name, version_code=version_code)
        if not body:
            return super().resolve_version(package_name, version_code=version_code)
        file_info = body.get("file") or {}
        try:
            code = int(file_info.get("vercode") or 0)
        except (TypeError, ValueError):
            code = 0
        download_url = str(file_info.get("path") or file_info.get("path_alt") or "")
        if code <= 0:
            return None
        return RemoteVersion(
            package_name=package_name,
            version_name=str(file_info.get("vername") or ""),
            version_code=code,
            source=self.name,
            download_url=download_url,
            app_id=str(body.get("id") or ""),
            unsupported_reason="" if download_url else "Aptoide: no APK download URL",
        )

    def download(
        self,
        package_name: str,
        dest_dir: Path,
        *,
        remote: RemoteVersion | None = None,
        progress=None,
    ) -> ApkArtifact:
        info = remote or self.get_latest(package_name)
        if info is None:
            raise RuntimeError(f"Aptoide: package not found: {package_name}")
        if info.unsupported_reason and not info.download_url:
            raise RuntimeError(info.unsupported_reason)
        if not info.download_url:
            info = self.get_latest(package_name) or info
        if not info.download_url:
            raise RuntimeError(f"Aptoide: no download URL for {package_name}")

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{info.version_code}.apk"
        download_file(
            info.download_url, dest, headers=self._headers(), progress=progress
        )
        return ApkArtifact(
            package_name=package_name,
            version_name=info.version_name,
            version_code=info.version_code,
            source=self.name,
            file_path=dest,
            download_url=info.download_url,
        )
