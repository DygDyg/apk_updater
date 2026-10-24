from __future__ import annotations

import re
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
from .xapk_util import (
    artifact_from_extracted,
    extract_xapk,
    is_xapk_archive,
    load_extracted_splits,
    url_looks_like_xapk,
)

HISTORY_URL = "https://tapi.pureapk.com/v3/get_app_his_version"
CMS_URL = "https://api.pureapk.com/m/v3/cms/app_version"

_APK_URL_RE = re.compile(r"https?://[^\s\"']+/APK[^\s\"']*", re.I)


class ApkPureProvider(ApkProvider):
    name = "apkpure"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Ual-Access-Businessid": "projecta",
            "Ual-Access-ProjectA": '{"device_info":{"os_ver":"33"}}',
            "x-sv": "33",
            "x-abis": "arm64-v8a,armeabi-v7a,armeabi",
            "x-gp": "1",
        }

    def get_latest(self, package_name: str) -> RemoteVersion | None:
        remote = self._from_history(package_name)
        if remote is not None:
            return remote
        return self._from_cms(package_name)

    def _from_history(self, package_name: str) -> RemoteVersion | None:
        try:
            resp = self._client.get(
                HISTORY_URL,
                params={"package_name": package_name, "hl": "en"},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None

        versions = data.get("version_list") or []
        if not versions:
            return None

        best = None
        best_code = -1
        for item in versions:
            try:
                code = int(item.get("version_code") or 0)
            except (TypeError, ValueError):
                continue
            if code > best_code:
                best_code = code
                best = item
        if best is None or best_code <= 0:
            return None

        asset = best.get("asset") or {}
        asset_type = str(asset.get("type") or "").upper()
        url = str(asset.get("url") or "")
        unsupported = ""
        if asset_type in ("", "APK", "XAPK"):
            unsupported = "" if url else "APKPure: нет ссылки на скачивание"
        else:
            unsupported = f"APKPure: формат {asset_type} не поддерживается"
            # Same versionCode may also list a plain APK asset.
            for item in versions:
                try:
                    code = int(item.get("version_code") or 0)
                except (TypeError, ValueError):
                    continue
                if code != best_code:
                    continue
                a = item.get("asset") or {}
                t = str(a.get("type") or "").upper()
                if t in ("APK", "XAPK") and a.get("url"):
                    url = str(a["url"])
                    asset_type = t
                    unsupported = ""
                    best = item
                    break

        return RemoteVersion(
            package_name=package_name,
            version_name=str(best.get("version_name") or ""),
            version_code=best_code,
            source=self.name,
            download_url=url,
            unsupported_reason=unsupported,
        )

    def _from_cms(self, package_name: str) -> RemoteVersion | None:
        try:
            resp = self._client.get(
                CMS_URL,
                params={"hl": "en-US", "package_name": package_name},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            text = resp.text
        except httpx.HTTPError:
            return None

        url_match = _APK_URL_RE.search(text)
        url = url_match.group(0) if url_match else ""
        version_code = 0
        version_name = ""
        m_code = re.search(r'"version_code"\s*:\s*"?(\d+)"?', text)
        if m_code:
            version_code = int(m_code.group(1))
        m_name = re.search(r'"version_name"\s*:\s*"([^"]+)"', text)
        if m_name:
            version_name = m_name.group(1)

        if version_code <= 0 and not url:
            return None
        if version_code <= 0:
            version_code = 1
        return RemoteVersion(
            package_name=package_name,
            version_name=version_name,
            version_code=version_code,
            source=self.name,
            download_url=url,
            unsupported_reason="" if url else "APKPure: no APK download URL",
        )

    def _history_payload(self, package_name: str) -> dict | None:
        try:
            resp = self._client.get(
                HISTORY_URL,
                params={"package_name": package_name, "hl": "en"},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def search(self, query: str, *, limit: int = 20) -> list[StoreSearchHit]:
        """APKPure public search API is blocked; exact package lookup via history/CMS."""
        _ = limit
        q = (query or "").strip()
        if not q or "." not in q:
            return []
        remote = self.get_latest(q)
        if remote is None:
            return []
        title = q
        icon_url = ""
        data = self._history_payload(q) or {}
        for key in ("title", "app_name", "name"):
            if data.get(key):
                title = str(data[key])
                break
        icon = data.get("icon") or data.get("icon_url") or ""
        if isinstance(icon, dict):
            icon_url = str(icon.get("url") or "")
        else:
            icon_url = str(icon or "")
        return [
            StoreSearchHit(
                package_name=q,
                title=title,
                source=self.name,
                version_name=remote.version_name,
                version_code=remote.version_code,
                icon_url=icon_url,
            )
        ]

    def get_details(self, package_name: str) -> StoreAppDetails | None:
        remote = self.get_latest(package_name)
        if remote is None:
            return None
        data = self._history_payload(package_name) or {}
        title = package_name
        for key in ("title", "app_name", "name"):
            if data.get(key):
                title = str(data[key])
                break
        icon = data.get("icon") or data.get("icon_url") or ""
        if isinstance(icon, dict):
            icon_url = str(icon.get("url") or "")
        else:
            icon_url = str(icon or "")
        developer = str(
            data.get("developer")
            or data.get("developer_name")
            or data.get("author")
            or ""
        )
        description = str(
            data.get("description")
            or data.get("short_description")
            or data.get("intro")
            or ""
        )
        return StoreAppDetails(
            package_name=package_name,
            title=title,
            source=self.name,
            description=description,
            short_description=description[:280] if description else "",
            developer=developer,
            icon_url=icon_url,
            version_name=remote.version_name,
            version_code=remote.version_code,
            download_url=remote.download_url,
            unsupported_reason=remote.unsupported_reason,
        )

    def list_versions(self, package_name: str, *, limit: int = 20) -> list[StoreVersion]:
        data = self._history_payload(package_name)
        if not data:
            return super().list_versions(package_name, limit=limit)
        versions = data.get("version_list") or []
        out: list[StoreVersion] = []
        for item in versions:
            try:
                code = int(item.get("version_code") or 0)
            except (TypeError, ValueError):
                continue
            if code <= 0:
                continue
            asset = item.get("asset") or {}
            asset_type = str(asset.get("type") or "").upper()
            url = str(asset.get("url") or "")
            unsupported = ""
            if asset_type in ("", "APK", "XAPK"):
                if not url:
                    unsupported = "APKPure: нет ссылки на скачивание"
            else:
                unsupported = f"APKPure: формат {asset_type} не поддерживается"
            out.append(
                StoreVersion(
                    version_name=str(item.get("version_name") or ""),
                    version_code=code,
                    source=self.name,
                    download_url=url if asset_type in ("", "APK", "XAPK") else "",
                    unsupported_reason=unsupported,
                )
            )
            if len(out) >= max(1, min(int(limit or 20), 50)):
                break
        if not out:
            return super().list_versions(package_name, limit=limit)
        # Prefer higher versionCode first
        out.sort(key=lambda v: v.version_code, reverse=True)
        return out[: max(1, min(int(limit or 20), 50))]

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
            raise RuntimeError(f"APKPure: package not found: {package_name}")
        if info.unsupported_reason and not info.download_url:
            raise RuntimeError(info.unsupported_reason)
        if not info.download_url:
            info = self.get_latest(package_name) or info
        if not info.download_url:
            raise RuntimeError(f"APKPure: no download URL for {package_name}")

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{info.version_code}.apk"
        splits_dir = dest_dir / f"{info.version_code}_splits"
        is_xapk = url_looks_like_xapk(info.download_url)

        # Reuse previous XAPK extract.
        cached_splits = load_extracted_splits(splits_dir, package_name)
        if len(cached_splits) > 1:
            return ApkArtifact(
                package_name=package_name,
                version_name=info.version_name,
                version_code=info.version_code,
                source=self.name,
                file_path=cached_splits[0],
                download_url=info.download_url,
                split_apks=cached_splits[1:],
            )

        # Misnamed XAPK left as N.apk by older builds.
        if dest.is_file() and dest.stat().st_size > 1024 and is_xapk_archive(dest):
            xapk_path = dest_dir / f"{info.version_code}.xapk"
            try:
                if xapk_path.exists() and xapk_path.resolve() != dest.resolve():
                    xapk_path.unlink()
                dest.replace(xapk_path)
            except OSError:
                xapk_path = dest
            extracted = extract_xapk(
                xapk_path, splits_dir, expected_package=package_name
            )
            artifact = artifact_from_extracted(
                extracted,
                package_name=package_name,
                version_name=info.version_name,
                version_code=info.version_code,
                source=self.name,
                download_url=info.download_url,
                single_apk_dest=dest if len(extracted.apks) == 1 else None,
            )
            try:
                if xapk_path.is_file() and artifact.split_apks:
                    xapk_path.unlink(missing_ok=True)
            except OSError:
                pass
            return artifact

        if dest.is_file() and dest.stat().st_size > 1024 and not is_xapk:
            return ApkArtifact(
                package_name=package_name,
                version_name=info.version_name,
                version_code=info.version_code,
                source=self.name,
                file_path=dest,
                download_url=info.download_url,
            )
        if dest.is_file() and is_xapk and not is_xapk_archive(dest):
            # Stale base-only APK while store serves XAPK.
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass

        if is_xapk:
            xapk_path = dest_dir / f"{info.version_code}.xapk"
            download_file(
                info.download_url,
                xapk_path,
                headers=self._headers(),
                progress=progress,
            )
            extracted = extract_xapk(
                xapk_path, splits_dir, expected_package=package_name
            )
            artifact = artifact_from_extracted(
                extracted,
                package_name=package_name,
                version_name=info.version_name,
                version_code=info.version_code,
                source=self.name,
                download_url=info.download_url,
                single_apk_dest=dest if len(extracted.apks) == 1 else None,
            )
            try:
                if xapk_path.is_file() and (
                    artifact.split_apks or artifact.file_path.resolve() == dest.resolve()
                ):
                    xapk_path.unlink(missing_ok=True)
            except OSError:
                pass
            return artifact

        download_file(
            info.download_url, dest, headers=self._headers(), progress=progress
        )
        # Downloaded «APK» that is actually XAPK (CDN without /XAPK/ in URL).
        if is_xapk_archive(dest):
            xapk_path = dest_dir / f"{info.version_code}.xapk"
            try:
                dest.replace(xapk_path)
            except OSError:
                xapk_path = dest
            extracted = extract_xapk(
                xapk_path, splits_dir, expected_package=package_name
            )
            artifact = artifact_from_extracted(
                extracted,
                package_name=package_name,
                version_name=info.version_name,
                version_code=info.version_code,
                source=self.name,
                download_url=info.download_url,
                single_apk_dest=dest if len(extracted.apks) == 1 else None,
            )
            try:
                if xapk_path.is_file() and artifact.split_apks:
                    xapk_path.unlink(missing_ok=True)
            except OSError:
                pass
            return artifact
        return ApkArtifact(
            package_name=package_name,
            version_name=info.version_name,
            version_code=info.version_code,
            source=self.name,
            file_path=dest,
            download_url=info.download_url,
        )
