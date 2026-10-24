from __future__ import annotations

import re

# Segment tokens that mark a pre-release / beta / nightly build.
_PRERELEASE_TOKENS = frozenset(
    {
        "alpha",
        "beta",
        "rc",
        "preview",
        "snapshot",
        "nightly",
        "canary",
        "dev",
        "pre",
        "draft",
        "test",
        "unstable",
        "experimental",
        "debug",
        "ea",  # early access
    }
)

# alpha1, beta2, rc3, a1, b2, pre1 — without a separator before the digits
_PRERELEASE_SEG = re.compile(
    r"^(?:alpha|beta|rc|pre|a|b)(?:\d+)?$",
    re.IGNORECASE,
)

# Embedded markers when glued to numbers: 1.0.0rc1, 2.0beta
_PRERELEASE_EMBEDDED = re.compile(
    r"(?i)(?:^|[^a-z])(?:alpha|beta|rc|preview|snapshot|nightly|canary|dev|pre)(?:[^a-z]|$|\d)",
)


def is_prerelease_version(version_name: str) -> bool:
    """True if versionName looks like beta/dev/rc rather than a stable release."""
    name = (version_name or "").strip()
    if not name:
        return False
    lower = name.lower()
    for part in re.split(r"[.\-_+]", lower):
        if not part:
            continue
        if part in _PRERELEASE_TOKENS:
            return True
        if _PRERELEASE_SEG.fullmatch(part):
            return True
    return bool(_PRERELEASE_EMBEDDED.search(lower))
