# Данные и операции

## Каталог `data/`

| Путь | Содержимое |
|------|------------|
| `data/apk_updater.db` | SQLite (устройства, настройки, кеши UI, ignores…) |
| `data/cache/apk/` | Скачанные APK |
| `data/cache/icons/` | Иконки приложений |
| `data/cache/devices/` | Превью устройств |
| `data/cache/screencaps/` | Скриншоты из модалки |
| `data/logs/updates.log` | Ротируемый лог проверок/установок фона |
| `data/tools/platform-tools/` | Локальный adb (если скачан) |
| `data/tools/scrcpy/` | scrcpy bootstrap |

Пути задаются в `app/paths.py` (`DATA_DIR`, `BUNDLE_DIR` для PyInstaller).

## Скрипты Windows

| Файл | Назначение |
|------|------------|
| `setup.bat` | venv + pip + при необходимости `web` build |
| `run_apk_updater.bat` | Запуск приложения |
| `stop.bat` | Остановка |
| `build.bat` | `npm run build` + embed schema + PyInstaller (`APKUpdater.spec`) |
| `publish_release.bat` | Stamp → commit всех правок → tag → билд → GitHub Release ZIP; notes = коммиты/файлы с прошлого `build-*` (`tools/commit_release_changes.ps1`, `tools/write_release_notes.ps1`) |
| `deploy_release.bat` | Копирование релиза; **источник ≠ назначение** |

`.bat`: только ASCII в теле скрипта, CRLF.

## Portable-сборка (PyInstaller datas)

В `_internal` (рядом с EXE) **обязательно** должны лежать:

| Путь в бандле | Источник | Зачем |
|---------------|----------|--------|
| `web/dist/**` | `npm run build` | UI |
| `app/resources/play_alarm.dex` | `tools/play_alarm` | звук alarm на устройстве |
| `app/db/schema.sql` | `app/db/schema.sql` | DDL при старте БД |

Дополнительно: `app/db/_schema_embed.py` (генерирует `build.bat`) — запасной вариант, если `schema.sql` не попал в datas. `certifi/cacert.pem` подтягивает PyInstaller сам (HTTPS).

`adb` / `scrcpy` в ZIP **не** кладутся — скачиваются в `data/tools/` при первом использовании.

`build.bat` проверяет эти файлы до и после упаковки.

## Dev-фронт

```bat
cd web
npm install
npm run build
```

Сервер отдаёт уже собранный `web/dist` (не обязателен `vite dev` для обычного запуска).

## Фоновые обновления

Настройки в UI → settings API. Интервалы / TTL / auto-install пишутся в `settings` и крутятся в `background.py`. События:

- файл `data/logs/updates.log` (`update_file_log.py`)
- таблица `bg_activity_log` (модалка журнала)

## MCP / AI (кратко)

- Уже полезен **user-adb** для живого устройства.
- Рекомендуется **Context7** для доков FastAPI/React — пример: [`.cursor/mcp.json.example`](../.cursor/mcp.json.example).
- Не ставить второй Android-MCP и fastapi_mcp «на все endpoint».

См. также [AGENTS.md](../AGENTS.md).
