from __future__ import annotations

from app.providers.base import StoreAppDetails, StoreSearchHit, StoreVersion
from app.providers.hybrid import HybridApkProvider
from app.providers.stores import STORE_ORDER, STORE_TITLES


def _details_dict(details: StoreAppDetails) -> dict:
    return {
        "package_name": details.package_name,
        "title": details.title,
        "source": details.source,
        "source_title": STORE_TITLES.get(details.source, details.source),
        "description": details.description,
        "short_description": details.short_description,
        "developer": details.developer,
        "icon_url": details.icon_url,
        "version_name": details.version_name,
        "version_code": details.version_code,
        "app_id": details.app_id,
        "download_url": details.download_url,
        "unsupported_reason": details.unsupported_reason,
    }


def _version_dict(ver: StoreVersion) -> dict:
    return {
        "version_name": ver.version_name,
        "version_code": ver.version_code,
        "source": ver.source,
        "source_title": STORE_TITLES.get(ver.source, ver.source),
        "download_url": ver.download_url,
        "app_id": ver.app_id,
        "unsupported_reason": ver.unsupported_reason,
        "label": ver.version_name or str(ver.version_code),
    }


def merge_search_hits(hits: list[StoreSearchHit], *, limit: int = 20) -> list[dict]:
    """Merge hits by package; collect sources; prefer richest title/icon."""
    by_pkg: dict[str, dict] = {}
    order: list[str] = []

    for hit in hits:
        pkg = hit.package_name
        if not pkg:
            continue
        if pkg not in by_pkg:
            order.append(pkg)
            by_pkg[pkg] = {
                "package_name": pkg,
                "title": hit.title or pkg,
                "icon_url": hit.icon_url,
                "developer": hit.developer,
                "short_description": hit.short_description,
                "sources": [],
                "source_titles": [],
                "version_name": hit.version_name,
                "version_code": hit.version_code,
            }
        row = by_pkg[pkg]
        if hit.source and hit.source not in row["sources"]:
            row["sources"].append(hit.source)
            row["source_titles"].append(STORE_TITLES.get(hit.source, hit.source))
        if hit.title and (not row["title"] or row["title"] == pkg):
            row["title"] = hit.title
        if hit.icon_url and not row["icon_url"]:
            row["icon_url"] = hit.icon_url
        if hit.developer and not row["developer"]:
            row["developer"] = hit.developer
        if hit.short_description and not row["short_description"]:
            row["short_description"] = hit.short_description
        if hit.version_code > int(row.get("version_code") or 0):
            row["version_code"] = hit.version_code
            row["version_name"] = hit.version_name

    # Stable source order
    for row in by_pkg.values():
        row["sources"] = [s for s in STORE_ORDER if s in row["sources"]] + [
            s for s in row["sources"] if s not in STORE_ORDER
        ]
        row["source_titles"] = [STORE_TITLES.get(s, s) for s in row["sources"]]

    return [by_pkg[pkg] for pkg in order[: max(1, min(int(limit or 20), 50))]]


class StoreSearchService:
    def __init__(self, provider: HybridApkProvider | None = None) -> None:
        self.provider = provider or HybridApkProvider()

    def search(
        self, query: str, *, limit: int = 20, enabled: set[str] | None = None
    ) -> list[dict]:
        hits = self.provider.search(query, limit=limit, enabled=enabled)
        return merge_search_hits(hits, limit=limit)

    def get_app_card(
        self, package_name: str, *, enabled: set[str] | None = None
    ) -> dict | None:
        by_source = self.provider.get_details_by_source(
            package_name, enabled=enabled
        )
        if not by_source:
            return None
        # Prefer title/icon from first available store in STORE_ORDER
        title = package_name
        icon_url = ""
        ordered_sources = [s for s in STORE_ORDER if s in by_source] + [
            s for s in by_source if s not in STORE_ORDER
        ]
        for src in ordered_sources:
            d = by_source[src]
            if d.title and title == package_name:
                title = d.title
            if d.icon_url and not icon_url:
                icon_url = d.icon_url
        return {
            "package_name": package_name,
            "title": title,
            "icon_url": icon_url,
            "sources": ordered_sources,
            "source_titles": [STORE_TITLES.get(s, s) for s in ordered_sources],
            "by_source": {s: _details_dict(d) for s, d in by_source.items()},
        }

    def list_versions(
        self, package_name: str, *, source: str, limit: int = 20
    ) -> list[dict]:
        versions = self.provider.list_versions(
            package_name, source=source, limit=limit
        )
        return [_version_dict(v) for v in versions]
