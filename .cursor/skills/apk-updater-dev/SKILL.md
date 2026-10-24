---
name: apk-updater-dev
description: >-
  Workflows for apk_updater (Wi-Fi ADB APK updates). Use when changing updates,
  devices, install, ignore flags, device actions/sound, deploy, or project docs.
---

# apk_updater — dev workflow

## Before coding

1. Read [AGENTS.md](../../../AGENTS.md) if not already in context.
2. For architecture: [docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md).
3. For endpoints: [docs/API-MAP.md](../../../docs/API-MAP.md).

## Feature checklist

1. **DB** — if needed, add table/columns to `app/db/schema.sql` (`CREATE IF NOT EXISTS` / migrate helpers).
2. **Service** — `app/services/…` (keep routers thin).
3. **API** — router + `schemas.py`; apps lists go through `serialize.app_out`.
4. **Frontend** — `web/src/api.ts` then minimal UI in `App.tsx` (Grep, don't load whole file).
5. **Build UI** — `cd web && npm run build`.
6. **Ops** — if release path changes, check `deploy_release.bat` (SRC ≠ DST).

## Pitfalls

- **Update TTL** — scheduled checks may need `force=True` or they look "cached forever".
- **update_ignored** — suppresses `has_update` and install-all; toggle in app details modal.
- **Secure lock** — SET_TIMER often silent until unlock; use `play_alarm` (USAGE_ALARM dex).
- **No auto volume** on timer/alarm actions.
- **MiTV / old API** — screencap may need pull; prefer media paths for TV sound.
- **App.tsx size** — always Grep/`Read` with offset.

## Quick paths

| Change | Touch |
|--------|--------|
| New device action | `device_actions.py`, `AdbClient`, `DeviceActionName` in `api.ts`, `DEVICE_ACTIONS_*` |
| Ignore updates | `update_ignores.py`, apps router, `app_out` |
| Store source | `package_sources.py` |
| Background schedule | `background.py`, settings schema/UI |
| Alarm beep dex | `tools/play_alarm/`, `app/resources/play_alarm.dex`, `build.ps1` |
