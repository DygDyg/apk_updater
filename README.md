# APK Updater

Локальное веб-приложение (FastAPI + React) для сопряжения Android по **Wi‑Fi ADB**, проверки обновлений (RuStore / APKPure / Aptoide / F-Droid) и установки APK.

Слушает `0.0.0.0:8765`: на этом ПК — `http://127.0.0.1:8765`, с других устройств в LAN — `http://<IP-ПК>:8765`. После запуска живёт **иконкой в системном трее**: «Открыть» / «Выход».

## Для пользователей (без Python)

1. Скачайте последний релиз: [Releases](https://github.com/DygDyg/apk_updater/releases/latest) → файл `APKUpdater-windows-x64.zip`.
2. Распакуйте в любую папку.
3. Запустите `APKUpdater.exe`.

Python и Node.js **не нужны**. ADB при первом запуске может скачаться в `data\tools\`. Данные и настройки хранятся в папке `data\` рядом с EXE (при обновлении приложения не удаляются).

Windows SmartScreen может предупреждать о неподписанном EXE — это ожидаемо, пока нет Authenticode.

В приложении: баннер о новой сборке и в **Настройках** — проверка обновлений раз в час и опциональное автообновление.

## Требования (разработка из исходников)

- Windows 10/11
- Python 3.11+
- Node.js 18+ (сборка фронта)
- `adb`: PATH или автозагрузка в `data/tools/platform-tools/`
- На телефоне: Android 11+, **Беспроводная отладка**

## Установка из исходников

```bat
setup.bat
run_apk_updater.bat
```

`setup.bat` создаёт `.venv`, ставит пакеты из `requirements.txt`, при необходимости собирает `web\dist`.

Флаги: `--no-browser` — не открывать вкладку при старте; `--no-tray` — только консоль; `--console` — окно консоли вместе с треем.

## Сопряжение

1. **Параметры → Для разработчиков → Беспроводная отладка**.
2. Вкладка **Приложения** → сайдбар устройств → **Скан сети** или сопряжение по QR.
3. Дубли IP / mDNS сливаются в одно логическое устройство.

## Данные

- `data/apk_updater.db` — устройства, настройки, история
- `data/cache/` — APK, иконки, превью моделей
- `data/logs/updates.log` — журнал фоновых проверок/установок

## Сборка EXE и публикация релиза

Идентификатор сборки — дата/время `YYYYMMDD-HHMMSS` (не semver).

```bat
build.bat
```

Собирает `dist\APKUpdater\APKUpdater.exe` (нужны Python + Node).

Публикация на GitHub одной командой (нужны `git`, `gh auth login`, Node, Python):

```bat
publish_release.bat
```

Скрипт: пишет stamp в `app/build_info.py` → commit/tag/push → локальный билд → ZIP → `gh release create` с ассетом `APKUpdater-windows-x64.zip`.

LAN-деплой исходников на сервер: `deploy_release.bat` (источник ≠ `Z:\`).

## Для AI / разработчика

| Файл | Зачем |
|------|--------|
| [AGENTS.md](AGENTS.md) | Карта проекта для Cursor Agent |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Потоки, модули, БД |
| [docs/API-MAP.md](docs/API-MAP.md) | Таблица `/api/...` |
| [docs/DATA-AND-OPS.md](docs/DATA-AND-OPS.md) | `data/`, bat, деплой, MCP |
| `.cursor/rules/` | Правила агента |
| `.cursor/skills/` | Скиллы `apk-updater-dev`, `fastapi` |

**MCP:** для живого Android достаточно **user-adb**. Рекомендуется **Context7** для доков FastAPI/React.
