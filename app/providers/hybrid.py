from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .apkpure import ApkPureProvider
from .aptoide import AptoideProvider
from .base import (
    ApkArtifact,
    ApkProvider,
    RemoteVersion,
    StoreAppDetails,
    StoreSearchHit,
    StoreVersion,
)
from .fdroid import FDroidProvider
from .rustore import RuStoreProvider
from .stores import STORE_ORDER


class HybridApkProvider(ApkProvider):
    """Query multiple stores; pick highest versionCode (RuStore preferred on ties)."""

    name = "hybrid"

    def __init__(
        self,
        rustore: ApkProvider | None = None,
        apkpure: ApkProvider | None = None,
        aptoide: ApkProvider | None = None,
        fdroid: ApkProvider | None = None,
        *,
        fallback: ApkProvider | None = None,
    ) -> None:
        self.rustore = rustore or RuStoreProvider()
        # `fallback` kept for older call sites (was APKPure).
        self.apkpure = apkpure or fallback or ApkPureProvider()
        self.fallback = self.apkpure
        self.aptoide = aptoide or AptoideProvider()
        self.fdroid = fdroid or FDroidProvider()
        self._by_name: dict[str, ApkProvider] = {
            self.rustore.name: self.rustore,
            self.apkpure.name: self.apkpure,
            self.aptoide.name: self.aptoide,
            self.fdroid.name: self.fdroid,
        }

    def _providers(
        self, *, enabled: set[str] | None = None
    ) -> list[ApkProvider]:
        ordered = [self._by_name[s] for s in STORE_ORDER if s in self._by_name]
        if not enabled:
            return ordered
        return [p for p in ordered if p.name in enabled]

    def provider_for(self, source: str) -> ApkProvider | None:
        return self._by_name.get(source)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        enabled: set[str] | None = None,
    ) -> list[StoreSearchHit]:
        providers = self._providers(enabled=enabled)
        if not providers:
            return []
        hits: list[StoreSearchHit] = []
        with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:
            futs = {
                pool.submit(p.search, query, limit=limit): p.name for p in providers
            }
            for fut in as_completed(futs):
                try:
                    hits.extend(fut.result() or [])
                except Exception:  # noqa: BLE001
                    continue
        return hits

    def get_details(
        self,
        package_name: str,
        *,
        source: str | None = None,
        enabled: set[str] | None = None,
    ) -> StoreAppDetails | None:
        if source:
            provider = self.provider_for(source)
            if provider is None:
                return None
            if enabled is not None and source not in enabled:
                return None
            try:
                return provider.get_details(package_name)
            except Exception:  # noqa: BLE001
                return None
        for provider in self._providers(enabled=enabled):
            try:
                details = provider.get_details(package_name)
            except Exception:  # noqa: BLE001
                details = None
            if details is not None:
                return details
        return None

    def get_details_by_source(
        self,
        package_name: str,
        *,
        enabled: set[str] | None = None,
    ) -> dict[str, StoreAppDetails]:
        providers = self._providers(enabled=enabled)
        out: dict[str, StoreAppDetails] = {}
        if not providers:
            return out
        with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:
            futs = {
                pool.submit(p.get_details, package_name): p.name for p in providers
            }
            for fut in as_completed(futs):
                source = futs[fut]
                try:
                    details = fut.result()
                except Exception:  # noqa: BLE001
                    details = None
                if details is not None:
                    out[source] = details
        return out

    def list_versions(
        self,
        package_name: str,
        *,
        source: str,
        limit: int = 20,
    ) -> list[StoreVersion]:
        provider = self.provider_for(source)
        if provider is None:
            return []
        try:
            return provider.list_versions(package_name, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def resolve_version(
        self,
        package_name: str,
        *,
        source: str,
        version_code: int | None = None,
    ) -> RemoteVersion | None:
        provider = self.provider_for(source)
        if provider is None:
            return None
        try:
            return provider.resolve_version(package_name, version_code=version_code)
        except Exception:  # noqa: BLE001
            return None

    def get_all_latest(
        self,
        package_name: str,
        *,
        enabled: set[str] | None = None,
    ) -> list[RemoteVersion]:
        """Query enabled stores and return all hits."""
        found: list[RemoteVersion] = []
        for provider in self._providers(enabled=enabled):
            try:
                remote = provider.get_latest(package_name)
            except Exception:  # noqa: BLE001
                remote = None
            if remote is not None:
                found.append(remote)
        return found

    def get_latest(
        self,
        package_name: str,
        *,
        enabled: set[str] | None = None,
    ) -> RemoteVersion | None:
        remotes = self.get_all_latest(package_name, enabled=enabled)
        if not remotes:
            return None
        best = remotes[0]
        for remote in remotes[1:]:
            if remote.version_code > best.version_code:
                best = remote
            elif (
                remote.version_code == best.version_code
                and remote.source == self.rustore.name
            ):
                best = remote
        return best

    def get_latest_preferring_newer_than(
        self,
        package_name: str,
        local_code: int,
        *,
        enabled: set[str] | None = None,
    ) -> RemoteVersion | None:
        """Return a store version newer than local_code; RuStore preferred when equal."""
        remotes = [
            r
            for r in self.get_all_latest(package_name, enabled=enabled)
            if r.version_code > local_code
        ]
        if not remotes:
            return None
        installable = [r for r in remotes if not r.unsupported_reason]
        pool = installable or remotes
        best = pool[0]
        for remote in pool[1:]:
            if remote.version_code > best.version_code:
                best = remote
            elif (
                remote.version_code == best.version_code
                and remote.source == self.rustore.name
            ):
                best = remote
        return best

    def download(
        self,
        package_name: str,
        dest_dir: Path,
        *,
        remote: RemoteVersion | None = None,
        enabled: set[str] | None = None,
        progress=None,
    ) -> ApkArtifact:
        info = remote or self.get_latest(package_name, enabled=enabled)
        if info is None:
            raise RuntimeError(f"No store listing for {package_name}")

        errors: list[str] = []
        order = self._providers(enabled=enabled)
        order = sorted(
            order,
            key=lambda p: 0 if info and p.name == info.source else 1,
        )

        for provider in order:
            try:
                use_remote = (
                    info if info.source == provider.name else provider.get_latest(package_name)
                )
                if use_remote is None:
                    continue
                return provider.download(
                    package_name, dest_dir, remote=use_remote, progress=progress
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.name}: {exc}")
                continue
        raise RuntimeError("; ".join(errors) or f"Download failed for {package_name}")
