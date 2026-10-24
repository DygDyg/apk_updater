# Карта API

Роутеры: `app/api/routers/`. Теги совпадают с именами файлов.

## status / adb / app-update

| Method | Path | Router |
|--------|------|--------|
| GET | `/api/status` | status.py |
| GET | `/api/restart` | status.py |
| POST | `/api/adb/path` | status.py |
| POST | `/api/adb/ensure` | status.py |
| GET | `/api/app-update/status` | app_update.py |
| POST | `/api/app-update/check` | app_update.py |
| POST | `/api/app-update/apply` | app_update.py |
| POST | `/api/app-update/dismiss` | app_update.py |

## devices

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/devices` | Список |
| POST | `/api/devices/refresh` | Обновить реестр |
| POST | `/api/devices/discover` | Скан сети |
| POST | `/api/devices/pair` | Сопряжение |
| POST | `/api/devices/connect` | Connect |
| POST | `/api/devices/disconnect` | Disconnect |
| POST | `/api/devices/remove` | Удалить из реестра |
| POST | `/api/devices/discovered/action` | Действие по найденному endpoint |
| POST | `/api/devices/profile` | Обновить профиль |
| GET | `/api/devices/info` | Live info + свойства |
| POST | `/api/devices/action` | wake / play_timer / play_alarm / mute… |
| POST | `/api/devices/screenshot` | Скрин в кеш |
| POST | `/api/devices/scrcpy` | Запуск scrcpy |

## apps

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/apps` | Список пакетов (+ update fields) |
| PUT | `/api/apps/update-ignore` | Не обновлять / снять |
| PUT | `/api/apps/source` | Предпочтительный стор |
| GET | `/api/apps/details` | Детали + permissions |
| POST | `/api/apps/action` | force_stop / clear_* / uninstall… |
| POST | `/api/apps/permission` | grant/revoke |

## updates (часто SSE)

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/updates/cache` | Снимок кеша + counts |
| POST | `/api/updates/check-batch` | Проверка (все или serials) |
| POST | `/api/updates/check` | Одно устройство |
| POST | `/api/updates/install-all` | Установить всё (serials optional) |
| POST | `/api/updates/install` | Одно приложение |
| POST | `/api/updates/reinstall` | uninstall+install (подпись) |

## store / install / media / qr / settings

| Method | Path | Router |
|--------|------|--------|
| GET | `/api/store/search` | store.py |
| GET | `/api/store/app` | store.py |
| GET | `/api/store/versions` | store.py |
| POST | `/api/store/install` | store.py (SSE) |
| POST | `/api/install` | install.py (multipart APK, SSE) |
| POST | `/api/install/cached` | install.py |
| GET | `/api/media/file` | media.py |
| POST | `/api/qr/start` | qr.py |
| POST | `/api/qr/cancel` | qr.py |
| GET | `/api/qr/wait` | qr.py (SSE) |
| GET/PUT | `/api/settings` | settings.py |
| POST | `/api/settings/reset-bg` | settings.py |
| GET/DELETE | `/api/settings/activity-log` | settings.py |
| POST | `/api/settings/clear-cache` | settings.py |
| POST | `/api/settings/unlock-job` | settings.py |

Схемы тел/ответов: `app/api/schemas.py`.
