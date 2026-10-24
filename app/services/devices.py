from __future__ import annotations

from collections.abc import Callable
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.adb.bootstrap import resolve_adb
from app.adb.client import AdbClient, AdbError
from app.db.database import Database
from app.services.device_profile import (
    DeviceProfile,
    DeviceProfileService,
    resolve_marketing_name,
)
from app.services.discovery import DiscoveryService, DiscoveredEndpoint, EndpointKind
from app.services.qr_pairing import QrPairingResult, QrPairingService, QrPairingSession
from app.services.transport import (
    extract_ip,
    is_ip_transport,
    short_transport_label,
    transport_family,
    transport_sort_key,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class DeviceRecord:
    id: int | None
    serial: str
    address: str
    model: str
    alias: str
    last_seen: str
    state: str = "unknown"
    manufacturer: str = ""
    brand: str = ""
    android_version: str = ""
    sdk: str = ""
    market_name: str = ""
    preview_path: str = ""
    device_uid: str = ""
    transports: list[str] = field(default_factory=list)

    @property
    def online(self) -> bool:
        return self.state == "device"

    @property
    def label(self) -> str:
        name = self.alias or self.market_name or self.model or self.serial
        brand = self.brand or self.manufacturer
        if brand and name and brand.lower() not in name.lower():
            return f"{brand} {name}"
        return name

    @property
    def description(self) -> str:
        parts = []
        if self.android_version:
            api = f" (API {self.sdk})" if self.sdk else ""
            parts.append(f"Android {self.android_version}{api}")
        if self.model and self.market_name and self.model != self.market_name:
            parts.append(self.model)
        return " · ".join(parts) if parts else self.serial

    @property
    def channels_text(self) -> str:
        """Human-readable primary + fallback transports (deduped labels)."""
        items = self.transports or [self.serial]
        labels: list[str] = []
        seen: set[str] = set()
        for s in items:
            lab = short_transport_label(s)
            key = lab.lower()
            if key in seen:
                continue
            seen.add(key)
            labels.append(lab)
        if not labels:
            return f"канал: {self.serial}"
        if len(labels) == 1:
            return f"канал: {labels[0]}"
        extra = f" (+{len(labels) - 2})" if len(labels) > 2 else ""
        return f"канал: {labels[0]} · резерв: {labels[1]}{extra}"


class DeviceService:
    def __init__(self, db: Database, adb: AdbClient) -> None:
        self.db = db
        self.adb = adb
        self.discovery = DiscoveryService()
        self.qr_pairing = QrPairingService(adb, self.discovery)
        self.profiles = DeviceProfileService(adb)
        # ip:port / ip -> last probed friendly name (session cache)
        self._probe_label_cache: dict[str, str] = {}
        path = db.get_setting("adb_path")
        if path:
            self.adb.set_adb_path(path)

    def get_adb_path(self) -> str:
        return self.db.get_setting("adb_path", "") or ""

    def set_adb_path(self, path: str) -> None:
        path = path.strip()
        self.db.set_setting("adb_path", path)
        self.adb.set_adb_path(path or None)

    def check_adb(self) -> tuple[bool, str]:
        return self.adb.is_available()

    def ensure_adb(
        self,
        *,
        download: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]:
        preferred = self.get_adb_path() or None
        try:
            path, message = resolve_adb(preferred, download=download, progress=progress)
        except AdbError as exc:
            return False, str(exc)
        self.set_adb_path(path)
        return True, message

    def _row_to_record(self, row, live_state: str = "offline", live_model: str = "") -> DeviceRecord:
        def col(name: str) -> str:
            try:
                return row[name] or ""
            except (IndexError, KeyError):
                return ""

        serial = row["serial"]
        return DeviceRecord(
            id=row["id"],
            serial=serial,
            address=row["address"] or "",
            model=row["model"] or live_model,
            alias=row["alias"] or "",
            last_seen=row["last_seen"] or "",
            state=live_state,
            manufacturer=col("manufacturer"),
            brand=col("brand"),
            android_version=col("android_version"),
            sdk=col("sdk"),
            market_name=col("market_name"),
            preview_path=col("preview_path"),
            device_uid=col("device_uid"),
            transports=[serial],
        )

    def list_known(self) -> list[DeviceRecord]:
        """Logical devices: one row per phone, transports merged (best + fallbacks)."""
        rows = self._list_transport_rows()
        self._ensure_uids_for_online(rows)
        return self._merge_logical(rows)

    def _ensure_uids_for_online(self, rows: list[DeviceRecord]) -> None:
        """Fill hardware serial / android_id on every online transport before merge."""
        for rec in rows:
            if not rec.online or rec.device_uid:
                continue
            try:
                uid = self.read_device_uid(rec.serial)
            except AdbError:
                continue
            if not uid:
                continue
            rec.device_uid = uid
            # Persist so next merge works even if mdns/IP link is missing
            self.db.execute(
                "UPDATE devices SET device_uid = ? WHERE serial = ?",
                (uid, rec.serial),
            )

    def _match_live(self, serial: str, live: dict, live_by_family: dict):
        """Exact serial first, then mDNS family (short name ↔ full ._tcp name)."""
        hit = live.get(serial)
        if hit:
            return hit
        fam = transport_family(serial)
        if fam:
            return live_by_family.get(fam)
        return None

    def _list_transport_rows(self) -> list[DeviceRecord]:
        live_list = self._safe_devices()
        live = {d.serial: d for d in live_list}
        live_by_family: dict = {}
        for d in live_list:
            fam = transport_family(d.serial)
            if fam and (fam not in live_by_family or d.online):
                live_by_family[fam] = d
        rows = self.db.fetchall(
            "SELECT id, serial, address, model, alias, last_seen, "
            "manufacturer, brand, android_version, sdk, market_name, preview_path, device_uid "
            "FROM devices ORDER BY last_seen DESC"
        )
        result: list[DeviceRecord] = []
        seen_serials: set[str] = set()
        matched_live: set[str] = set()
        for row in rows:
            serial = row["serial"]
            seen_serials.add(serial)
            live_dev = self._match_live(serial, live, live_by_family)
            if live_dev:
                matched_live.add(live_dev.serial)
            result.append(
                self._row_to_record(
                    row,
                    live_state=live_dev.state if live_dev else "offline",
                    live_model=live_dev.model if live_dev else "",
                )
            )
        for serial, live_dev in live.items():
            if serial in seen_serials or serial in matched_live:
                continue
            # Also skip if DB already has same mDNS family under another key
            fam = transport_family(serial)
            if fam and any(transport_family(s) == fam for s in seen_serials):
                continue
            address = serial if ":" in serial else ""
            rec = DeviceRecord(
                id=None,
                serial=serial,
                address=address,
                model=live_dev.model,
                alias="",
                last_seen=_utc_now(),
                state=live_dev.state,
                transports=[serial],
            )
            self.upsert(rec)
            result.insert(0, rec)
        return result

    def _mdns_ip_map(self) -> dict[str, str]:
        """Map mdns instance / full name → IP."""
        mapping: dict[str, str] = {}
        try:
            for name, stype, ip, port in self.adb.mdns_services():
                if "connect" not in stype:
                    continue
                mapping[name] = ip
                mapping[f"{name}._adb-tls-connect._tcp"] = ip
                mapping[transport_family(name)] = ip
        except AdbError:
            pass
        return mapping

    def _merge_logical(self, records: list[DeviceRecord]) -> list[DeviceRecord]:
        if not records:
            return []

        parent = {r.serial: r.serial for r in records}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        by_uid: dict[str, list[str]] = {}
        by_fam: dict[str, list[str]] = {}
        by_ip: dict[str, list[str]] = {}
        mdns_ip = self._mdns_ip_map()

        for r in records:
            if r.device_uid:
                by_uid.setdefault(r.device_uid, []).append(r.serial)
            by_fam.setdefault(transport_family(r.serial), []).append(r.serial)
            ip = extract_ip(r.serial)
            if not ip:
                ip = mdns_ip.get(r.serial) or mdns_ip.get(transport_family(r.serial))
            if ip:
                by_ip.setdefault(ip, []).append(r.serial)

        for group in (*by_uid.values(), *by_fam.values(), *by_ip.values()):
            head = group[0]
            for s in group[1:]:
                union(head, s)

        buckets: dict[str, list[DeviceRecord]] = {}
        for r in records:
            buckets.setdefault(find(r.serial), []).append(r)

        merged = [self._collapse_group(members) for members in buckets.values()]
        merged.sort(key=lambda r: (not r.online, r.label.lower(), r.serial.lower()))
        return merged

    def _collapse_group(self, members: list[DeviceRecord]) -> DeviceRecord:
        ranked = sorted(
            members,
            key=lambda r: transport_sort_key(r.serial, online=r.online),
        )
        online_members = [m for m in ranked if m.online]
        primary = online_members[0] if online_members else ranked[0]
        transports = [m.serial for m in online_members] + [
            m.serial for m in ranked if not m.online
        ]
        # Deduplicate by exact serial and by transport family (short/full mdns)
        seen_serial: set[str] = set()
        seen_family: set[str] = set()
        ordered: list[str] = []
        for s in transports:
            if s in seen_serial:
                continue
            fam = transport_family(s)
            if fam in seen_family:
                continue
            seen_serial.add(s)
            seen_family.add(fam)
            ordered.append(s)

        best_meta = max(
            members,
            key=lambda r: sum(
                bool(x)
                for x in (
                    r.market_name,
                    r.preview_path,
                    r.android_version,
                    r.brand,
                    r.manufacturer,
                    r.alias,
                    r.device_uid,
                )
            ),
        )
        online = bool(online_members)
        shared_uid = (
            best_meta.device_uid
            or primary.device_uid
            or next((m.device_uid for m in members if m.device_uid), "")
        )
        # Stamp UID onto all transports so IP ↔ mDNS keep merging next time
        if shared_uid:
            for m in members:
                if m.device_uid == shared_uid:
                    continue
                m.device_uid = shared_uid
                try:
                    self.db.execute(
                        "UPDATE devices SET device_uid = ? WHERE serial = ?",
                        (shared_uid, m.serial),
                    )
                except Exception:  # noqa: BLE001
                    pass

        return DeviceRecord(
            id=primary.id,
            serial=primary.serial,
            address=primary.address or (primary.serial if ":" in primary.serial else ""),
            model=best_meta.model or primary.model,
            alias=best_meta.alias or primary.alias,
            last_seen=max((m.last_seen for m in members), default=primary.last_seen),
            state="device" if online else primary.state,
            manufacturer=best_meta.manufacturer or primary.manufacturer,
            brand=best_meta.brand or primary.brand,
            android_version=best_meta.android_version or primary.android_version,
            sdk=best_meta.sdk or primary.sdk,
            market_name=best_meta.market_name or primary.market_name,
            preview_path=best_meta.preview_path or primary.preview_path,
            device_uid=shared_uid,
            transports=ordered,
        )

    def transport_candidates(self, serial: str) -> list[str]:
        """Ordered online transports for a logical device (best first)."""
        live = {d.serial for d in self._safe_devices() if d.state == "device"}
        for rec in self.list_known():
            if serial == rec.serial or serial in rec.transports:
                online = [s for s in rec.transports if s in live]
                return online or ([serial] if serial in live else rec.transports[:1] or [serial])
        return [serial] if serial in live else [serial]

    def resolve_online(self, serial: str) -> str:
        cands = self.transport_candidates(serial)
        return cands[0] if cands else serial

    def prepare_online(self, serial: str, *, hold_awake: bool = True) -> str:
        """Resolve a live transport; wake only if shell is silent.

        Raises AdbError when the device still does not answer after wake —
        callers should skip it instead of hanging on dumpsys/install.
        """
        from app.adb.client import AdbError

        working = self.resolve_online(serial)
        try:
            ok = self.adb.prepare_for_work(working, hold_awake=hold_awake)
        except AdbError as exc:
            raise AdbError(
                f"Устройство не отвечает: {working} ({exc}). Пропуск."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise AdbError(
                f"Устройство не отвечает: {working} ({exc}). Пропуск."
            ) from exc
        if not ok:
            raise AdbError(
                f"Устройство не отвечает после пробуждения (экран заблокирован?): "
                f"{working}. Пропуск."
            )
        return working

    def release_online(self, serial: str) -> None:
        try:
            self.adb.release_after_work(serial)
        except Exception:  # noqa: BLE001
            pass

    def read_device_uid(self, serial: str) -> str:
        """Stable hardware identity: ro.serialno (preferred), then android_id."""
        props = (
            "ro.serialno",
            "ro.boot.serialno",
            "ro.ril.oem.psno",
        )
        try:
            cmd = " && ".join(f"echo __{p}=$(getprop {p})" for p in props)
            result = self.adb.shell(serial, cmd, check=False, timeout=12.0)
            blob = result.stdout or ""
        except AdbError:
            blob = ""
        for prop in props:
            marker = f"__{prop}="
            for line in blob.splitlines():
                line = line.strip()
                if not line.startswith(marker):
                    continue
                sn = line[len(marker) :].strip()
                if sn and sn.lower() not in ("unknown", "null", ""):
                    return f"sn:{sn}"
        try:
            result = self.adb.shell(
                serial, "settings get secure android_id", check=False, timeout=10.0
            )
            aid = (result.stdout or "").strip()
        except AdbError:
            aid = ""
        if aid and aid.lower() not in ("null", "unknown", ""):
            return f"aid:{aid}"
        return ""

    def _known_host_ports(self) -> list[str]:
        """IP:port endpoints remembered in the registry (primary + address)."""
        rows = self.db.fetchall("SELECT serial, address FROM devices")
        out: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for candidate in (row["serial"] or "", row["address"] or ""):
                cand = candidate.strip()
                if not is_ip_transport(cand):
                    continue
                key = cand.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(cand)
        return out

    def _reconnect_known_wireless(self) -> None:
        """Re-attach wireless devices after adb daemon restart / Wi‑Fi sleep."""
        try:
            live = self._safe_devices()
        except AdbError:
            live = []
        online_fams = {
            transport_family(d.serial) for d in live if d.online and transport_family(d.serial)
        }
        online_ips = {extract_ip(d.serial) for d in live if d.online and extract_ip(d.serial)}

        # 1) Saved IP:port from registry
        for host_port in self._known_host_ports():
            ip = extract_ip(host_port)
            if ip and ip in online_ips:
                continue
            try:
                self.adb.connect(host_port)
            except AdbError:
                pass

        # 2) Current mDNS connect services for known families
        known_fams: set[str] = set()
        try:
            for row in self.db.fetchall("SELECT serial FROM devices"):
                fam = transport_family(row["serial"] or "")
                if fam:
                    known_fams.add(fam)
        except Exception:  # noqa: BLE001
            known_fams = set()

        try:
            services = self.adb.mdns_services()
        except AdbError:
            services = []
        for name, stype, ip, port in services:
            if "connect" not in (stype or "").lower():
                continue
            if not ip or not port:
                continue
            fam = transport_family(name) or transport_family(
                f"{name}._adb-tls-connect._tcp"
            )
            # Only auto-connect devices already in the registry
            if known_fams and fam and fam not in known_fams:
                continue
            if fam and fam in online_fams:
                continue
            if ip in online_ips:
                continue
            try:
                self.adb.connect(f"{ip}:{port}")
            except AdbError:
                pass

    def refresh(self, *, with_profiles: bool = True) -> list[DeviceRecord]:
        self._reconnect_known_wireless()
        for live_dev in self._safe_devices():
            address = live_dev.serial if ":" in live_dev.serial else ""
            model = live_dev.model
            rec = DeviceRecord(
                id=None,
                serial=live_dev.serial,
                address=address,
                model=model,
                alias="",
                last_seen=_utc_now(),
                state=live_dev.state,
                transports=[live_dev.serial],
            )
            if live_dev.online:
                try:
                    rec.device_uid = self.read_device_uid(live_dev.serial)
                except AdbError:
                    pass
            if live_dev.online and with_profiles:
                try:
                    profile = self.profiles.fetch(live_dev.serial, refresh_preview=False)
                    self.apply_profile(rec, profile)
                except AdbError:
                    if not model:
                        try:
                            rec.model = self.adb.getprop(live_dev.serial, "ro.product.model")
                        except AdbError:
                            pass
            elif live_dev.online and not model:
                try:
                    rec.model = self.adb.getprop(live_dev.serial, "ro.product.model")
                except AdbError:
                    pass
            self.upsert(rec)
        return self.list_known()

    def apply_profile(self, device: DeviceRecord, profile: DeviceProfile) -> None:
        device.manufacturer = profile.manufacturer or device.manufacturer
        device.brand = profile.brand or device.brand
        device.model = profile.model or device.model
        device.market_name = profile.market_name or device.market_name
        device.android_version = profile.android_version or device.android_version
        device.sdk = profile.sdk or device.sdk
        device.preview_path = profile.preview_path or device.preview_path

    def fetch_profile(self, serial: str, *, refresh_preview: bool = True) -> DeviceProfile:
        working = self.resolve_online(serial)
        profile = self.profiles.fetch(working, refresh_preview=refresh_preview)
        rec = DeviceRecord(
            id=None,
            serial=working,
            address=working if ":" in working else "",
            model=profile.model,
            alias="",
            last_seen=_utc_now(),
            state="device",
            transports=[working],
        )
        try:
            rec.device_uid = self.read_device_uid(working)
        except AdbError:
            pass
        self.apply_profile(rec, profile)
        self.upsert(rec)
        return profile

    def upsert(self, device: DeviceRecord) -> None:
        existing = self.db.fetchone(
            "SELECT id, alias, model, address, manufacturer, brand, android_version, sdk, "
            "market_name, preview_path, device_uid FROM devices WHERE serial = ?",
            (device.serial,),
        )
        if existing:
            alias = device.alias or existing["alias"] or ""
            model = device.model or existing["model"] or ""
            address = device.address or existing["address"] or ""
            manufacturer = device.manufacturer or existing["manufacturer"] or ""
            brand = device.brand or existing["brand"] or ""
            android_version = device.android_version or existing["android_version"] or ""
            sdk = device.sdk or existing["sdk"] or ""
            market_name = device.market_name or existing["market_name"] or ""
            preview_path = device.preview_path or existing["preview_path"] or ""
            try:
                existing_uid = existing["device_uid"] or ""
            except (IndexError, KeyError):
                existing_uid = ""
            device_uid = device.device_uid or existing_uid or ""
            self.db.execute(
                "UPDATE devices SET address=?, model=?, alias=?, last_seen=?, "
                "manufacturer=?, brand=?, android_version=?, sdk=?, market_name=?, preview_path=?, "
                "device_uid=? WHERE serial=?",
                (
                    address,
                    model,
                    alias,
                    device.last_seen or _utc_now(),
                    manufacturer,
                    brand,
                    android_version,
                    sdk,
                    market_name,
                    preview_path,
                    device_uid,
                    device.serial,
                ),
            )
            device.id = existing["id"]
            device.alias = alias
            device.model = model
            device.address = address
            device.manufacturer = manufacturer
            device.brand = brand
            device.android_version = android_version
            device.sdk = sdk
            device.market_name = market_name
            device.preview_path = preview_path
            device.device_uid = device_uid
        else:
            cur = self.db.execute(
                "INSERT INTO devices(serial, address, model, alias, last_seen, "
                "manufacturer, brand, android_version, sdk, market_name, preview_path, device_uid) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device.serial,
                    device.address,
                    device.model,
                    device.alias,
                    device.last_seen or _utc_now(),
                    device.manufacturer,
                    device.brand,
                    device.android_version,
                    device.sdk,
                    device.market_name,
                    device.preview_path,
                    device.device_uid,
                ),
            )
            device.id = cur.lastrowid

    def set_alias(self, serial: str, alias: str) -> None:
        # Apply alias to all transports of the logical device
        for rec in self.list_known():
            if serial == rec.serial or serial in rec.transports:
                for s in rec.transports:
                    self.db.execute("UPDATE devices SET alias = ? WHERE serial = ?", (alias.strip(), s))
                return
        self.db.execute("UPDATE devices SET alias = ? WHERE serial = ?", (alias.strip(), serial))

    def remove(self, serial: str) -> None:
        for rec in self.list_known():
            if serial == rec.serial or serial in rec.transports:
                for s in rec.transports:
                    self.db.execute("DELETE FROM devices WHERE serial = ?", (s,))
                return
        self.db.execute("DELETE FROM devices WHERE serial = ?", (serial,))

    def pair(self, ip: str, pairing_port: str, code: str) -> str:
        host_port = f"{ip.strip()}:{pairing_port.strip()}"
        result = self.adb.pair(host_port, code)
        if not result.ok and "Successfully paired" not in result.combined:
            raise AdbError(result.combined or "Pairing failed")
        return result.combined or "Successfully paired"

    def connect(self, ip: str, connect_port: str) -> str:
        host_port = f"{ip.strip()}:{connect_port.strip()}"
        result = self.adb.connect(host_port)
        text = result.combined
        if not result.ok and "connected" not in text.lower():
            raise AdbError(text or "Connect failed")
        self.refresh()
        return text or f"connected to {host_port}"

    def disconnect(self, serial_or_address: str) -> str:
        messages: list[str] = []
        targets: list[str] = []
        for rec in self.list_known():
            if serial_or_address == rec.serial or serial_or_address in rec.transports:
                targets = list(rec.transports)
                break
        if not targets:
            targets = [serial_or_address]
        for target in targets:
            result = self.adb.disconnect(target)
            messages.append(result.combined or f"disconnected {target}")
        return "; ".join(messages)

    def online_devices(self) -> list[DeviceRecord]:
        return [d for d in self.list_known() if d.online]

    def discover_network(self, duration: float = 4.5) -> list[DiscoveredEndpoint]:
        """mDNS wireless debugging + LAN sweep for classic ADB TCP (:5555)."""
        found = {ep.key: ep for ep in self.discovery.scan(duration=duration)}
        try:
            for name, stype, ip, port in self.adb.mdns_services():
                if "pairing" in stype:
                    kind = EndpointKind.PAIRING
                elif "connect" in stype:
                    kind = EndpointKind.CONNECT
                else:
                    kind = EndpointKind.LEGACY
                ep = DiscoveredEndpoint(kind=kind, name=name, ip=ip, port=port, full_name=name)
                found[ep.key] = ep
        except AdbError:
            pass
        return sorted(found.values(), key=lambda e: (e.kind.value, e.ip, e.port))

    def label_for_discovered(self, ep: DiscoveredEndpoint) -> str:
        """Friendly name from registry when IP / mDNS matches a known device."""
        known = self.list_known()
        ep_family = transport_family(ep.short_name or ep.name or "")
        ep_ip = (ep.ip or "").strip()
        for rec in known:
            if ep_ip:
                for t in rec.transports or [rec.serial]:
                    tip = extract_ip(t)
                    if tip and tip == ep_ip:
                        return rec.label
                if ep_ip in (rec.channels_text or "") or ep_ip in (rec.address or ""):
                    return rec.label
            if ep_family:
                for t in rec.transports or [rec.serial]:
                    if transport_family(t) == ep_family:
                        return rec.label
                if rec.device_uid and rec.device_uid.lower() in ep_family:
                    return rec.label
                # adb-<serialno>-xxx often embeds hardware serial
                serial_l = (rec.serial or "").lower()
                if serial_l and len(serial_l) >= 6 and serial_l in ep_family:
                    return rec.label
                uid = (rec.device_uid or "").lower()
                if uid and len(uid) >= 6 and uid in ep_family:
                    return rec.label
        cached = self._probe_label_cache.get(ep.host_port) or self._probe_label_cache.get(ep_ip)
        return cached or ""

    def probe_discovered_name(self, ep: DiscoveredEndpoint) -> str:
        """
        Ask the device its name via a short ADB connect + getprop.
        Only for connect/legacy (pairing needs a code first).
        Temporarily connected hosts are disconnected again.
        """
        if ep.kind == EndpointKind.PAIRING:
            return ""
        host_port = ep.host_port
        if not host_port:
            return ""
        cached = self._probe_label_cache.get(host_port)
        if cached:
            return cached

        live_before = {d.serial for d in self._safe_devices() if d.state == "device"}
        already = host_port in live_before
        connected_by_us = False
        try:
            if not already:
                self.adb.connect(host_port)
                connected_by_us = True
                time.sleep(0.35)
            props = (
                "ro.product.brand",
                "ro.product.manufacturer",
                "ro.product.model",
                "ro.product.marketname",
                "ro.oppo.market.name",
                "ro.vendor.oplus.market.name",
            )
            cmd = " && ".join(f"echo __{p}=$(getprop {p})" for p in props)
            result = self.adb.shell(host_port, cmd, check=False, timeout=10.0)
            blob = result.stdout or ""
            got: dict[str, str] = {}
            for prop in props:
                marker = f"__{prop}="
                for line in blob.splitlines():
                    line = line.strip()
                    if line.startswith(marker):
                        got[prop] = line[len(marker) :].strip()
                        break
            brand = got.get("ro.product.brand") or got.get("ro.product.manufacturer") or ""
            model = got.get("ro.product.model") or ""
            market = (
                got.get("ro.product.marketname")
                or got.get("ro.oppo.market.name")
                or got.get("ro.vendor.oplus.market.name")
                or ""
            )
            market = resolve_marketing_name(brand=brand, model=model, market_name=market)
            name = market or model
            if not name:
                return ""
            if brand and name and brand.lower() not in name.lower():
                label = f"{brand} {name}"
            else:
                label = name
            label = label.strip()
            if label:
                self._probe_label_cache[host_port] = label
                if ep.ip:
                    self._probe_label_cache[ep.ip] = label
            return label
        except AdbError:
            return ""
        finally:
            if connected_by_us and host_port not in live_before:
                try:
                    self.adb.disconnect(host_port)
                except AdbError:
                    pass

    def resolve_discovered_labels(
        self,
        endpoints: list[DiscoveredEndpoint],
        *,
        probe_unknown: bool = True,
    ) -> dict[str, str]:
        """Registry match first; optionally ADB-probe remaining connect/legacy endpoints."""
        labels: dict[str, str] = {}
        need_probe: list[DiscoveredEndpoint] = []
        for ep in endpoints:
            lab = self.label_for_discovered(ep)
            if lab:
                labels[ep.key] = lab
            elif probe_unknown and ep.kind != EndpointKind.PAIRING:
                need_probe.append(ep)

        if not need_probe:
            return labels

        # Deduplicate by host_port so we don't probe the same device twice
        by_hp: dict[str, list[DiscoveredEndpoint]] = {}
        for ep in need_probe:
            by_hp.setdefault(ep.host_port, []).append(ep)

        def one(hp: str) -> tuple[str, str]:
            ep0 = by_hp[hp][0]
            return hp, self.probe_discovered_name(ep0)

        with ThreadPoolExecutor(max_workers=min(4, len(by_hp))) as pool:
            futs = [pool.submit(one, hp) for hp in by_hp]
            for fut in as_completed(futs):
                try:
                    hp, lab = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if not lab:
                    continue
                for ep in by_hp[hp]:
                    labels[ep.key] = lab
        return labels

    def connect_discovered(self, endpoint: DiscoveredEndpoint) -> str:
        if endpoint.kind == EndpointKind.PAIRING:
            raise AdbError("This endpoint is for pairing — enter the 6-digit code or use QR")
        return self.connect(endpoint.ip, str(endpoint.port))

    def pair_discovered(self, endpoint: DiscoveredEndpoint, code: str) -> str:
        msg = self.pair(endpoint.ip, str(endpoint.port), code)
        try:
            connect_eps = [
                e
                for e in self.discover_network(duration=3.0)
                if e.ip == endpoint.ip and e.kind == EndpointKind.CONNECT
            ]
            if connect_eps:
                self.connect_discovered(connect_eps[0])
        except AdbError:
            pass
        return msg

    def start_qr_session(self) -> QrPairingSession:
        return QrPairingSession.create()

    def complete_qr_session(
        self,
        session: QrPairingSession,
        *,
        timeout: float = 90.0,
        cancel_event=None,
        progress: Callable[[str], None] | None = None,
    ) -> QrPairingResult:
        result = self.qr_pairing.run_session(
            session,
            timeout=timeout,
            cancel_event=cancel_event,
            progress=progress,
        )
        if result.success:
            self.refresh()
        return result

    def _safe_devices(self):
        try:
            return self.adb.devices()
        except AdbError:
            return []
