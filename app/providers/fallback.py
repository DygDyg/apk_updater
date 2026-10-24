from __future__ import annotations

# Backward-compatible alias — fallback store is APKPure.
from .apkpure import ApkPureProvider as FallbackProvider

__all__ = ["FallbackProvider"]
