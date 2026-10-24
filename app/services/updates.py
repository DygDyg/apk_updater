from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.database import Database
from app.providers.base import ApkArtifact, RemoteVersion
from app.providers.hybrid import HybridApkProvider
from app.providers.stores import STORE_ORDER, STORE_TITLES, setting_key
from app.providers.version_util import is_prerelease_version
from app.services.apps import AppInfo
from app.services.cache import ApkCache
from app.services.install import InstallResult, InstallService

PLAY_INSTALLER = "com.android.vending"
ProgressCb = Callable[[str], None]
ItemProgressCb = Callable[[str, str, str], None]  # serial, phase, detail
RemoteProgressCb = Callable[[int, int, str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_code(app: AppInfo) -> int:
    try:
        return int(app.version_code) if str(app.version_code).isdigit() else 0
    except ValueError:
        return 0


def _pick_best(
    stores: list[RemoteVersion],
    *,
    prefer_release: bool = True,
) -> RemoteVersion | None:
    if not stores:
        return None
    installable = [r for r in stores if not r.unsupported_reason] or list(stores)
    pool = installable
    if prefer_release:
        stable = [r for r in installable if not is_prerelease_version(r.version_name)]
        if stable:
            pool = stable
    best = pool[0]
    for remote in pool[1:]:
        if remote.version_code > best.version_code:
            best = remote
        elif remote.version_code == best.version_code and remote.source == "rustore":
            best = remote
    return best


@dataclass(frozen=True)
class UpdateInfo:
    package_name: str
    label: str
    local_code: int
    local_name: str
    remote: RemoteVersion | None
    skipped_play: bool = False
    status: str = ""  # update | up_to_date | not_found | play | unsupported | error
    error: str = ""
    stores: tuple[RemoteVersion, ...] = ()
    checked_sources: tuple[str, ...] = ()

    @property
    def has_update(self) -> bool:
        return self.status == "update" and self.remote is not None and not self.remote.unsupported_reason

    @property
    def manual_update(self) -> bool:
        """Newer installable APK exists in some store, but auto «Обновить» is not offered
        (e.g. highest hit is XAPK/unsupported — pick another store in app properties)."""
        if self.has_update:
            return False
        return any(
            (not s.unsupported_reason) and s.version_code > self.local_code
            for s in self.stores
        )

    @property
    def badge(self) -> str:
        if self.skipped_play or self.status == "play":
            return "Play — пропуск"
        if self.status == "update" and self.remote:
            src = self.remote.source
            name = self.remote.version_name or str(self.remote.version_code)
            if self.remote.unsupported_reason:
                return f"↑ {name} ({src}, не APK)"
            return f"↑ {name} ({src})"
        if self.manual_update:
            installable = [
                s
                for s in self.stores
                if not s.unsupported_reason and s.version_code > self.local_code
            ]
            if installable:
                best = max(installable, key=lambda s: s.version_code)
                name = best.version_name or str(best.version_code)
                return f"ручной выбор · ↑ {name} ({best.source})"
            return "ручной выбор стора"
        if self.status == "up_to_date":
            return "актуально"
        if self.status == "not_found":
            return "нет в сторах"
        if self.status == "error":
            return "ошибка проверки"
        return self.status or "—"

    def stores_tooltip(self, *, chosen_source: str = "") -> str:
        """Multi-line hover text: every store that was queried."""
        lines: list[str] = []
        chosen = chosen_source or (self.remote.source if self.remote else "")
        by_src = {s.source: s for s in self.stores}
        order = self.checked_sources or tuple(
            s for s in STORE_ORDER if s in by_src
        ) or STORE_ORDER
        for src in order:
            remote = by_src.get(src)
            title = STORE_TITLES.get(src, src)
            if remote is None:
                lines.append(f"{title}: не найдено")
                continue
            ver = remote.version_name or str(remote.version_code)
            mark = " ← выбрано" if remote.source == chosen else ""
            extra = f", code {remote.version_code}"
            if remote.unsupported_reason:
                extra += f", {remote.unsupported_reason}"
            lines.append(f"{title}: {ver}{extra}{mark}")
        for remote in self.stores:
            if remote.source in order:
                continue
            ver = remote.version_name or str(remote.version_code)
            mark = " ← выбрано" if remote.source == chosen else ""
            lines.append(f"{remote.source}: {ver} (code {remote.version_code}){mark}")
        if not lines:
            lines.append("Сторы не опрашивались (все выключены?)")
        return "\n".join(lines)


@dataclass
class FetchRemotesResult:
    best: dict[str, RemoteVersion | None] = field(default_factory=dict)
    stores: dict[str, tuple[RemoteVersion, ...]] = field(default_factory=dict)
    checked_sources: tuple[str, ...] = ()


class UpdateService:
    def __init__(
        self,
        db: Database,
        install: InstallService,
        cache: ApkCache | None = None,
        provider: HybridApkProvider | None = None,
    ) -> None:
        self.db = db
        self.install = install
        self.cache = cache or ApkCache()
        self.provider = provider or HybridApkProvider()

    def enabled_store_sources(self) -> set[str]:
        """Which stores to query (from settings). At least one is forced on."""
        enabled: set[str] = set()
        truthy = {"1", "true", "yes", "on"}
        for store_id in STORE_ORDER:
            raw = self.db.get_setting(setting_key(store_id), "1") or "1"
            if raw.strip().lower() in truthy:
                enabled.add(store_id)
        if not enabled:
            enabled.add("rustore")
        return enabled

    def prefer_release_updates(self) -> bool:
        """When True, beta/dev/rc store builds are not offered as updates over a release."""
        raw = self.db.get_setting("prefer_release_updates", "1") or "1"
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _latest_release_from_store(self, remote: RemoteVersion) -> RemoteVersion | None:
        """If tip is pre-release, resolve newest stable from that store's version list."""
        if not is_prerelease_version(remote.version_name):
            return remote
        try:
            versions = self.provider.list_versions(
                remote.package_name, source=remote.source, limit=50
            )
        except Exception:  # noqa: BLE001
            return None
        stable = [
            v
            for v in versions
            if v.version_code > 0 and not is_prerelease_version(v.version_name)
        ]
        if not stable:
            return None
        best = max(stable, key=lambda v: v.version_code)
        try:
            resolved = self.provider.resolve_version(
                remote.package_name,
                source=remote.source,
                version_code=best.version_code,
            )
        except Exception:  # noqa: BLE001
            resolved = None
        if resolved is not None and not is_prerelease_version(resolved.version_name):
            return resolved
        return RemoteVersion(
            package_name=remote.package_name,
            version_name=best.version_name,
            version_code=best.version_code,
            source=remote.source,
            download_url=best.download_url or remote.download_url,
            app_id=best.app_id or remote.app_id,
            unsupported_reason=best.unsupported_reason,
        )

    def _stabilize_store_hits(
        self, stores: list[RemoteVersion], *, prefer_release: bool
    ) -> list[RemoteVersion]:
        if not prefer_release or not stores:
            return stores
        out: list[RemoteVersion] = []
        for remote in stores:
            if not is_prerelease_version(remote.version_name):
                out.append(remote)
                continue
            stable = self._latest_release_from_store(remote)
            # Keep the tip when no stable exists so tooltips still show the beta;
            # build_update_info will refuse to offer it over a release install.
            out.append(stable if stable is not None else remote)
        return out

    def fetch_remotes(
        self,
        package_names: list[str],
        *,
        max_workers: int = 6,
        progress: RemoteProgressCb | None = None,
    ) -> FetchRemotesResult:
        """Query enabled stores per package; return best + all hits."""
        unique = sorted({p for p in package_names if p})
        enabled = self.enabled_store_sources()
        prefer_release = self.prefer_release_updates()
        checked = tuple(s for s in STORE_ORDER if s in enabled)
        out = FetchRemotesResult(checked_sources=checked)
        total = len(unique)
        done = 0

        def one(pkg: str) -> tuple[str, list[RemoteVersion]]:
            try:
                hits = list(self.provider.get_all_latest(pkg, enabled=enabled))
            except Exception:  # noqa: BLE001
                hits = []
            return pkg, self._stabilize_store_hits(hits, prefer_release=prefer_release)

        if not unique:
            return out

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(one, pkg): pkg for pkg in unique}
            for fut in as_completed(futures):
                pkg = futures[fut]
                done += 1
                try:
                    _, stores = fut.result()
                except Exception:  # noqa: BLE001
                    stores = []
                store_t = tuple(stores)
                out.stores[pkg] = store_t
                out.best[pkg] = _pick_best(stores, prefer_release=prefer_release)
                if progress:
                    progress(done, total, pkg)
        return out

    def build_update_info(
        self,
        app: AppInfo,
        remote: RemoteVersion | None,
        *,
        stores: tuple[RemoteVersion, ...] | list[RemoteVersion] | None = None,
        checked_sources: tuple[str, ...] | None = None,
        prefer_release: bool | None = None,
    ) -> UpdateInfo:
        local_code = _local_code(app)
        installer = (app.installer or "").strip()
        store_t = tuple(stores or ())
        checked = checked_sources or ()
        want_release = (
            self.prefer_release_updates() if prefer_release is None else prefer_release
        )

        if installer == PLAY_INSTALLER:
            return UpdateInfo(
                package_name=app.package_name,
                label=app.display_label,
                local_code=local_code,
                local_name=app.version_name,
                remote=None,
                skipped_play=True,
                status="play",
                stores=store_t,
                checked_sources=checked,
            )

        if remote is None and not store_t:
            return UpdateInfo(
                package_name=app.package_name,
                label=app.display_label,
                local_code=local_code,
                local_name=app.version_name,
                remote=None,
                status="not_found",
                stores=store_t,
                checked_sources=checked,
            )

        # Prefer a stable store build when the picked remote is beta/dev/rc
        # and the device already runs a release (or we only want releases).
        local_is_prerelease = is_prerelease_version(app.version_name)
        if want_release and store_t:
            if remote is None or (
                is_prerelease_version(remote.version_name) and not local_is_prerelease
            ):
                stable_best = _pick_best(list(store_t), prefer_release=True)
                if stable_best is not None and not is_prerelease_version(
                    stable_best.version_name
                ):
                    remote = stable_best
                elif remote is not None and is_prerelease_version(remote.version_name):
                    # Only beta/dev available — do not offer as an update over a release.
                    remote = None

        if remote is None:
            return UpdateInfo(
                package_name=app.package_name,
                label=app.display_label,
                local_code=local_code,
                local_name=app.version_name,
                remote=None,
                status="not_found" if not store_t else "up_to_date",
                stores=store_t,
                checked_sources=checked,
            )

        # Safety: never offer prerelease over a release when prefer_release is on
        if (
            want_release
            and not local_is_prerelease
            and is_prerelease_version(remote.version_name)
        ):
            return UpdateInfo(
                package_name=app.package_name,
                label=app.display_label,
                local_code=local_code,
                local_name=app.version_name,
                remote=remote,
                status="up_to_date",
                stores=store_t,
                checked_sources=checked,
            )

        status = "update" if remote.version_code > local_code else "up_to_date"
        if remote.unsupported_reason and status == "update":
            status = "unsupported"
        return UpdateInfo(
            package_name=app.package_name,
            label=app.display_label,
            local_code=local_code,
            local_name=app.version_name,
            remote=remote,
            status=status,
            error=remote.unsupported_reason or "",
            stores=store_t,
            checked_sources=checked,
        )

    def check_updates(
        self,
        apps: list[AppInfo],
        *,
        max_workers: int = 6,
        progress: ProgressCb | None = None,
        remotes: dict[str, RemoteVersion | None] | None = None,
        stores: dict[str, tuple[RemoteVersion, ...]] | None = None,
        checked_sources: tuple[str, ...] | None = None,
    ) -> dict[str, UpdateInfo]:
        """Build UpdateInfo per app; fetch remotes once for unique non-Play packages."""
        results: dict[str, UpdateInfo] = {}
        need_fetch: list[str] = []
        for app in apps:
            installer = (app.installer or "").strip()
            if installer == PLAY_INSTALLER:
                results[app.package_name] = self.build_update_info(app, None)
            else:
                need_fetch.append(app.package_name)

        stores_map: dict[str, tuple[RemoteVersion, ...]] = dict(stores or {})
        checked = checked_sources
        prefer_release = self.prefer_release_updates()
        if remotes is None:
            def on_prog(done: int, total: int, _pkg: str) -> None:
                if progress:
                    progress(f"Проверка обновлений ({done}/{total})…")

            fetched = self.fetch_remotes(need_fetch, max_workers=max_workers, progress=on_prog)
            remotes = fetched.best
            stores_map.update(fetched.stores)
            checked = fetched.checked_sources
        elif progress and need_fetch:
            progress(f"Проверка обновлений ({len(need_fetch)} пакетов)…")

        for app in apps:
            if app.package_name in results:
                continue
            pkg = app.package_name
            results[pkg] = self.build_update_info(
                app,
                (remotes or {}).get(pkg),
                stores=stores_map.get(pkg, ()),
                checked_sources=checked,
                prefer_release=prefer_release,
            )
        return results

    def download_to_cache(self, update: UpdateInfo, *, progress: ProgressCb | None = None) -> ApkArtifact:
        if not update.remote:
            raise RuntimeError("No remote version to download")
        remote = update.remote
        dest = self.cache.apk_path(update.package_name, remote.version_code)
        if dest.is_file() and dest.stat().st_size > 1024:
            if progress:
                progress(f"Кеш: {dest.name}")
            artifact = ApkArtifact(
                package_name=update.package_name,
                version_name=remote.version_name,
                version_code=remote.version_code,
                source=remote.source,
                file_path=dest,
                download_url=remote.download_url,
            )
            self._record_cache(artifact)
            return artifact

        if progress:
            progress(f"Скачивание {update.package_name} из {remote.source}…")
        artifact = self.provider.download(
            update.package_name,
            dest.parent,
            remote=remote,
            enabled=self.enabled_store_sources(),
        )
        if artifact.file_path.resolve() != dest.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if artifact.file_path.is_file():
                artifact.file_path.replace(dest)
                artifact = ApkArtifact(
                    package_name=artifact.package_name,
                    version_name=artifact.version_name,
                    version_code=artifact.version_code,
                    source=artifact.source,
                    file_path=dest,
                    download_url=artifact.download_url,
                )
        self._record_cache(artifact)
        return artifact

    def download_and_install(
        self,
        update: UpdateInfo,
        serials: list[str],
        *,
        progress: ProgressCb | None = None,
        item_progress: ItemProgressCb | None = None,
    ) -> tuple[ApkArtifact, list[InstallResult]]:
        artifact = self.download_to_cache(update, progress=progress)
        if progress:
            progress(f"Установка {artifact.file_path.name}…")
        results = self.install.install_on_devices(
            artifact.file_path,
            serials,
            item_progress=item_progress,
        )
        for r in results:
            self.db.execute(
                "UPDATE install_history SET package_name = ? WHERE device_serial = ? AND apk_path = ? "
                "AND created_at = (SELECT MAX(created_at) FROM install_history WHERE device_serial = ? AND apk_path = ?)",
                (update.package_name, r.serial, r.apk_path, r.serial, r.apk_path),
            )
            if r.success and artifact.source:
                try:
                    from app.services.package_sources import remember_install_source

                    remember_install_source(
                        self.db,
                        self.install.adb,
                        self.install.devices,
                        r.serial,
                        update.package_name,
                        artifact.source,
                    )
                except Exception:  # noqa: BLE001
                    pass
        return artifact, results

    def _record_cache(self, artifact: ApkArtifact) -> None:
        self.db.execute(
            "INSERT INTO apk_cache(package_name, version_code, version_name, source, file_path, downloaded_at, expires_at) "
            "VALUES(?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(package_name, version_code) DO UPDATE SET "
            "version_name=excluded.version_name, source=excluded.source, file_path=excluded.file_path, "
            "downloaded_at=excluded.downloaded_at",
            (
                artifact.package_name,
                artifact.version_code,
                artifact.version_name,
                artifact.source,
                str(artifact.file_path),
                _utc_now(),
            ),
        )
