from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.adb.client import AdbClient
from app.paths import DEVICE_CACHE_DIR, ensure_data_dirs
from app.providers.http_util import DEFAULT_TIMEOUT

# Product photos are stable; refresh rarely.
PREVIEW_TTL_SEC = 30 * 24 * 3600
PLAY_CSV_TTL_SEC = 7 * 24 * 3600
PLAY_DEVICES_URL = "https://storage.googleapis.com/play_public/supported_devices.csv"
SPECS_SEARCH_URL = "https://mobile-specs-api-sandy.vercel.app/search"

_BRAND_COLORS = {
    "samsung": (0x14, 0x28, 0xA0),
    "xiaomi": (0xFF, 0x69, 0x00),
    "redmi": (0xFF, 0x69, 0x00),
    "poco": (0xFF, 0xD2, 0x00),
    "huawei": (0xCF, 0x0A, 0x2C),
    "honor": (0x1A, 0x1A, 0x1A),
    "google": (0x42, 0x85, 0xF4),
    "oneplus": (0xEB, 0x00, 0x24),
    "oppo": (0x00, 0x7A, 0xFF),
    "vivo": (0x41, 0x57, 0xFF),
    "realme": (0xFF, 0xC9, 0x15),
    "motorola": (0xE2, 0x1B, 0x21),
    "nokia": (0x12, 0x4C, 0xAA),
    "sony": (0x22, 0x22, 0x22),
    "asus": (0x00, 0x50, 0xA0),
    "tecno": (0x00, 0x8C, 0xFF),
    "infinix": (0x00, 0xB2, 0xA9),
    "nothing": (0x55, 0x55, 0x55),
}

_play_index_cache: dict[str, list[tuple[str, str]]] | None = None
_play_index_loaded_at = 0.0


@dataclass
class DeviceProfile:
    serial: str
    manufacturer: str = ""
    brand: str = ""
    model: str = ""
    market_name: str = ""
    device: str = ""
    android_version: str = ""
    sdk: str = ""
    preview_path: str = ""
    preview_source: str = ""  # product | brand

    @property
    def title(self) -> str:
        name = self.market_name or self.model or self.device or self.serial
        brand = self.brand or self.manufacturer
        if brand and name and brand.lower() not in name.lower():
            return f"{brand} {name}"
        return name

    @property
    def subtitle(self) -> str:
        parts = []
        if self.android_version:
            api = f" (API {self.sdk})" if self.sdk else ""
            parts.append(f"Android {self.android_version}{api}")
        if self.model and self.market_name and self.model != self.market_name:
            parts.append(self.model)
        return " · ".join(parts) if parts else self.serial


def _safe_serial(serial: str) -> str:
    return re.sub(r"[^\w.-]+", "_", serial)[:80]


def _safe_key(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text.strip().lower())[:80] or "unknown"


def matte_preview(
    img: Image.Image,
    *,
    pad: int = 6,
    border: tuple[int, int, int] = (0x2D, 0xD4, 0xBF),
    border_width: int = 2,
) -> Image.Image:
    """Transparent pad + accent outline so dark assets stay visible on dark UI."""
    src = img.convert("RGBA")
    w, h = src.size
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    canvas.paste(src, (pad, pad), src)
    draw = ImageDraw.Draw(canvas)
    x0, y0 = pad - 1, pad - 1
    x1, y1 = pad + w, pad + h
    for i in range(border_width):
        draw.rectangle(
            [x0 - i, y0 - i, x1 + i, y1 + i],
            outline=(*border, 255),
        )
    return canvas


def load_preview_for_ui(path: str | Path, size: tuple[int, int]) -> Image.Image:
    """Load preview with transparent pad + outline for dark-theme UI."""
    img = Image.open(path).convert("RGBA")
    corner = img.getpixel((0, 0))
    if not (isinstance(corner, tuple) and len(corner) == 4 and corner[3] < 32):
        img = matte_preview(img, pad=5)
    img.thumbnail(size, Image.Resampling.LANCZOS)
    return img


def _decode_play_csv(raw: bytes) -> str:
    for enc in ("utf-16", "utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _load_play_devices_index() -> dict[str, list[tuple[str, str]]]:
    """Map lowercase model -> [(brand, marketing_name), ...]."""
    global _play_index_cache, _play_index_loaded_at
    now = time.time()
    if _play_index_cache is not None and now - _play_index_loaded_at < PLAY_CSV_TTL_SEC:
        return _play_index_cache

    cache_path = DEVICE_CACHE_DIR / "supported_devices.csv"
    ensure_data_dirs()
    need_fetch = True
    if cache_path.is_file():
        age = now - cache_path.stat().st_mtime
        if age < PLAY_CSV_TTL_SEC and cache_path.stat().st_size > 1000:
            need_fetch = False

    if need_fetch:
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(PLAY_DEVICES_URL)
                resp.raise_for_status()
                cache_path.write_bytes(resp.content)
        except httpx.HTTPError:
            if not cache_path.is_file():
                _play_index_cache = {}
                _play_index_loaded_at = now
                return _play_index_cache

    text = _decode_play_csv(cache_path.read_bytes())
    index: dict[str, list[tuple[str, str]]] = {}
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        _play_index_cache = {}
        _play_index_loaded_at = now
        return _play_index_cache

    for row in reader:
        if len(row) < 4:
            continue
        brand, marketing, _device, model = row[0].strip(), row[1].strip(), row[2].strip(), row[3].strip()
        if not model or not marketing:
            continue
        key = model.lower()
        index.setdefault(key, []).append((brand, marketing))

    _play_index_cache = index
    _play_index_loaded_at = now
    return index


def resolve_marketing_name(*, brand: str, model: str, market_name: str = "") -> str:
    """Prefer getprop market name; else Google Play supported_devices.csv."""
    if market_name.strip():
        return market_name.strip()
    model_key = (model or "").strip().lower()
    if not model_key:
        return ""
    brand_l = (brand or "").strip().lower()
    entries = _load_play_devices_index().get(model_key, [])
    if not entries:
        return ""
    if brand_l:
        for b, name in entries:
            if brand_l in b.lower() or b.lower() in brand_l:
                return name
    return entries[0][1]


def _score_search_hit(name: str, query: str) -> int:
    n = name.lower().strip()
    q = query.lower().strip()
    if n == q:
        return 100
    if q in n:
        return 80
    q_tokens = [t for t in re.split(r"[\s()]+", q) if t]
    if q_tokens and all(t in n for t in q_tokens):
        return 60
    return 0


def _search_query_variants(query: str) -> list[str]:
    """GSMArena search is picky: full '… Pro' may miss while shorter query hits."""
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    # Drop trailing tier words once: Pro / Plus / Ultra / Lite / 5G
    stripped = re.sub(
        r"\s*\(?\s*(pro|plus|ultra|lite|5g)\s*\)?\s*$",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip(" -")
    if stripped and stripped.lower() != q.lower():
        variants.append(stripped)
    # Without parentheses: Nothing Phone (4a) Pro -> Nothing Phone 4a Pro
    no_parens = re.sub(r"[()]", " ", q)
    no_parens = re.sub(r"\s+", " ", no_parens).strip()
    if no_parens and no_parens.lower() not in {v.lower() for v in variants}:
        variants.append(no_parens)
    return variants


def _pick_best_image(data: list, target: str) -> str | None:
    best_url: str | None = None
    best_score = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("imageUrl") or item.get("thumbUrl") or "")
        if not url:
            continue
        score = _score_search_hit(name, target)
        if score > best_score:
            best_score = score
            best_url = url
    return best_url if best_score > 0 else None


def fetch_product_image_url(query: str) -> str | None:
    """Look up official product thumb via Mobile Specs (GSMArena) search."""
    target = (query or "").strip()
    if not target:
        return None
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            for q in _search_query_variants(target):
                try:
                    resp = client.get(SPECS_SEARCH_URL, params={"query": q})
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError):
                    continue
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or not data:
                    continue
                # Score against original marketing name, not the shortened query
                url = _pick_best_image(data, target)
                if url:
                    return url
                # If scoring failed but only one result — take it
                if len(data) == 1 and isinstance(data[0], dict):
                    one = str(data[0].get("imageUrl") or data[0].get("thumbUrl") or "")
                    if one:
                        return one
    except httpx.HTTPError:
        return None
    return None


def download_image(url: str) -> Image.Image | None:
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "APK-Updater/1.0"})
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except (httpx.HTTPError, OSError):
        return None


class DeviceProfileService:
    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb
        ensure_data_dirs()

    def fetch(self, serial: str, *, refresh_preview: bool = False) -> DeviceProfile:
        props = self._read_props(serial)
        brand = props.get("ro.product.brand", "")
        model = props.get("ro.product.model", "")
        market = (
            props.get("ro.product.marketname", "")
            or props.get("ro.oppo.market.name", "")
            or props.get("ro.config.marketing_name", "")
        )
        market = resolve_marketing_name(brand=brand, model=model, market_name=market)
        profile = DeviceProfile(
            serial=serial,
            manufacturer=props.get("ro.product.manufacturer", ""),
            brand=brand,
            model=model,
            market_name=market,
            device=props.get("ro.product.device", ""),
            android_version=props.get("ro.build.version.release", ""),
            sdk=props.get("ro.build.version.sdk", ""),
        )
        path, source = self._ensure_preview(serial, profile, force=refresh_preview)
        profile.preview_path = str(path)
        profile.preview_source = source
        return profile

    def _read_props(self, serial: str) -> dict[str, str]:
        keys = [
            "ro.product.manufacturer",
            "ro.product.brand",
            "ro.product.model",
            "ro.product.marketname",
            "ro.product.device",
            "ro.build.version.release",
            "ro.build.version.sdk",
            "ro.oppo.market.name",
            "ro.config.marketing_name",
        ]
        cmd = " && ".join(f"echo __P_{k}=$(getprop {k})" for k in keys)
        result = self.adb.shell(serial, cmd, check=False, timeout=20.0)
        props: dict[str, str] = {}
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("__P_"):
                continue
            body = line[4:]
            if "=" not in body:
                continue
            key, _, value = body.partition("=")
            props[key] = value.strip()
        return props

    def _preview_path(self, serial: str, profile: DeviceProfile) -> Path:
        # _v3: product photo / brand avatar (no screencap)
        key = _safe_key(profile.market_name or f"{profile.brand}_{profile.model}" or serial)
        return DEVICE_CACHE_DIR / f"{key}_v3.png"

    def _ensure_preview(
        self, serial: str, profile: DeviceProfile, *, force: bool
    ) -> tuple[Path, str]:
        path = self._preview_path(serial, profile)
        if not force and path.is_file():
            age = time.time() - path.stat().st_mtime
            if age < PREVIEW_TTL_SEC and path.stat().st_size > 100:
                # Heuristic: brand avatars are 160x160 solid; product photos taller
                try:
                    with Image.open(path) as im:
                        source = "product" if im.size != (160, 160) else "brand"
                except OSError:
                    source = "product"
                return path, source

        query = profile.market_name or " ".join(
            p for p in (profile.brand or profile.manufacturer, profile.model) if p
        ).strip()
        url = fetch_product_image_url(query) if query else None
        if url:
            img = download_image(url)
            if img is not None:
                img.thumbnail((200, 360), Image.Resampling.LANCZOS)
                img = matte_preview(img, pad=8)
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(path, format="PNG")
                return path, "product"

        return self._brand_avatar(profile, path), "brand"

    def _brand_avatar(self, profile: DeviceProfile, path: Path) -> Path:
        brand = (profile.brand or profile.manufacturer or "?").strip()
        key = brand.lower()
        color = (0x2D, 0xD4, 0xBF)
        for name, rgb in _BRAND_COLORS.items():
            if name in key:
                color = rgb
                break
        letter = (brand[:1] or "?").upper()
        img = Image.new("RGB", (160, 160), color)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 72)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((160 - tw) / 2, (160 - th) / 2 - 4), letter, fill=(255, 255, 255), font=font)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, format="PNG")
        return path
