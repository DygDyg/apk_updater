from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassifiedInstallError:
    code: str
    title: str
    hint: str
    actions: tuple[str, ...]
    friendly: str


_DOWNGRADE = re.compile(
    r"INSTALL_FAILED_VERSION_DOWNGRADE.*?(\d+).*?(\d+)",
    re.I | re.S,
)
_DOWNGRADE_ALT = re.compile(
    r"Update version code\s+(\d+)\s+is older than current\s+(\d+)",
    re.I,
)
_INCOMPATIBLE = re.compile(r"INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures?\s+do\s+not\s+match", re.I)
_INSUFFICIENT = re.compile(r"INSTALL_FAILED_INSUFFICIENT_STORAGE", re.I)
_OLDER_SDK = re.compile(r"INSTALL_FAILED_OLDER_SDK", re.I)
_NO_MATCHING_ABIS = re.compile(r"INSTALL_FAILED_NO_MATCHING_ABIS", re.I)
_USER_RESTRICTED = re.compile(r"INSTALL_FAILED_USER_RESTRICTED", re.I)
_INVALID_APK = re.compile(r"INSTALL_PARSE_FAILED|INSTALL_FAILED_INVALID_APK", re.I)
_ALREADY_EXISTS = re.compile(r"INSTALL_FAILED_ALREADY_EXISTS", re.I)
_ABORTED = re.compile(r"INSTALL_FAILED_ABORTED|INSTALL_CANCELED", re.I)
_GENERIC_FAIL = re.compile(r"INSTALL_FAILED_([A-Z0-9_]+)", re.I)
_SDK_PAIR = re.compile(
    r"(?:minSdk|min_sdk)\s*=\s*(\d+).*?(?:deviceSdk|device_sdk|sdk)\s*=\s*(\d+)",
    re.I | re.S,
)


def older_sdk_error(
    *,
    min_sdk: int | None = None,
    device_sdk: int | None = None,
    raw: str = "",
) -> ClassifiedInstallError:
    """Friendly explanation when APK minSdk is above the device API level."""
    if min_sdk is None or device_sdk is None:
        m = _SDK_PAIR.search(raw or "")
        if m:
            min_sdk = int(m.group(1))
            device_sdk = int(m.group(2))
    if min_sdk is not None and device_sdk is not None:
        friendly = f"APK требует API {min_sdk}, на устройстве API {device_sdk}"
        hint = (
            f"В манифесте APK указан minSdk={min_sdk}, а устройство на API {device_sdk}. "
            "Нужна более новая версия Android на планшете/телефоне, либо более старый APK "
            "с меньшим minSdk (другая версия в сторе)."
        )
    elif min_sdk is not None:
        friendly = f"APK требует API {min_sdk}+"
        hint = (
            f"Этот APK требует Android API {min_sdk} или новее, чем установлено на устройстве."
        )
    else:
        friendly = "APK требует более новый Android"
        hint = "Этот APK требует более новую версию Android, чем на устройстве."
    return ClassifiedInstallError(
        code="older_sdk",
        title="Слишком старый Android",
        hint=hint,
        actions=(),
        friendly=friendly,
    )


def classify_install_error(raw: str) -> ClassifiedInstallError | None:
    """Map adb install failure text to a short RU explanation + suggested actions."""
    text = (raw or "").strip()
    if not text:
        return None
    low = text.lower()
    if "success" in low and "failure" not in low and "failed" not in low:
        return None

    m = _DOWNGRADE_ALT.search(text) or _DOWNGRADE.search(text)
    if m or "version_downgrade" in low:
        new_c = m.group(1) if m else "?"
        old_c = m.group(2) if m else "?"
        return ClassifiedInstallError(
            code="version_downgrade",
            title="Нужен даунгрейд",
            hint=(
                f"На устройстве уже стоит более новая версия (code {old_c}), "
                f"а выбранный APK старше (code {new_c}). "
                "Обычная установка это запрещает. Можно поставить с даунгрейдом "
                "(данные обычно сохраняются) или выбрать более новую версию в сторе."
            ),
            actions=("downgrade",),
            friendly=f"Версия на устройстве новее (code {old_c} → ставите {new_c})",
        )

    if _INCOMPATIBLE.search(text):
        return ClassifiedInstallError(
            code="signature_mismatch",
            title="Конфликт подписи",
            hint=(
                "APK подписан другим ключом, чем установленное приложение "
                "(часто другой стор). Обновление поверх невозможно — нужна "
                "переустановка: приложение удалится вместе с данными, затем "
                "поставится новый APK."
            ),
            actions=("reinstall",),
            friendly="Подпись не совпадает с установленным приложением",
        )

    if _INSUFFICIENT.search(text):
        return ClassifiedInstallError(
            code="insufficient_storage",
            title="Мало места",
            hint="На устройстве недостаточно свободного места для установки APK.",
            actions=(),
            friendly="Недостаточно места на устройстве",
        )

    if _OLDER_SDK.search(text):
        return older_sdk_error(raw=text)

    if _NO_MATCHING_ABIS.search(text):
        return ClassifiedInstallError(
            code="no_matching_abis",
            title="Неподходящая архитектура",
            hint="В APK нет нативных библиотек под процессор этого устройства (ABI).",
            actions=(),
            friendly="APK не подходит под архитектуру устройства",
        )

    if _USER_RESTRICTED.search(text):
        return ClassifiedInstallError(
            code="user_restricted",
            title="Установка запрещена",
            hint="На устройстве запрещена установка из неизвестных источников или через ADB.",
            actions=(),
            friendly="Установка запрещена настройками устройства",
        )

    if _INVALID_APK.search(text):
        return ClassifiedInstallError(
            code="invalid_apk",
            title="Повреждённый APK",
            hint="Файл APK битый или это не обычный APK (например XAPK/bundle без распаковки).",
            actions=(),
            friendly="Некорректный или повреждённый APK",
        )

    if _ALREADY_EXISTS.search(text):
        return ClassifiedInstallError(
            code="already_exists",
            title="Уже установлено",
            hint="Пакет уже есть на устройстве. Попробуйте установку с заменой или удалите приложение.",
            actions=(),
            friendly="Пакет уже установлен",
        )

    if _ABORTED.search(text):
        return ClassifiedInstallError(
            code="aborted",
            title="Установка прервана",
            hint="Установка отменена на устройстве или оборвалась соединение ADB.",
            actions=(),
            friendly="Установка прервана",
        )

    m_gen = _GENERIC_FAIL.search(text)
    if m_gen:
        code = m_gen.group(1).upper()
        return ClassifiedInstallError(
            code=f"install_failed_{code.lower()}",
            title="Ошибка установки",
            hint=f"ADB вернул {m_gen.group(0)}. Полный текст ниже — если повторяется, пришлите его.",
            actions=(),
            friendly=m_gen.group(0).replace("_", " "),
        )

    if "failed to install" in low or "failure [" in low or "install failed" in low:
        return ClassifiedInstallError(
            code="install_failed",
            title="Ошибка установки",
            hint="Установка через ADB не удалась. См. подробности ниже.",
            actions=(),
            friendly="Установка не удалась",
        )
    return None


def enrich_install_message(raw: str) -> tuple[str, ClassifiedInstallError | None]:
    """Return (display_message, classification). Display prefers friendly + short raw tag."""
    classified = classify_install_error(raw)
    if classified is None:
        return raw, None
    # Keep a short raw snippet for debugging
    snippet = re.sub(r"\s+", " ", (raw or "").strip())
    if len(snippet) > 160:
        snippet = snippet[:157] + "…"
    display = f"{classified.friendly}\n{snippet}" if snippet else classified.friendly
    return display, classified
