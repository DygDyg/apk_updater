from __future__ import annotations

import os
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

OVERALL_INFO_URL = "https://backapi.rustore.ru/applicationData/overallInfo/{package}"
APPS_SEARCH_URL = "https://backapi.rustore.ru/applicationData/apps"
DOWNLOAD_LINK_URL = "https://backapi.rustore.ru/applicationData/download-link"
DOWNLOAD_LINK_V2_URL = "https://backapi.rustore.ru/applicationData/v2/download-link"
CATALOG_URL = "https://www.rustore.ru/catalog/app/{package}"

# Since July 2026 backapi rejects requests without ruStoreVerCode (empty HTTP 400 / kittenx).
# Range is enforced (~247 … ~1.1M); override via RUSTORE_VER_CODE if bounds change.
_VER_CODE_PRIMARY = os.environ.get("RUSTORE_VER_CODE", "1000000")
_VER_CODE_FALLBACKS = (_VER_CODE_PRIMARY, "12000", "247")
_VER_CODE_REJECT = {417, 419}


def _version_name_to_code(version_name: str) -> int:
    """Best-effort numeric code from dotted version for comparisons when API is blocked."""
    parts = re.findall(r"\d+", version_name or "")
    nums = [int(p) for p in parts[:4]]
    while len(nums) < 4:
        nums.append(0)
    return nums[0] * 1_000_000 + nums[1] * 10_000 + nums[2] * 100 + min(nums[3], 99)


class RuStoreProvider(ApkProvider):
    name = "rustore"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def _api_headers(self, *, ver_code: str, json_body: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": "okhttp/4.12.0",
            "Accept": "application/json",
            "ruStoreVerCode": ver_code,
            "Origin": "https://www.rustore.ru",
            "Referer": "https://www.rustore.ru/",
        }
        if json_body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        return headers

    def _request_with_ver_code(self, send) -> httpx.Response | None:
        """Retry across ruStoreVerCode candidates when edge rejects the value."""
        seen: set[str] = set()
        last: httpx.Response | None = None
        for ver in _VER_CODE_FALLBACKS:
            if not ver or ver in seen:
                continue
            seen.add(ver)
            try:
                resp = send(ver)
            except httpx.HTTPError:
                continue
            last = resp
            if resp.status_code not in _VER_CODE_REJECT:
                return resp
        return last

    def get_latest(self, package_name: str) -> RemoteVersion | None:
        remote = self._from_api(package_name)
        if remote is not None:
            return remote
        remote = self._from_apps_search(package_name)
        if remote is not None:
            return remote
        return self._from_catalog_page(package_name)

    def _from_api(self, package_name: str) -> RemoteVersion | None:
        url = OVERALL_INFO_URL.format(package=package_name)

        def send(ver: str) -> httpx.Response:
            return self._client.get(url, headers=self._api_headers(ver_code=ver))

        resp = self._request_with_ver_code(send)
        if resp is None or resp.status_code != 200 or not resp.text.strip():
            return None
        try:
            data = resp.json()
        except ValueError:
            return None

        body = data.get("body") or {}
        if not body:
            return None
        try:
            version_code = int(body.get("versionCode") or body.get("activeVerCode") or 0)
        except (TypeError, ValueError):
            version_code = 0
        if version_code <= 0:
            return None
        return RemoteVersion(
            package_name=package_name,
            version_name=str(body.get("versionName") or ""),
            version_code=version_code,
            source=self.name,
            app_id=str(body.get("appId") or ""),
        )

    def _from_apps_search(self, package_name: str) -> RemoteVersion | None:
        """Fallback: search apps by package name (overallInfo sometimes blocked)."""

        def send(ver: str) -> httpx.Response:
            return self._client.get(
                APPS_SEARCH_URL,
                params={"query": package_name, "pageNumber": 0, "pageSize": 20},
                headers=self._api_headers(ver_code=ver),
            )

        resp = self._request_with_ver_code(send)
        if resp is None or resp.status_code != 200 or not resp.text.strip():
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        content = ((data.get("body") or {}).get("content")) or []
        hit = None
        for item in content:
            if str(item.get("packageName") or "") == package_name:
                hit = item
                break
        if hit is None and content:
            # Exact package query usually returns the app first
            first = content[0]
            if str(first.get("packageName") or "") == package_name:
                hit = first
        if hit is None:
            return None
        try:
            version_code = int(hit.get("versionCode") or hit.get("vercode") or 0)
        except (TypeError, ValueError):
            version_code = 0
        app_id = str(hit.get("appId") or "")
        if version_code <= 0 and not app_id:
            return None
        if version_code <= 0:
            # Search hit may omit versionCode; still useful for download via appId
            version_code = 1
        return RemoteVersion(
            package_name=package_name,
            version_name=str(hit.get("versionName") or hit.get("vername") or ""),
            version_code=version_code,
            source=self.name,
            app_id=app_id,
        )

    def _from_catalog_page(self, package_name: str) -> RemoteVersion | None:
        """Fallback when backapi is blocked — scrape public catalog HTML."""
        url = CATALOG_URL.format(package=package_name)
        try:
            resp = self._client.get(
                url,
                headers={
                    **self._api_headers(ver_code=_VER_CODE_PRIMARY),
                    "Accept": "text/html,application/json,*/*",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                return None
            text = resp.text
        except httpx.HTTPError:
            return None

        if package_name not in text or "challenge.html" in str(resp.url):
            return None

        idx = text.find(package_name)
        window = text[max(0, idx - 2500) : idx + 5000] if idx >= 0 else text

        version_name = ""
        m_soft = re.search(r'itemProp="softwareVersion">([^<]+)', text)
        if m_soft:
            version_name = m_soft.group(1).strip()
        if not version_name:
            m2 = re.search(r'"softwareVersion":"([^"]+)"', window)
            if m2:
                version_name = m2.group(1).strip()

        app_id = ""
        apk_ids = re.findall(r"/apk/(\d+)/", window)
        if apk_ids:
            app_id = max(set(apk_ids), key=apk_ids.count)

        version_code = 0
        m_code = re.search(r'"versionCode":(\d+)', window)
        if m_code:
            version_code = int(m_code.group(1))
        if version_code <= 0 and version_name:
            version_code = _version_name_to_code(version_name)

        if version_code <= 0 and not version_name:
            return None

        return RemoteVersion(
            package_name=package_name,
            version_name=version_name,
            version_code=version_code,
            source=self.name,
            app_id=app_id,
            unsupported_reason="" if app_id else "RuStore: appId not found on catalog page",
        )

    def _resolve_download_url(self, app_id: int) -> tuple[str, int, str]:
        """Return (apk_url, version_code, version_name). Prefer v2 (real .apk)."""

        def post_v2(ver: str) -> httpx.Response:
            return self._client.post(
                DOWNLOAD_LINK_V2_URL,
                json={
                    "appId": app_id,
                    "firstInstall": True,
                    "withoutSplits": True,
                    "screenDensity": 480,
                    "sdkVersion": 33,
                    "supportedAbis": ["arm64-v8a", "armeabi-v7a", "armeabi"],
                },
                headers=self._api_headers(ver_code=ver, json_body=True),
            )

        resp = self._request_with_ver_code(post_v2)
        if resp is not None and resp.status_code == 200 and resp.text.strip():
            try:
                body = (resp.json() or {}).get("body") or {}
            except ValueError:
                body = {}
            urls = body.get("downloadUrls") or []
            apk_url = ""
            if urls and isinstance(urls, list):
                apk_url = str((urls[0] or {}).get("url") or "")
            if apk_url:
                try:
                    code = int(body.get("versionCode") or 0)
                except (TypeError, ValueError):
                    code = 0
                return apk_url, code, str(body.get("versionName") or "")

        def post_v1(ver: str) -> httpx.Response:
            return self._client.post(
                DOWNLOAD_LINK_URL,
                json={"appId": app_id, "firstInstall": True},
                headers=self._api_headers(ver_code=ver, json_body=True),
            )

        resp = self._request_with_ver_code(post_v1)
        if resp is None or resp.status_code != 200 or not resp.text.strip():
            raise RuntimeError(
                f"RuStore download-link HTTP {getattr(resp, 'status_code', '?')}"
            )
        try:
            body = (resp.json() or {}).get("body") or {}
        except ValueError as exc:
            raise RuntimeError(f"RuStore download-link bad JSON: {exc}") from exc
        apk_url = str(body.get("apkUrl") or "")
        if not apk_url:
            raise RuntimeError("RuStore: empty apkUrl")
        try:
            code = int(body.get("versionCode") or 0)
        except (TypeError, ValueError):
            code = 0
        return apk_url, code, str(body.get("versionName") or "")

    def search(self, query: str, *, limit: int = 20) -> list[StoreSearchHit]:
        q = (query or "").strip()
        if not q:
            return []
        page_size = max(1, min(int(limit or 20), 50))

        def send(ver: str) -> httpx.Response:
            return self._client.get(
                APPS_SEARCH_URL,
                params={"query": q, "pageNumber": 0, "pageSize": page_size},
                headers=self._api_headers(ver_code=ver),
            )

        resp = self._request_with_ver_code(send)
        if resp is None or resp.status_code != 200 or not resp.text.strip():
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        content = ((data.get("body") or {}).get("content")) or []
        hits: list[StoreSearchHit] = []
        for item in content:
            package = str(item.get("packageName") or "").strip()
            if not package:
                continue
            try:
                version_code = int(item.get("versionCode") or item.get("vercode") or 0)
            except (TypeError, ValueError):
                version_code = 0
            hits.append(
                StoreSearchHit(
                    package_name=package,
                    title=str(item.get("appName") or package),
                    source=self.name,
                    version_name=str(item.get("versionName") or item.get("vername") or ""),
                    version_code=version_code,
                    icon_url=str(item.get("iconUrl") or ""),
                    developer=str(item.get("companyName") or item.get("developerName") or ""),
                    short_description=str(item.get("shortDescription") or ""),
                    app_id=str(item.get("appId") or ""),
                )
            )
            if len(hits) >= page_size:
                break
        return hits

    def get_details(self, package_name: str) -> StoreAppDetails | None:
        url = OVERALL_INFO_URL.format(package=package_name)

        def send(ver: str) -> httpx.Response:
            return self._client.get(url, headers=self._api_headers(ver_code=ver))

        resp = self._request_with_ver_code(send)
        body: dict = {}
        if resp is not None and resp.status_code == 200 and resp.text.strip():
            try:
                body = (resp.json() or {}).get("body") or {}
            except ValueError:
                body = {}

        if body:
            try:
                version_code = int(body.get("versionCode") or body.get("activeVerCode") or 0)
            except (TypeError, ValueError):
                version_code = 0
            return StoreAppDetails(
                package_name=package_name,
                title=str(body.get("appName") or package_name),
                source=self.name,
                description=str(body.get("fullDescription") or body.get("description") or ""),
                short_description=str(body.get("shortDescription") or ""),
                developer=str(body.get("companyName") or ""),
                icon_url=str(body.get("iconUrl") or ""),
                version_name=str(body.get("versionName") or ""),
                version_code=version_code,
                app_id=str(body.get("appId") or ""),
            )

        # Fallback: search hit / latest remote
        remote = self._from_apps_search(package_name) or self.get_latest(package_name)
        if remote is None:
            return None
        hits = self.search(package_name, limit=10)
        hit = next((h for h in hits if h.package_name == package_name), None)
        return StoreAppDetails(
            package_name=package_name,
            title=(hit.title if hit else package_name),
            source=self.name,
            description="",
            short_description=(hit.short_description if hit else ""),
            developer=(hit.developer if hit else ""),
            icon_url=(hit.icon_url if hit else ""),
            version_name=remote.version_name,
            version_code=remote.version_code,
            app_id=remote.app_id or (hit.app_id if hit else ""),
        )

    def list_versions(self, package_name: str, *, limit: int = 20) -> list[StoreVersion]:
        _ = limit
        details = self.get_details(package_name)
        if details is None or details.version_code <= 0:
            remote = self.get_latest(package_name)
            if remote is None:
                return []
            return [
                StoreVersion(
                    version_name=remote.version_name,
                    version_code=remote.version_code,
                    source=self.name,
                    app_id=remote.app_id,
                )
            ]
        return [
            StoreVersion(
                version_name=details.version_name,
                version_code=details.version_code,
                source=self.name,
                app_id=details.app_id,
            )
        ]

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
            raise RuntimeError(f"RuStore: package not found: {package_name}")
        if not info.app_id:
            info = self.get_latest(package_name) or info
        if not info.app_id:
            raise RuntimeError(f"RuStore: no appId for {package_name}")

        try:
            apk_url, dl_code, dl_name = self._resolve_download_url(int(info.app_id))
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"RuStore download-link failed: {exc}") from exc

        version_code = dl_code or info.version_code
        version_name = dl_name or info.version_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        # v1 may return .zip; keep extension from URL when not .apk
        suffix = ".apk"
        lower = apk_url.lower().split("?", 1)[0]
        if lower.endswith(".zip"):
            suffix = ".zip"
        dest = dest_dir / f"{version_code}{suffix}"
        download_file(
            apk_url,
            dest,
            headers=self._api_headers(ver_code=_VER_CODE_PRIMARY),
            progress=progress,
        )
        return ApkArtifact(
            package_name=package_name,
            version_name=version_name,
            version_code=version_code,
            source=self.name,
            file_path=dest,
            download_url=apk_url,
        )
