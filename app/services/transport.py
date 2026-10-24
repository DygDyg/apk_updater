from __future__ import annotations

import re

_IP_PORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d+)$")
# adb-<hardwareSerial>-<random> (Wireless debugging mDNS)
_MDNS_HW_SERIAL = re.compile(r"^adb-([^-]+)-", re.I)
_MDNS_SUFFIXES = (
    "._adb-tls-connect._tcp",
    "._adb-tls-pairing._tcp",
    "._adb._tcp",
)


def transport_family(serial: str) -> str:
    """Normalize ADB serial so short mdns name and full service name match."""
    s = (serial or "").strip().replace(" ", "")
    # Drop accidental "(2)" from broken adb devices -l wraps
    s = re.sub(r"\(\d+\)(?=\._adb)", "", s)
    s = re.sub(r"\(\d+\)$", "", s)
    lower = s.lower()
    for suf in _MDNS_SUFFIXES:
        if lower.endswith(suf):
            s = s[: -len(suf)]
            break
    # Trailing (N) on short names: adb-xxx(2)
    s = re.sub(r"\(\d+\)$", "", s)
    return s.lower()


def mdns_hardware_serial(serial: str) -> str:
    """Hardware serial embedded in ``adb-<serial>-xxxx`` mDNS names (empty if N/A)."""
    fam = transport_family(serial)
    m = _MDNS_HW_SERIAL.match(fam)
    return (m.group(1) or "").strip() if m else ""


def uid_from_mdns(serial: str) -> str:
    hw = mdns_hardware_serial(serial)
    return f"sn:{hw}" if hw else ""


def extract_ip(serial: str) -> str | None:
    m = _IP_PORT.match((serial or "").strip())
    return m.group(1) if m else None


def is_ip_transport(serial: str) -> bool:
    return extract_ip(serial) is not None


def is_mdns_transport(serial: str) -> bool:
    s = (serial or "").strip().lower()
    return s.startswith("adb-") or any(suf in s for suf in _MDNS_SUFFIXES)


def is_usb_transport(serial: str) -> bool:
    s = (serial or "").strip()
    return bool(s) and ":" not in s and not is_mdns_transport(s)


def transport_kind_rank(serial: str) -> int:
    """Lower is better: USB → IP:port → mDNS → other."""
    if is_usb_transport(serial):
        return 0
    if is_ip_transport(serial):
        return 1
    if is_mdns_transport(serial):
        return 2
    return 3


def transport_sort_key(serial: str, *, online: bool) -> tuple:
    return (0 if online else 1, transport_kind_rank(serial), serial.lower())


def short_transport_label(serial: str) -> str:
    if is_ip_transport(serial):
        return serial
    if is_usb_transport(serial):
        return "USB"
    if is_mdns_transport(serial):
        fam = transport_family(serial)
        return f"mDNS ({fam})" if fam else "mDNS"
    return serial
