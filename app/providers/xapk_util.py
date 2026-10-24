"""Extract APKPure/APKMirror-style XAPK (ZIP) into installable APK set."""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedXapk:
    package_name: str
    version_name: str
    version_code: int
    apks: tuple[Path, ...]
    obb_files: tuple[Path, ...]


def url_looks_like_xapk(url: str) -> bool:
    u = (url or "").lower()
    return "/xapk/" in u or u.endswith(".xapk") or ".xapk?" in u or ".xapk&" in u


def is_xapk_archive(path: Path) -> bool:
    """True if path is a ZIP that wraps one or more .apk members (XAPK/APKS)."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            nested = [
                n
                for n in zf.namelist()
                if not n.endswith("/") and n.lower().endswith(".apk")
            ]
            if nested:
                return True
            names_l = {n.lower().rsplit("/", 1)[-1] for n in zf.namelist()}
            return "manifest.json" in names_l and any(
                n.endswith(".obb") for n in names_l
            )
    except (OSError, zipfile.BadZipFile):
        return False


def artifact_from_extracted(
    extracted: ExtractedXapk,
    *,
    package_name: str,
    version_name: str,
    version_code: int,
    source: str,
    download_url: str,
    single_apk_dest: Path | None = None,
) -> "ApkArtifact":
    """Build ApkArtifact; optionally copy lone APK to cache path."""
    from .base import ApkArtifact

    if not extracted.apks:
        raise RuntimeError("XAPK не содержит APK")
    base = extracted.apks[0]
    if len(extracted.apks) == 1 and not extracted.obb_files and single_apk_dest is not None:
        shutil.copy2(base, single_apk_dest)
        return ApkArtifact(
            package_name=package_name,
            version_name=version_name or extracted.version_name,
            version_code=version_code or extracted.version_code,
            source=source,
            file_path=single_apk_dest,
            download_url=download_url,
        )
    return ApkArtifact(
        package_name=package_name,
        version_name=version_name or extracted.version_name,
        version_code=version_code or extracted.version_code,
        source=source,
        file_path=base,
        download_url=download_url,
        split_apks=extracted.apks[1:],
        obb_files=extracted.obb_files,
    )


def _order_apks(paths: list[Path], package_name: str) -> list[Path]:
    def rank(p: Path) -> tuple[int, str]:
        name = p.name.lower()
        stem = p.stem.lower()
        if stem == package_name.lower() or name == f"{package_name.lower()}.apk":
            return (0, name)
        if stem == "base" or name == "base.apk":
            return (1, name)
        if name.startswith("config."):
            return (3, name)
        return (2, name)

    return sorted(paths, key=rank)


def extract_xapk(
    archive: Path,
    dest_dir: Path,
    *,
    expected_package: str = "",
) -> ExtractedXapk:
    """Unzip XAPK into dest_dir and return ordered APK/OBB paths."""
    if not archive.is_file():
        raise RuntimeError(f"XAPK not found: {archive}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Fresh extract
    for child in dest_dir.iterdir():
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child, ignore_errors=True)

    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(dest_dir)

    package_name = (expected_package or "").strip()
    version_name = ""
    version_code = 0
    manifest = dest_dir / "manifest.json"
    split_names: list[str] = []
    if manifest.is_file():
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            meta = {}
        if isinstance(meta, dict):
            package_name = str(meta.get("package_name") or package_name)
            version_name = str(meta.get("version_name") or "")
            try:
                version_code = int(meta.get("version_code") or 0)
            except (TypeError, ValueError):
                version_code = 0
            for entry in meta.get("split_apks") or []:
                if isinstance(entry, dict) and entry.get("file"):
                    split_names.append(str(entry["file"]))
            for entry in meta.get("expansions") or []:
                # expansions listed for OBB; files still picked by suffix below
                _ = entry

    apk_paths = [p for p in dest_dir.rglob("*.apk") if p.is_file()]
    if not apk_paths and split_names:
        apk_paths = [dest_dir / n for n in split_names if (dest_dir / n).is_file()]
    if not apk_paths:
        raise RuntimeError("XAPK не содержит APK")

    ordered = _order_apks(apk_paths, package_name or expected_package)
    obb_files = tuple(sorted(p for p in dest_dir.rglob("*.obb") if p.is_file()))
    if not package_name:
        # Infer from largest / first apk name
        package_name = ordered[0].stem
    return ExtractedXapk(
        package_name=package_name,
        version_name=version_name,
        version_code=version_code,
        apks=tuple(ordered),
        obb_files=obb_files,
    )


def load_extracted_splits(splits_dir: Path, package_name: str = "") -> tuple[Path, ...]:
    """Reload APKs from a previous extract directory (base first)."""
    if not splits_dir.is_dir():
        return ()
    apks = [p for p in splits_dir.rglob("*.apk") if p.is_file()]
    if not apks:
        return ()
    return tuple(_order_apks(apks, package_name))
