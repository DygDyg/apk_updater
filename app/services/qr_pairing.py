from __future__ import annotations

import io
import secrets
import string
import threading
from collections.abc import Callable
from dataclasses import dataclass

import qrcode
from PIL import Image

from app.adb.client import AdbClient, AdbError
from app.services.discovery import DiscoveryService, DiscoveredEndpoint


ProgressCb = Callable[[str], None]


def _random_token(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class QrPairingSession:
    service_name: str
    password: str
    payload: str

    @staticmethod
    def create() -> QrPairingSession:
        # Android QR pairing: WIFI:T:ADB;S:<name>;P:<password>;;
        name = f"apkupd{_random_token(6)}"
        password = _random_token(8)
        payload = f"WIFI:T:ADB;S:{name};P:{password};;"
        return QrPairingSession(service_name=name, password=password, payload=payload)

    def make_image(self, box_size: int = 8, border: int = 2) -> Image.Image:
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
        qr.add_data(self.payload)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")

    def png_bytes(self) -> bytes:
        img = self.make_image()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


@dataclass
class QrPairingResult:
    success: bool
    message: str
    paired_endpoint: DiscoveredEndpoint | None = None
    connected_host_port: str | None = None


class QrPairingService:
    def __init__(self, adb: AdbClient, discovery: DiscoveryService | None = None) -> None:
        self.adb = adb
        self.discovery = discovery or DiscoveryService()

    def run_session(
        self,
        session: QrPairingSession,
        *,
        timeout: float = 90.0,
        cancel_event: threading.Event | None = None,
        progress: ProgressCb | None = None,
    ) -> QrPairingResult:
        def report(msg: str) -> None:
            if progress:
                progress(msg)

        report("Listening for pairing advertisement…")
        endpoint = self.discovery.wait_for_pairing_named(
            session.service_name,
            timeout=timeout,
            cancel_event=cancel_event,
        )
        if cancel_event is not None and cancel_event.is_set():
            return QrPairingResult(success=False, message="Cancelled")
        if endpoint is None:
            return QrPairingResult(
                success=False,
                message="Timed out waiting for phone QR scan. Open Wireless debugging → Pair with QR code.",
            )

        report(f"Pairing {endpoint.host_port}…")
        pair_result = self.adb.pair(endpoint.host_port, session.password)
        text = pair_result.combined
        if not pair_result.ok and "Successfully paired" not in text:
            return QrPairingResult(
                success=False,
                message=text or "adb pair failed",
                paired_endpoint=endpoint,
            )

        report("Looking for connect service…")
        connect_ep = self.discovery.wait_for_connect_near(
            endpoint.ip,
            timeout=20.0,
            cancel_event=cancel_event,
        )
        host_port = connect_ep.host_port if connect_ep else endpoint.host_port
        # Prefer discovered connect port; fallback: try connect on same IP using mdns later
        if connect_ep is None:
            # Some devices reuse discovery shortly after pair — try adb connect via instance name pattern
            report("Connect service not seen yet; trying paired host…")
            # After successful pair, wireless debugging connect port is usually different;
            # scan briefly once more
            found = self.discovery.scan(duration=3.0)
            for ep in found:
                if ep.ip == endpoint.ip and ep.kind.value == "connect":
                    host_port = ep.host_port
                    break

        report(f"Connecting {host_port}…")
        conn = self.adb.connect(host_port)
        conn_text = conn.combined
        if not conn.ok and "connected" not in conn_text.lower():
            return QrPairingResult(
                success=False,
                message=f"Paired, but connect failed: {conn_text or 'unknown error'}",
                paired_endpoint=endpoint,
                connected_host_port=host_port,
            )

        return QrPairingResult(
            success=True,
            message=conn_text or f"connected to {host_port}",
            paired_endpoint=endpoint,
            connected_host_port=host_port,
        )
