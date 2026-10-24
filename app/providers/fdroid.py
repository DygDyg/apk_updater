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

PACKAGES_URL = "https://f-droid.org/api/v1/packages/{package}"
APK_URL = "https://f-droid.org/repo/{package}_{code}.apk"
SEARCH_URL = "https://search.f-droid.org/"
PACKAGE_PAGE_URL = "https://f-droid.org/en/packages/{package}/"

_PACKAGE_HEADER_RE = re.compile(
    r'<a class="package-header"[^>]*href="https://f-droid\.org/[^"]*?/packages/([^"/]+)/?"[^>]*>'
    r'.*?<img class="package-icon"[^>]*src="([^"]*)"[^>]*>'
    r'.*?<h4 class="package-name">\s*([^<]+?)\s*</h4>'
    r'.*?<span class="package-summary">([^<]*)</span>',
    re.I | re.S,
)
_SUMMARY_RE = re.compile(
    r'itemprop="description"[^>]*content="([^"]+)"|'
    r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
    re.I,
)
_TITLE_RE = re.compile(r"<h3[^>]*itemprop=\"name\"[^>]*>\s*([^<]+)", re.I)
_ICON_RE = re.compile(r'<img[^>]+class="package-icon"[^>]+src="([^"]+)"', re.I)


class FDroidProvider(ApkProvider):
    """Official F-Droid repo (FOSS apps only)."""

    name = "fdroid"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "apk-updater/1.0 (F-Droid client)",
            "Accept": "application/json",
        }

    def get_latest(self, package_name: str) -> RemoteVersion | None:
        url = PACKAGES_URL.format(package=package_name)
        try:
            resp = self._client.get(url, headers=self._headers())
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None

        packages = data.get("packages") or []
        try:
            suggested = int(data.get("suggestedVersionCode") or 0)
        except (TypeError, ValueError):
            suggested = 0

        best_code = 0
        best_name = ""
        for item in packages:
            try:
                code = int(item.get("versionCode") or 0)
            except (TypeError, ValueError):
                continue
            if code > best_code:
                best_code = code
                best_name = str(item.get("versionName") or "")

        # Prefer suggested when present and listed; otherwise highest versionCode.
        if suggested > 0:
            for item in packages:
                try:
                    code = int(item.get("versionCode") or 0)
                except (TypeError, ValueError):
                    continue
                if code == suggested:
                    best_code = suggested
                    best_name = str(item.get("versionName") or best_name)
                    break

        if best_code <= 0:
            return None

        download_url = APK_URL.format(package=package_name, code=best_code)
        return RemoteVersion(
            package_name=package_name,
            version_name=best_name,
            version_code=best_code,
            source=self.name,
            download_url=download_url,
        )

    def _packages_json(self, package_name: str) -> dict | None:
        url = PACKAGES_URL.format(package=package_name)
        try:
            resp = self._client.get(url, headers=self._headers())
            if resp.status_code == 404 or resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def search(self, query: str, *, limit: int = 20) -> list[StoreSearchHit]:
        q = (query or "").strip()
        if not q:
            return []
        page_size = max(1, min(int(limit or 20), 40))
        hits: list[StoreSearchHit] = []

        # Exact package name
        if "." in q:
            remote = self.get_latest(q)
            if remote is not None:
                details = self.get_details(q)
                hits.append(
                    StoreSearchHit(
                        package_name=q,
                        title=(details.title if details else q),
                        source=self.name,
                        version_name=remote.version_name,
                        version_code=remote.version_code,
                        icon_url=(details.icon_url if details else ""),
                        developer=(details.developer if details else ""),
                        short_description=(details.short_description if details else ""),
                    )
                )

        try:
            resp = self._client.get(
                SEARCH_URL,
                params={"q": q, "lang": "en"},
                headers={
                    **self._headers(),
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 apk-updater",
                },
            )
            if resp.status_code == 200:
                for match in _PACKAGE_HEADER_RE.finditer(resp.text):
                    package = match.group(1).strip()
                    if not package or any(h.package_name == package for h in hits):
                        continue
                    hits.append(
                        StoreSearchHit(
                            package_name=package,
                            title=match.group(3).strip() or package,
                            source=self.name,
                            icon_url=match.group(2).strip(),
                            short_description=match.group(4).strip(),
                        )
                    )
                    if len(hits) >= page_size:
                        break
        except httpx.HTTPError:
            pass

        # Fill version for hits that only have package from HTML search
        for i, hit in enumerate(list(hits)):
            if hit.version_code > 0:
                continue
            remote = self.get_latest(hit.package_name)
            if remote is None:
                continue
            hits[i] = StoreSearchHit(
                package_name=hit.package_name,
                title=hit.title,
                source=self.name,
                version_name=remote.version_name,
                version_code=remote.version_code,
                icon_url=hit.icon_url,
                developer=hit.developer,
                short_description=hit.short_description,
            )
            if i + 1 >= page_size:
                break
        return hits[:page_size]

    def get_details(self, package_name: str) -> StoreAppDetails | None:
        remote = self.get_latest(package_name)
        if remote is None:
            return None
        title = package_name
        description = ""
        icon_url = ""
        try:
            resp = self._client.get(
                PACKAGE_PAGE_URL.format(package=package_name),
                headers={
                    **self._headers(),
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 apk-updater",
                },
            )
            if resp.status_code == 200:
                text = resp.text
                m_title = _TITLE_RE.search(text)
                if m_title:
                    title = m_title.group(1).strip()
                m_sum = _SUMMARY_RE.search(text)
                if m_sum:
                    description = (m_sum.group(1) or m_sum.group(2) or "").strip()
                m_icon = _ICON_RE.search(text)
                if m_icon:
                    icon_url = m_icon.group(1).strip()
        except httpx.HTTPError:
            pass
        return StoreAppDetails(
            package_name=package_name,
            title=title,
            source=self.name,
            description=description,
            short_description=description[:280] if description else "",
            icon_url=icon_url,
            version_name=remote.version_name,
            version_code=remote.version_code,
            download_url=remote.download_url,
        )

    def list_versions(self, package_name: str, *, limit: int = 20) -> list[StoreVersion]:
        data = self._packages_json(package_name)
        if not data:
            return super().list_versions(package_name, limit=limit)
        packages = data.get("packages") or []
        page_size = max(1, min(int(limit or 20), 50))
        out: list[StoreVersion] = []
        seen: set[int] = set()
        # Highest versionCode first
        parsed: list[tuple[int, str]] = []
        for item in packages:
            try:
                code = int(item.get("versionCode") or 0)
            except (TypeError, ValueError):
                continue
            if code <= 0 or code in seen:
                continue
            seen.add(code)
            parsed.append((code, str(item.get("versionName") or "")))
        parsed.sort(key=lambda x: x[0], reverse=True)
        for code, name in parsed[:page_size]:
            out.append(
                StoreVersion(
                    version_name=name,
                    version_code=code,
                    source=self.name,
                    download_url=APK_URL.format(package=package_name, code=code),
                )
            )
        return out

    def download(
        self, package_name: str, dest_dir: Path, *, remote: RemoteVersion | None = None
    ) -> ApkArtifact:
        info = remote or self.get_latest(package_name)
        if info is None:
            raise RuntimeError(f"F-Droid: package not found: {package_name}")
        url = info.download_url or APK_URL.format(
            package=package_name, code=info.version_code
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{info.version_code}.apk"
        download_file(url, dest, headers=self._headers())
        return ApkArtifact(
            package_name=package_name,
            version_name=info.version_name,
            version_code=info.version_code,
            source=self.name,
            file_path=dest,
            download_url=url,
        )
