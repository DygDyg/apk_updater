from __future__ import annotations

"""Canonical store ids, display titles, and settings keys."""

# Priority order when versionCode ties (first wins among equals via pick_best).
STORE_ORDER: tuple[str, ...] = ("rustore", "apkpure", "aptoide", "fdroid")

STORE_TITLES: dict[str, str] = {
    "rustore": "RuStore",
    "apkpure": "APKPure",
    "aptoide": "Aptoide",
    "fdroid": "F-Droid",
}

# Default: commercial-ish sources on; F-Droid on (harmless 404 for non-FOSS).
STORE_DEFAULT_ENABLED: dict[str, bool] = {
    "rustore": True,
    "apkpure": True,
    "aptoide": True,
    "fdroid": True,
}


def setting_key(store_id: str) -> str:
    return f"store_{store_id}_enabled"


def title(store_id: str) -> str:
    return STORE_TITLES.get(store_id, store_id)
