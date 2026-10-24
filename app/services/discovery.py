from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

# Classic `adb tcpip` listen port (Android TV / old wireless ADB)
LEGACY_ADB_PORTS = (5555,)


class EndpointKind(str, Enum):
    CONNECT = "connect"
    PAIRING = "pairing"
    LEGACY = "legacy"


SERVICE_CONNECT = "_adb-tls-connect._tcp.local."
SERVICE_PAIRING = "_adb-tls-pairing._tcp.local."
SERVICE_LEGACY = "_adb._tcp.local."


@dataclass(frozen=True)
class DiscoveredEndpoint:
    kind: EndpointKind
    name: str
    ip: str
    port: int
    full_name: str = ""

    @property
    def host_port(self) -> str:
        return f"{self.ip}:{self.port}"

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.ip}:{self.port}"

    @property
    def short_name(self) -> str:
        # "adb-XXXX._adb-tls-connect._tcp.local." -> "adb-XXXX"
        base = self.name or self.full_name
        return base.split(".", 1)[0] if base else self.host_port


def _addresses_from_info(info: ServiceInfo) -> list[str]:
    addrs: list[str] = []
    parsed = info.parsed_addresses()
    for addr in parsed:
        # Prefer IPv4
        if ":" not in addr:
            addrs.append(addr)
    if not addrs:
        addrs = list(parsed)
    return addrs


class _CollectListener(ServiceListener):
    def __init__(self, kind: EndpointKind, sink: dict[str, DiscoveredEndpoint]) -> None:
        self.kind = kind
        self.sink = sink
        self._lock = threading.Lock()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._ingest(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._ingest(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            for key, ep in list(self.sink.items()):
                if ep.full_name == name and ep.kind == self.kind:
                    del self.sink[key]

    def _ingest(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=3000)
        if info is None or not info.port:
            return
        for ip in _addresses_from_info(info):
            ep = DiscoveredEndpoint(
                kind=self.kind,
                name=name.split(".", 1)[0],
                ip=ip,
                port=int(info.port),
                full_name=name,
            )
            with self._lock:
                self.sink[ep.key] = ep


def _tcp_open(ip: str, port: int, *, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ipv4_lan_score(ip: str) -> int:
    """Lower is better. Prefer home LAN over Docker/Hyper-V/VPN ranges."""
    parts = ip.split(".")
    if len(parts) != 4:
        return 100
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return 100
    if a == 127:
        return 100
    if a == 192 and b == 168:
        return 0
    if a == 10:
        return 1
    # 172.16.0.0/12 — often Docker/Hyper-V; keep as last resort
    if a == 172 and 16 <= b <= 31:
        return 8
    if a == 169 and b == 254:
        return 90
    return 5


def local_ipv4_candidates() -> list[str]:
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    # stable unique, best LAN first
    uniq = sorted(set(found), key=_ipv4_lan_score)
    return uniq


def scan_legacy_tcp(
    *,
    ports: tuple[int, ...] = LEGACY_ADB_PORTS,
    timeout: float = 0.4,
    workers: int = 64,
) -> list[DiscoveredEndpoint]:
    """Sweep local /24 for hosts with classic ADB TCP ports open (usually 5555).

    Does not prove the service is ADB — only that the port accepts TCP.
    Connect + getprop later confirms the device.
    """
    candidates = local_ipv4_candidates()
    if not candidates:
        return []
    # Prefer 192.168/10; only fall back to 172.16/12 if nothing else
    hint = candidates[0]
    if _ipv4_lan_score(hint) >= 8:
        # Still allow scan, but caller may get Docker noise — skip if only docker-like
        better = [c for c in candidates if _ipv4_lan_score(c) < 8]
        if better:
            hint = better[0]
        else:
            return []

    parts = hint.split(".")
    try:
        if not all(0 <= int(p) <= 255 for p in parts):
            return []
    except ValueError:
        return []
    prefix = ".".join(parts[:3])
    self_ip = hint

    targets: list[tuple[str, int]] = []
    for host in range(1, 255):
        ip = f"{prefix}.{host}"
        if ip == self_ip:
            continue
        for port in ports:
            targets.append((ip, port))

    open_ports: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=max(8, min(workers, 96))) as pool:
        futs = {
            pool.submit(_tcp_open, ip, port, timeout=timeout): (ip, port)
            for ip, port in targets
        }
        for fut in as_completed(futs):
            ip, port = futs[fut]
            try:
                ok = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if ok:
                open_ports.append((ip, port))

    # Guard: a whole /24 "open" means wrong NIC / broken stack — discard
    if len(open_ports) > 40:
        return []

    return [
        DiscoveredEndpoint(
            kind=EndpointKind.LEGACY,
            name=f"adb-tcp-{port}",
            ip=ip,
            port=port,
            full_name=f"legacy:{ip}:{port}",
        )
        for ip, port in sorted(open_ports)
    ]


class DiscoveryService:
    """Browse LAN for Android wireless debugging mDNS + legacy TCP :5555."""

    def scan(self, duration: float = 4.0) -> list[DiscoveredEndpoint]:
        sink: dict[str, DiscoveredEndpoint] = {}
        legacy_hits: list[DiscoveredEndpoint] = []

        def _tcp_job() -> None:
            try:
                legacy_hits.extend(scan_legacy_tcp())
            except Exception:  # noqa: BLE001
                pass

        tcp_thread = threading.Thread(target=_tcp_job, name="adb-legacy-scan", daemon=True)
        tcp_thread.start()

        zc = Zeroconf()
        browsers = [
            ServiceBrowser(zc, SERVICE_CONNECT, _CollectListener(EndpointKind.CONNECT, sink)),
            ServiceBrowser(zc, SERVICE_PAIRING, _CollectListener(EndpointKind.PAIRING, sink)),
            ServiceBrowser(zc, SERVICE_LEGACY, _CollectListener(EndpointKind.LEGACY, sink)),
        ]
        try:
            # TCP /24 scan usually finishes in ~1–3s; keep mDNS window as requested
            time.sleep(max(0.5, duration))
        finally:
            for browser in browsers:
                try:
                    browser.cancel()
                except Exception:  # noqa: BLE001
                    pass
            zc.close()

        tcp_thread.join(timeout=max(2.0, duration + 2.0))
        for ep in legacy_hits:
            # Prefer tls-connect over generic open:5555 on the same host:port
            if ep.key in sink:
                continue
            # If same IP already has a modern connect endpoint, still keep :5555
            # as a separate legacy target (different port almost always).
            sink[ep.key] = ep
        return sorted(sink.values(), key=lambda e: (e.kind.value, e.ip, e.port))

    def wait_for_pairing_named(
        self,
        service_name: str,
        *,
        timeout: float = 90.0,
        cancel_event: threading.Event | None = None,
    ) -> DiscoveredEndpoint | None:
        """Wait until a pairing service whose name contains service_name appears."""
        needle = service_name.lower()
        sink: dict[str, DiscoveredEndpoint] = {}
        zc = Zeroconf()
        browser = ServiceBrowser(zc, SERVICE_PAIRING, _CollectListener(EndpointKind.PAIRING, sink))
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return None
                for ep in list(sink.values()):
                    hay = f"{ep.name} {ep.full_name}".lower()
                    if needle in hay:
                        return ep
                time.sleep(0.25)
        finally:
            try:
                browser.cancel()
            except Exception:  # noqa: BLE001
                pass
            zc.close()
        return None

    def wait_for_connect_near(
        self,
        ip: str,
        *,
        timeout: float = 20.0,
        cancel_event: threading.Event | None = None,
    ) -> DiscoveredEndpoint | None:
        """After pairing, wait for tls-connect on same IP (port may differ)."""
        sink: dict[str, DiscoveredEndpoint] = {}
        zc = Zeroconf()
        browser = ServiceBrowser(zc, SERVICE_CONNECT, _CollectListener(EndpointKind.CONNECT, sink))
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return None
                for ep in list(sink.values()):
                    if ep.ip == ip:
                        return ep
                time.sleep(0.25)
        finally:
            try:
                browser.cancel()
            except Exception:  # noqa: BLE001
                pass
            zc.close()
        return None


def local_ipv4_hint() -> str:
    cands = local_ipv4_candidates()
    return cands[0] if cands else ""
