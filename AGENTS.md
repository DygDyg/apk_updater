# AGENTS — apk_updater

Краткий вход для Cursor Agent. Детали: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/API-MAP.md](docs/API-MAP.md), [docs/DATA-AND-OPS.md](docs/DATA-AND-OPS.md).

## Стек

- Backend: FastAPI (`app/`), SQLite (`data/apk_updater.db`)
- Frontend: React + Vite (`web/`), сборка → `web/dist`
- Android: Wi‑Fi ADB (`app/adb/client.py`), порт UI `8765`, tray (`app/tray.py`)

## Куда смотреть

| Задача | Пути |
|--------|------|
| HTTP API | `app/api/routers/*.py`, схемы `app/api/schemas.py` |
| Бизнес-логика | `app/services/*` |
| ADB / звук / скрин | `app/adb/client.py`, `app/services/device_actions.py` |
| Обновления / batch | `app/services/updates.py`, `batch_jobs.py`, `background.py` |
| UI | `web/src/App.tsx`, клиент `web/src/api.ts`, стили `web/src/styles.css` |
| Схема БД | `app/db/schema.sql` |
| Лог обновлений | `data/logs/updates.log` |

## Конвенции

- Ответы пользователю и UI-тексты — **на русском**.
- Длинные операции — **SSE** через `app/api/sse_job.py`.
- Устройство online: `devices.prepare_online` / `AdbClient.prepare_for_work`.
- Игнор обновлений: таблица `update_ignores`, API `PUT /api/apps/update-ignore`.
- Звук устройства: `play_timer` / `play_alarm` — **без** автоподъёма громкости.
- `.bat`: только ASCII, CRLF (см. user rule).

## Не делать

- Не читать целиком огромный `web/src/App.tsx` — Grep / фрагменты.
- Не ставить второй Android-MCP рядом с уже подключённым `user-adb`.
- `deploy_release.bat`: не копировать релиз поверх самого себя (источник ≠ Z: назначение).

## Запуск (dev)

```bat
setup.bat
run_apk_updater.bat
```

UI после правок фронта: `cd web && npm run build`.

## Релиз (портатив)

- Сборка: `BUILD_ID` в `app/build_info.py` (`YYYYMMDD-HHMMSS` или `dev`).
- Публикация: `publish_release.bat` → GitHub Release `APKUpdater-windows-x64.zip`.
- Автообновление EXE: `app/services/self_update.py`, API `/api/app-update/*`.
