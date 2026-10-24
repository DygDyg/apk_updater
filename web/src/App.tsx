import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppDetails,
  AppRow,
  AppSettings,
  Device,
  DeviceActionName,
  DeviceInfo,
  Discover,
  Status,
  StoreAppCard,
  StoreHit,
  StoreSearchResult,
  StoreVersion,
  UpdatesCache,
  BgActivityEntry,
  appAction,
  applyAppUpdate,
  checkAppUpdate,
  checkUpdatesBatch,
  clearActivityLog,
  clearFileCache,
  connectDevice,
  dismissAppUpdate,
  unlockJob,
  deviceAction,
  deviceScreenshot,
  disconnectDevice,
  discoverNetwork,
  discoveredAction,
  ensureAdb,
  fetchDeviceInfo,
  getActivityLog,
  getAppDetails,
  getSettings,
  getStatus,
  getUpdatesCache,
  installAllUpdates,
  installApk,
  installCachedApk,
  installUpdate,
  listDevices,
  pairDevice,
  qrCancel,
  qrStart,
  qrWait,
  refreshDevices,
  reinstallUpdate,
  removeDevice,
  saveSettings,
  setAdbPath,
  setAppPermission,
  setAppSource,
  setUpdateIgnore,
  storeApp,
  storeInstall,
  storeSearch,
  storeVersions,
} from "./api";

const DEVICE_ACTIONS_MOBILE: { action: DeviceActionName; label: string }[] = [
  { action: "wake", label: "Разбудить" },
  { action: "play_timer", label: "Таймер" },
  { action: "play_alarm", label: "Alarm" },
  { action: "mute", label: "Беззвучный" },
  { action: "unmute", label: "Звук вкл." },
  { action: "volume_up", label: "Громче" },
  { action: "volume_down", label: "Тише" },
  { action: "stay_awake_on", label: "Не гасить экран" },
  { action: "stay_awake_off", label: "Обычный сон" },
];

const DEVICE_ACTIONS_TV: { action: DeviceActionName; label: string }[] = [
  { action: "wake", label: "Разбудить ТВ" },
  { action: "play_alarm", label: "Alarm / media" },
  { action: "mute", label: "Без звука" },
  { action: "unmute", label: "Звук вкл." },
  { action: "volume_up", label: "Громче" },
  { action: "volume_down", label: "Тише" },
  { action: "stay_awake_on", label: "Не гасить экран" },
  { action: "stay_awake_off", label: "Обычный сон" },
];

const STORE_OPTIONS: { id: string; label: string; settingKey: keyof AppSettings | null }[] = [
  { id: "", label: "Авто", settingKey: null },
  { id: "rustore", label: "RuStore", settingKey: "store_rustore_enabled" },
  { id: "apkpure", label: "APKPure", settingKey: "store_apkpure_enabled" },
  { id: "aptoide", label: "Aptoide", settingKey: "store_aptoide_enabled" },
  { id: "fdroid", label: "F-Droid", settingKey: "store_fdroid_enabled" },
];

function storeSelectOptionLabel(
  opt: (typeof STORE_OPTIONS)[number],
  row: {
    stores?: StoreHit[];
    remote_version?: string;
    remote_source?: string;
  },
): string {
  if (!opt.id) {
    const autoVer = (row.remote_version || "").trim();
    return autoVer ? `Авто · ${autoVer}` : "Авто";
  }
  const hit = row.stores?.find((s) => s.source === opt.id);
  if (!hit) return opt.label;
  if (!hit.version_name && !hit.version_code) return `${opt.label} · нет`;
  const ver = hit.version_name || String(hit.version_code);
  return `${opt.label} · ${ver}`;
}

function isSignatureMismatch(detail: string, code?: string): boolean {
  if (code === "signature_mismatch") return true;
  const t = (detail || "").toLowerCase();
  return (
    t.includes("update_incompatible") ||
    t.includes("signatures do not match") ||
    (t.includes("signature") && t.includes("match")) ||
    t.includes("конфликт подписи")
  );
}

function isVersionDowngrade(detail: string, code?: string): boolean {
  if (code === "version_downgrade") return true;
  const t = (detail || "").toLowerCase();
  return t.includes("version_downgrade") || t.includes("даунгрейд") || t.includes("новее");
}

type Tab = "apps" | "store" | "settings";

type InstallProgressItem = {
  id: string;
  package: string;
  app_label: string;
  serial: string;
  device_label: string;
  status: "queued" | "downloading" | "installing" | "ok" | "fail" | string;
  detail: string;
  error_code?: string;
  error_title?: string;
  error_hint?: string;
  actions?: string[];
  apk_path?: string;
  raw_detail?: string;
};

function formatRunAt(raw: string | null | undefined): string {
  if (!raw) return "ещё не было";
  const n = Number(raw);
  if (Number.isFinite(n) && n > 0) {
    return new Date(n * 1000).toLocaleString("ru-RU");
  }
  const d = Date.parse(raw);
  if (Number.isFinite(d)) return new Date(d).toLocaleString("ru-RU");
  return "ещё не было";
}

function formatTs(sec: number | null | undefined): string {
  if (!sec || !Number.isFinite(sec) || sec <= 0) return "—";
  return new Date(sec * 1000).toLocaleString("ru-RU");
}

function activityKindLabel(kind: string): string {
  switch (kind) {
    case "check":
      return "Проверка";
    case "auto_install":
      return "Автоустановка";
    case "catch_up":
      return "Догон";
    case "startup":
      return "Старт";
    default:
      return kind || "—";
  }
}

function activityStatusLabel(status: string): string {
  switch (status) {
    case "ok":
      return "Успех";
    case "fail":
      return "Ошибка";
    case "skip":
      return "Пропуск";
    default:
      return "Инфо";
  }
}

/** Cache store icon URLs so rows without a local icon don't re-hit /api/store/app. */
const storeIconCache = new Map<string, string | null>();

function pickStoreIconUrl(
  card: StoreAppCard,
  preferredSource?: string,
): string {
  const preferred = (preferredSource || "").trim();
  if (preferred) {
    const fromSrc = card.by_source?.[preferred]?.icon_url?.trim();
    if (fromSrc) return fromSrc;
  }
  return (card.icon_url || "").trim();
}

function AppListIcon({
  packageName,
  iconUrl,
  preferredSource,
}: {
  packageName: string;
  iconUrl: string | null;
  preferredSource?: string;
}) {
  const [src, setSrc] = useState(iconUrl || "");
  const [phase, setPhase] = useState<"local" | "store" | "done">(
    iconUrl ? "local" : "store",
  );

  useEffect(() => {
    setSrc(iconUrl || "");
    setPhase(iconUrl ? "local" : "store");
  }, [iconUrl, packageName]);

  useEffect(() => {
    if (phase !== "store") return;
    const cached = storeIconCache.get(packageName);
    if (cached !== undefined) {
      if (cached) setSrc(cached);
      setPhase("done");
      return;
    }
    let cancelled = false;
    storeApp(packageName)
      .then((card) => {
        if (cancelled) return;
        const url = pickStoreIconUrl(card, preferredSource);
        storeIconCache.set(packageName, url || null);
        if (url) setSrc(url);
        setPhase("done");
      })
      .catch(() => {
        if (cancelled) return;
        storeIconCache.set(packageName, null);
        setPhase("done");
      });
    return () => {
      cancelled = true;
    };
  }, [phase, packageName, preferredSource]);

  if (!src) return <div className="app-icon" />;
  return (
    <img
      className="app-icon"
      src={src}
      alt=""
      onError={() => {
        if (phase === "local") {
          setSrc("");
          setPhase("store");
        } else {
          setSrc("");
          setPhase("done");
        }
      }}
    />
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("apps");
  const [status, setStatus] = useState<Status | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [discovered, setDiscovered] = useState<Discover[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("Готово");
  const [adbPath, setAdbPathLocal] = useState("");

  const [pairIp, setPairIp] = useState("");
  const [pairPort, setPairPort] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [connIp, setConnIp] = useState("");
  const [connPort, setConnPort] = useState("");
  const [pairModalOpen, setPairModalOpen] = useState(false);
  const [deviceInfoOpen, setDeviceInfoOpen] = useState(false);
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [deviceInfoLoading, setDeviceInfoLoading] = useState(false);
  const [deviceInfoBusy, setDeviceInfoBusy] = useState(false);
  const [deviceInfoError, setDeviceInfoError] = useState("");
  const [screenshotUrl, setScreenshotUrl] = useState("");
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const [installModalOpen, setInstallModalOpen] = useState(false);
  const [installProgressOpen, setInstallProgressOpen] = useState(false);
  const [installProgressTitle, setInstallProgressTitle] = useState("Установка");
  const [installProgressItems, setInstallProgressItems] = useState<InstallProgressItem[]>([]);
  const [installProgressDone, setInstallProgressDone] = useState(false);

  const [qrOpen, setQrOpen] = useState(false);
  const [qrPng, setQrPng] = useState("");
  const [qrStatus, setQrStatus] = useState("");

  const [apps, setApps] = useState<AppRow[]>([]);
  const [appsBySerial, setAppsBySerial] = useState<Record<string, AppRow[]>>({});
  const [filter, setFilter] = useState("");
  const [thirdParty, setThirdParty] = useState(true);
  const [hidePlay, setHidePlay] = useState(true);
  const [updatesOnly, setUpdatesOnly] = useState(false);
  const [updateCounts, setUpdateCounts] = useState<Record<string, number>>({});
  const [pendingSerials, setPendingSerials] = useState<string[]>([]);
  const [settings, setSettings] = useState<AppSettings>({
    bg_updates_enabled: false,
    bg_updates_interval_hours: 6,
    update_check_ttl_hours: 1,
    auto_install_enabled: false,
    auto_install_interval_hours: 24,
    notify_updates_enabled: true,
    store_rustore_enabled: true,
    store_apkpure_enabled: true,
    store_aptoide_enabled: true,
    store_fdroid_enabled: true,
    prefer_release_updates: true,
    open_browser_on_start: false,
    hide_play_store: true,
    third_party_only: true,
    apk_cache_keep_days: 2,
    bg_updates_last_run: "",
    auto_install_last_run: "",
    app_update_check_enabled: true,
    app_auto_update_enabled: false,
    app_update_check_interval_hours: 1,
    app_update_last_check: "",
    build_id: "dev",
  });
  const [intervalHoursDraft, setIntervalHoursDraft] = useState("6");
  const [ttlHoursDraft, setTtlHoursDraft] = useState("1");
  const [autoInstallHoursDraft, setAutoInstallHoursDraft] = useState("24");
  const [apkKeepDaysDraft, setApkKeepDaysDraft] = useState("2");
  const [updateBanner, setUpdateBanner] = useState<{
    local: string;
    latest: string;
  } | null>(null);
  const [appUpdateBusy, setAppUpdateBusy] = useState(false);
  const [activityLogOpen, setActivityLogOpen] = useState(false);
  const [activityLog, setActivityLog] = useState<BgActivityEntry[]>([]);
  const [activityLogBusy, setActivityLogBusy] = useState(false);

  const [apkFile, setApkFile] = useState<File | null>(null);
  const [installTargets, setInstallTargets] = useState<Record<string, boolean>>({});
  const [log, setLog] = useState("");

  const [storeQuery, setStoreQuery] = useState("");
  const [storeResults, setStoreResults] = useState<StoreSearchResult[]>([]);
  const [storeSearching, setStoreSearching] = useState(false);
  const [storeCard, setStoreCard] = useState<StoreAppCard | null>(null);
  const [storeCardSource, setStoreCardSource] = useState("");
  const [storeCardOpen, setStoreCardOpen] = useState(false);
  const [storeInstallOpen, setStoreInstallOpen] = useState(false);
  const [storeInstallSource, setStoreInstallSource] = useState("");
  const [storeInstallVersions, setStoreInstallVersions] = useState<StoreVersion[]>([]);
  const [storeInstallVersionCode, setStoreInstallVersionCode] = useState<number | "">("");
  const [storeInstallTargets, setStoreInstallTargets] = useState<Record<string, boolean>>(
    {},
  );
  const [storeVersionsLoading, setStoreVersionsLoading] = useState(false);

  const [appDetailsOpen, setAppDetailsOpen] = useState(false);
  const [appDetailsLoading, setAppDetailsLoading] = useState(false);
  const [appDetails, setAppDetails] = useState<AppDetails | null>(null);
  const [appDetailsPkg, setAppDetailsPkg] = useState("");
  const [appDetailsError, setAppDetailsError] = useState("");
  const [appDetailsBusy, setAppDetailsBusy] = useState(false);
  const [detailsStoreCard, setDetailsStoreCard] = useState<StoreAppCard | null>(null);
  const [detailsStoreSource, setDetailsStoreSource] = useState("");
  const [detailsStoreVersions, setDetailsStoreVersions] = useState<StoreVersion[]>([]);
  const [detailsStoreVersionCode, setDetailsStoreVersionCode] = useState<number | "">("");
  const [detailsStoreLoading, setDetailsStoreLoading] = useState(false);
  const [detailsVersionsLoading, setDetailsVersionsLoading] = useState(false);

  const defaultTabDone = useRef(false);
  const autoCheckDone = useRef(false);
  const selectedRef = useRef(selected);
  const hidePlayRef = useRef(hidePlay);
  selectedRef.current = selected;
  hidePlayRef.current = hidePlay;

  const selectedDevice = useMemo(
    () => devices.find((d) => d.serial === selected) ?? null,
    [devices, selected],
  );

  const online = useMemo(() => devices.filter((d) => d.online), [devices]);

  const totalUpdates = useMemo(
    () => Object.values(updateCounts).reduce((a, b) => a + b, 0),
    [updateCounts],
  );

  const selectedUpdateCount = useMemo(
    () => (selected ? updateCounts[selected] ?? 0 : 0),
    [updateCounts, selected],
  );

  const filteredApps = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const rows = apps.filter((a) => {
      if (
        hidePlay &&
        (a.update_status === "play" || a.installer === "com.android.vending")
      ) {
        return false;
      }
      if (updatesOnly && !a.has_update && !a.manual_update) return false;
      if (!q) return true;
      return (
        a.label.toLowerCase().includes(q) ||
        a.package_name.toLowerCase().includes(q)
      );
    });
    return rows.sort((a, b) => {
      const u = Number(b.has_update) - Number(a.has_update);
      if (u !== 0) return u;
      const m = Number(Boolean(b.manual_update)) - Number(Boolean(a.manual_update));
      if (m !== 0) return m;
      return a.label.localeCompare(b.label, "ru");
    });
  }, [apps, filter, hidePlay, updatesOnly]);

  const appDetailsIgnored = Boolean(
    apps.find((a) => a.package_name === appDetailsPkg)?.update_ignored,
  );

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    setMsg(label);
    try {
      await fn();
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setMsg(text);
      console.error(e);
    } finally {
      setBusy(false);
    }
  }, []);

  const applyCache = useCallback(
    (cache: UpdatesCache) => {
      setAppsBySerial(cache.devices || {});
      setUpdateCounts(cache.update_counts || {});
      setPendingSerials(cache.pending_serials || []);
      setSettings((s) => ({
        ...s,
        bg_updates_last_run: cache.bg_updates_last_run || s.bg_updates_last_run,
        auto_install_last_run: cache.auto_install_last_run || s.auto_install_last_run,
      }));
      const serial = selectedRef.current;
      if (serial && cache.devices?.[serial]) {
        setApps(cache.devices[serial]);
      } else if (serial) {
        setApps([]);
      }
    },
    [],
  );

  const loadCache = useCallback(async () => {
    const cache = await getUpdatesCache();
    applyCache(cache);
    return cache;
  }, [applyCache]);

  const loadStatus = useCallback(async () => {
    const s = await getStatus();
    setStatus(s);
    setAdbPathLocal(s.adb_path);
    if (s.update_available && s.latest_build) {
      setUpdateBanner({
        local: s.build_id || "dev",
        latest: s.latest_build,
      });
    } else {
      setUpdateBanner(null);
    }
  }, []);

  const loadDevices = useCallback(async () => {
    const list = await listDevices();
    setDevices(list);
    setSelected((prev) => {
      if (prev && list.some((d) => d.serial === prev)) return prev;
      const firstOnline = list.find((d) => d.online);
      return firstOnline?.serial || list[0]?.serial || "";
    });
    setInstallTargets((prev) => {
      const next: Record<string, boolean> = {};
      for (const d of list.filter((x) => x.online)) {
        next[d.serial] = prev[d.serial] ?? true;
      }
      return next;
    });
    if (!defaultTabDone.current) {
      defaultTabDone.current = true;
      if (list.some((d) => d.online)) {
        setTab("apps");
      }
    }
    return list;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await loadStatus();
        const s = await getSettings();
        setSettings(s);
        setIntervalHoursDraft(String(s.bg_updates_interval_hours));
        setTtlHoursDraft(String(s.update_check_ttl_hours));
        setAutoInstallHoursDraft(String(s.auto_install_interval_hours ?? 24));
        setApkKeepDaysDraft(String(s.apk_cache_keep_days ?? 2));
        setHidePlay(s.hide_play_store);
        setThirdParty(s.third_party_only);
        hidePlayRef.current = s.hide_play_store;
        const list = await loadDevices();
        const cache = await loadCache();
        const onlineSerials = list.filter((d) => d.online).map((d) => d.serial);
        const hasCache = Object.values(cache.devices || {}).some(
          (rows) => Array.isArray(rows) && rows.length > 0,
        );
        // Empty cache + online device → start check immediately (don't wait for manual click)
        if (!autoCheckDone.current && onlineSerials.length > 0 && !hasCache) {
          autoCheckDone.current = true;
          await runStartupCheck();
        }
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once on mount
  }, [loadCache, loadDevices, loadStatus]);

  // Switching device: show cache only (no store checks)
  useEffect(() => {
    if (!selected) {
      setApps([]);
      return;
    }
    setApps(appsBySerial[selected] ?? []);
  }, [selected, appsBySerial]);

  async function onRefresh() {
    await run("Обновление устройств…", async () => {
      const list = await refreshDevices();
      setDevices(list);
      setMsg(`Устройств: ${list.length}, online: ${list.filter((d) => d.online).length}`);
      await loadStatus();
    });
  }

  async function onDiscover() {
    await run("Поиск в сети…", async () => {
      const found = await discoverNetwork();
      setDiscovered(found);
      setMsg(`Найдено endpoints: ${found.length}`);
    });
  }

  async function onEnsureAdb() {
    await run("Проверка / загрузка ADB…", async () => {
      const r = await ensureAdb();
      setMsg(r.message);
      await loadStatus();
    });
  }

  async function onSaveAdb() {
    await run("Сохранение пути ADB…", async () => {
      const r = await setAdbPath(adbPath);
      setMsg(r.message);
      await loadStatus();
    });
  }

  async function onSaveAppSettings() {
    await run("Сохранение настроек…", async () => {
      const hours = Number(intervalHoursDraft.replace(",", "."));
      if (!Number.isFinite(hours) || hours < 0.25 || hours > 168) {
        throw new Error("Интервал проверки: от 0.25 до 168 часов");
      }
      const ttlHours = Number(ttlHoursDraft.replace(",", "."));
      if (!Number.isFinite(ttlHours) || ttlHours < 0.25 || ttlHours > 168) {
        throw new Error("Пауза повторной проверки: от 0.25 до 168 часов");
      }
      const autoHours = Number(autoInstallHoursDraft.replace(",", "."));
      if (!Number.isFinite(autoHours) || autoHours < 0.25 || autoHours > 168) {
        throw new Error("Интервал автоустановки: от 0.25 до 168 часов");
      }
      const apkKeepDays = Number(apkKeepDaysDraft.replace(",", "."));
      if (!Number.isFinite(apkKeepDays) || apkKeepDays < 0.25 || apkKeepDays > 365) {
        throw new Error("Срок хранения APK: от 0.25 до 365 дней");
      }
      if (
        !settings.store_rustore_enabled &&
        !settings.store_apkpure_enabled &&
        !settings.store_aptoide_enabled &&
        !settings.store_fdroid_enabled
      ) {
        throw new Error("Нужен хотя бы один включённый стор");
      }
      const next = await saveSettings({
        bg_updates_enabled: settings.bg_updates_enabled,
        bg_updates_interval_hours: hours,
        update_check_ttl_hours: ttlHours,
        auto_install_enabled: settings.auto_install_enabled,
        auto_install_interval_hours: autoHours,
        notify_updates_enabled: settings.notify_updates_enabled,
        store_rustore_enabled: settings.store_rustore_enabled,
        store_apkpure_enabled: settings.store_apkpure_enabled,
        store_aptoide_enabled: settings.store_aptoide_enabled,
        store_fdroid_enabled: settings.store_fdroid_enabled,
        prefer_release_updates: settings.prefer_release_updates,
        open_browser_on_start: settings.open_browser_on_start,
        hide_play_store: hidePlay,
        third_party_only: thirdParty,
        apk_cache_keep_days: apkKeepDays,
        app_update_check_enabled: settings.app_update_check_enabled,
        app_auto_update_enabled: settings.app_auto_update_enabled,
        app_update_check_interval_hours: settings.app_update_check_interval_hours,
      });
      setSettings(next);
      setIntervalHoursDraft(String(next.bg_updates_interval_hours));
      setTtlHoursDraft(String(next.update_check_ttl_hours));
      setAutoInstallHoursDraft(String(next.auto_install_interval_hours));
      setApkKeepDaysDraft(String(next.apk_cache_keep_days ?? 2));
      setHidePlay(next.hide_play_store);
      setThirdParty(next.third_party_only);
      setMsg("Настройки сохранены");
    });
  }

  async function onCheckAppUpdate() {
    setAppUpdateBusy(true);
    try {
      const r = await checkAppUpdate();
      setSettings((s) => ({
        ...s,
        app_update_last_check: r.last_check || s.app_update_last_check,
        build_id: r.local_build || s.build_id,
      }));
      if (r.update_available && r.latest_build) {
        setUpdateBanner({ local: r.local_build, latest: r.latest_build });
      } else {
        setUpdateBanner(null);
      }
      setMsg(r.message || (r.update_available ? "Есть новая сборка" : "Сборка актуальна"));
      await loadStatus();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setAppUpdateBusy(false);
    }
  }

  async function onApplyAppUpdate() {
    if (
      !window.confirm(
        "Скачать и установить новую сборку? Приложение перезапустится. Папка data не затрагивается.",
      )
    ) {
      return;
    }
    setAppUpdateBusy(true);
    try {
      const r = await applyAppUpdate();
      setMsg(r.message || "Обновление…");
      if (r.restarting) {
        setUpdateBanner(null);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setAppUpdateBusy(false);
    }
  }

  async function onDismissAppUpdate() {
    try {
      await dismissAppUpdate();
      setUpdateBanner(null);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function onRunBgNow() {
    await onForceCheckAll();
  }

  async function loadActivityLog() {
    setActivityLogBusy(true);
    try {
      const data = await getActivityLog(250);
      setActivityLog(data.entries || []);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setActivityLogBusy(false);
    }
  }

  async function openActivityLog() {
    setActivityLogOpen(true);
    await loadActivityLog();
  }

  async function onClearActivityLog() {
    if (!window.confirm("Очистить журнал автопроверки и автоустановки?")) return;
    setActivityLogBusy(true);
    try {
      await clearActivityLog();
      setActivityLog([]);
      setMsg("Журнал очищен");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setActivityLogBusy(false);
    }
  }

  async function onClearFileCache() {
    if (
      !window.confirm(
        "Удалить кеш APK, иконок и превью устройств?\nБаза данных, настройки и ADB не трогаются.",
      )
    ) {
      return;
    }
    await run("Очистка кеша…", async () => {
      const result = await clearFileCache();
      setMsg(result.message || "Кеш очищен");
    });
  }

  async function onUnlockJob() {
    await run("Сброс блокировки…", async () => {
      const r = await unlockJob();
      setMsg(r.message || "Блокировка сброшена");
    });
  }

  async function openQr() {
    await run("QR…", async () => {
      const started = await qrStart();
      setQrPng(started.png_base64);
      setQrStatus(started.message);
      setQrOpen(true);
      setBusy(false);
      try {
        await qrWait((ev) => {
          if (ev.type === "progress") setQrStatus(String(ev.message || ""));
          if (ev.type === "done") {
            setQrStatus(String(ev.message || ""));
            if (ev.ok) {
              void loadDevices();
              setTimeout(() => setQrOpen(false), 1200);
            }
          }
        });
      } catch (e) {
        setQrStatus(e instanceof Error ? e.message : String(e));
      }
    });
  }

  async function closeQr() {
    try {
      await qrCancel();
    } catch {
      /* ignore */
    }
    setQrOpen(false);
  }

  function selectDevice(serial: string) {
    setSelected(serial);
  }

  async function loadDeviceInfoPanel(serial: string) {
    setDeviceInfoLoading(true);
    setDeviceInfoError("");
    try {
      const info = await fetchDeviceInfo(serial);
      setDeviceInfo(info);
      if (info.screenshot_url) {
        setScreenshotUrl(info.screenshot_url);
      }
    } catch (e) {
      setDeviceInfoError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeviceInfoLoading(false);
    }
  }

  function openDeviceInfo(serial?: string) {
    const ser = (serial || selectedDevice?.serial || "").trim();
    if (!ser) return;
    setSelected(ser);
    setDeviceInfoOpen(true);
    setDeviceInfo(null);
    setScreenshotUrl(""); // restored from server cache in loadDeviceInfoPanel
    setDeviceInfoError("");
    void loadDeviceInfoPanel(ser);
  }

  function closeDeviceInfo() {
    if (deviceInfoBusy || screenshotBusy) return;
    setDeviceInfoOpen(false);
  }

  async function onDeviceInfoAction(action: DeviceActionName) {
    if (!selectedDevice?.online) return;
    setDeviceInfoBusy(true);
    try {
      const r = await deviceAction(selectedDevice.serial, action);
      setMsg(r.message);
      if (!r.ok) setDeviceInfoError(r.message);
      else {
        setDeviceInfoError("");
        await loadDeviceInfoPanel(selectedDevice.serial);
      }
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setMsg(text);
      setDeviceInfoError(text);
    } finally {
      setDeviceInfoBusy(false);
    }
  }

  async function onDeviceScreenshot() {
    if (!selectedDevice?.online) return;
    setScreenshotBusy(true);
    setDeviceInfoError("");
    try {
      const r = await deviceScreenshot(selectedDevice.serial);
      if (r.url) setScreenshotUrl(r.url);
      setMsg(r.message);
      if (!r.ok) setDeviceInfoError(r.message);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setMsg(text);
      setDeviceInfoError(text);
    } finally {
      setScreenshotBusy(false);
    }
  }

  async function waitForCacheFill(timeoutMs = 120_000): Promise<boolean> {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      await new Promise((r) => setTimeout(r, 2000));
      const cache = await loadCache();
      if (Object.values(cache.devices || {}).some((rows) => rows.length > 0)) {
        return true;
      }
    }
    return false;
  }

  function applyCheckBatchDone(
    ev: Record<string, unknown>,
    opts: { merge: boolean },
  ) {
    const devicesMap = (ev.devices || {}) as Record<string, AppRow[]>;
    if (opts.merge) {
      setAppsBySerial((prev) => ({ ...prev, ...devicesMap }));
      setUpdateCounts((prev) => {
        const next = { ...prev };
        for (const [ser, rows] of Object.entries(devicesMap)) {
          next[ser] = rows.filter((a) => a.has_update).length;
        }
        return next;
      });
    } else {
      const counts: Record<string, number> = {};
      for (const [ser, rows] of Object.entries(devicesMap)) {
        counts[ser] = rows.filter((a) => a.has_update).length;
      }
      setAppsBySerial(devicesMap);
      setUpdateCounts(counts);
    }
    if (ev.pending_serials) {
      setPendingSerials((ev.pending_serials as string[]) || []);
    }
    if (selectedRef.current && devicesMap[selectedRef.current]) {
      setApps(devicesMap[selectedRef.current]);
    }
    setMsg(String(ev.message || "Проверка завершена"));
  }

  async function runCheckBatch(opts: {
    label: string;
    serials?: string[] | null;
    merge: boolean;
  }) {
    await run(opts.label, async () => {
      try {
        await checkUpdatesBatch(
          (ev) => {
            if (ev.type === "progress") setMsg(String(ev.message || ""));
            if (ev.type === "error") setMsg(String(ev.message || "Ошибка"));
            if (ev.type === "done") {
              applyCheckBatchDone(ev, { merge: opts.merge });
            }
          },
          {
            force: true,
            hidePlayStore: hidePlayRef.current,
            serials: opts.serials ?? undefined,
          },
        );
      } catch (e) {
        const text = e instanceof Error ? e.message : String(e);
        if (/уже выполняется|409/i.test(text)) {
          setMsg("Фоновая проверка… ждём результат");
          const ok = await waitForCacheFill();
          setMsg(
            ok
              ? "Проверка завершена"
              : "Кеш всё ещё пуст — нажмите «Проверить обновления»",
          );
        } else {
          throw e;
        }
      }
      await loadCache();
      const s = await getSettings();
      setSettings(s);
    });
  }

  async function runStartupCheck() {
    await runCheckBatch({
      label: "Проверка обновлений…",
      serials: null,
      merge: false,
    });
  }

  async function onForceCheckAll() {
    await runCheckBatch({
      label: "Проверка обновлений (все)…",
      serials: null,
      merge: false,
    });
  }

  async function onForceCheckSelected() {
    if (!selected) {
      setMsg("Выберите устройство");
      return;
    }
    const label = selectedDevice?.label || selected;
    await runCheckBatch({
      label: `Проверка: ${label}…`,
      serials: [selected],
      merge: true,
    });
  }

  function applyInstallSse(ev: Record<string, unknown>) {
    if (ev.type === "install_plan" && Array.isArray(ev.items)) {
      setInstallProgressItems(ev.items as InstallProgressItem[]);
      setInstallProgressTitle(String(ev.message || "Установка"));
      setInstallProgressDone(false);
      setInstallProgressOpen(true);
      setMsg(String(ev.message || "Установка…"));
      return;
    }
    if (ev.type === "install_item") {
      const id = String(ev.id || "");
      setInstallProgressOpen(true);
      setInstallProgressItems((prev) => {
        const next = [...prev];
        const idx = next.findIndex((x) => x.id === id);
        const prevItem = idx >= 0 ? next[idx] : null;
        const actionsRaw = ev.actions;
        const actions = Array.isArray(actionsRaw)
          ? actionsRaw.map(String)
          : prevItem?.actions;
        const patch: InstallProgressItem = {
          id,
          package: String(ev.package || prevItem?.package || ""),
          app_label: String(ev.app_label || prevItem?.app_label || ""),
          serial: String(ev.serial || prevItem?.serial || ""),
          device_label: String(ev.device_label || prevItem?.device_label || ""),
          status: String(ev.status || "queued"),
          detail: String(ev.detail || prevItem?.detail || ""),
          error_code:
            ev.error_code != null
              ? String(ev.error_code)
              : prevItem?.error_code,
          error_title:
            ev.error_title != null
              ? String(ev.error_title)
              : prevItem?.error_title,
          error_hint:
            ev.error_hint != null
              ? String(ev.error_hint)
              : prevItem?.error_hint,
          actions,
          apk_path:
            ev.apk_path != null ? String(ev.apk_path) : prevItem?.apk_path,
          raw_detail:
            ev.raw_detail != null
              ? String(ev.raw_detail)
              : prevItem?.raw_detail,
        };
        if (idx >= 0) next[idx] = { ...next[idx], ...patch };
        else next.push(patch);
        return next;
      });
      if (ev.detail) setMsg(String(ev.detail));
      return;
    }
    if (ev.type === "progress") setMsg(String(ev.message || ""));
    if (ev.type === "error") {
      setMsg(String(ev.message || "Ошибка"));
      setInstallProgressDone(true);
    }
  }

  async function onInstallAll(scope: "all" | "selected" = "all") {
    const serials =
      scope === "selected" && selectedRef.current
        ? [selectedRef.current]
        : undefined;
    const title =
      scope === "selected"
        ? "Установка обновлений (это устройство)"
        : "Установка всех обновлений";
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(title);
    setInstallProgressItems([]);
    await run(title + "…", async () => {
      await installAllUpdates(
        (ev) => {
          applyInstallSse(ev);
          if (ev.type === "done") {
            if (ev.devices) {
              const devicesMap = ev.devices as Record<string, AppRow[]>;
              setAppsBySerial(devicesMap);
              if (selectedRef.current && devicesMap[selectedRef.current]) {
                setApps(devicesMap[selectedRef.current]);
              }
            }
            if (ev.update_counts) {
              setUpdateCounts(ev.update_counts as Record<string, number>);
            }
            setPendingSerials((ev.pending_serials as string[]) || []);
            setMsg(String(ev.message || "Установка завершена"));
            setInstallProgressDone(true);
          }
        },
        serials ? { serials } : undefined,
      );
      await loadCache();
      setInstallProgressDone(true);
    });
  }

  function seedInstallItem(opts: {
    package: string;
    app_label: string;
    serial: string;
    detail?: string;
  }): InstallProgressItem {
    const d = devices.find((x) => x.serial === opts.serial);
    return {
      id: `${opts.package}::${opts.serial}`,
      package: opts.package,
      app_label: opts.app_label,
      serial: opts.serial,
      device_label: d?.label || opts.serial,
      status: "queued",
      detail: opts.detail || "Подготовка…",
    };
  }

  function finalizeStuckInstallProgress(fallbackDetail = "Не удалось начать установку") {
    setInstallProgressDone(true);
    setInstallProgressItems((prev) => {
      if (prev.length === 0) {
        return [
          {
            id: "failed",
            package: "",
            app_label: "Установка",
            serial: selected || "",
            device_label: "",
            status: "fail",
            detail: fallbackDetail,
            error_title: "Ошибка",
            error_hint: fallbackDetail,
          },
        ];
      }
      const stuck = prev.every(
        (it) =>
          it.status === "queued" ||
          it.status === "downloading" ||
          it.status === "installing",
      );
      if (!stuck) return prev;
      return prev.map((it) =>
        it.status === "ok" || it.status === "fail"
          ? it
          : {
              ...it,
              status: "fail",
              detail: it.detail && it.detail !== "Подготовка…" && it.detail !== "В очереди"
                ? it.detail
                : fallbackDetail,
              error_title: it.error_title || "Ошибка",
              error_hint: it.error_hint || fallbackDetail,
            },
      );
    });
  }

  async function onInstallUpdate(pkg: string) {
    if (!selected) return;
    const row = apps.find((a) => a.package_name === pkg);
    const label = row?.label || pkg;
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(`Обновление ${label}`);
    setInstallProgressItems([
      seedInstallItem({
        package: pkg,
        app_label: label,
        serial: selected,
        detail: "Подготовка…",
      }),
    ]);
    let failDetail = "Не удалось начать установку";
    try {
      await run(`Обновление ${pkg}…`, async () => {
        try {
          await installUpdate(selected, pkg, (ev) => {
            applyInstallSse(ev);
            if (ev.type === "done") {
              setMsg(String(ev.message || ""));
              setInstallProgressDone(true);
              if (Array.isArray(ev.apps)) {
                putAppsFromList(selected, ev.apps as AppRow[]);
              } else {
                void loadCache();
              }
            }
            if (ev.type === "error") {
              failDetail = String(ev.message || "Ошибка установки");
              finalizeStuckInstallProgress(failDetail);
            }
          });
        } catch (e) {
          failDetail = e instanceof Error ? e.message : String(e);
          throw e;
        }
        setInstallProgressDone(true);
      });
    } finally {
      finalizeStuckInstallProgress(failDetail);
    }
  }

  function extractApkPathFromDetail(text: string): string {
    const m = (text || "").match(/([A-Za-z]:\\[^\s\]\"]+\.apk|\/[^\s\]\"]+\.apk)/i);
    return m ? m[1] : "";
  }

  async function onDowngradeInstall(item: InstallProgressItem) {
    const apkPath =
      (item.apk_path || "").trim() ||
      extractApkPathFromDetail(item.raw_detail || "") ||
      extractApkPathFromDetail(item.detail || "");
    if (!apkPath) {
      setMsg("Путь к APK в кеше неизвестен — повторите установку из свойств");
      return;
    }
    const serial = item.serial || selected;
    if (!serial) {
      setMsg("Нет устройства для установки");
      return;
    }
    const ok = window.confirm(
      `${item.error_title || "Даунгрейд"}\n\n` +
        `${item.error_hint || "Будет установлена более старая версия поверх текущей (adb install -r -d). Данные приложения обычно сохраняются."}\n\n` +
        "Продолжить установку с даунгрейдом?",
    );
    if (!ok) return;
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(
      `Даунгрейд ${item.app_label || item.package}`,
    );
    setInstallProgressItems([
      {
        ...item,
        id: `${item.package}::${serial}`,
        status: "queued",
        detail: "Подготовка даунгрейда…",
        error_code: undefined,
        error_title: undefined,
        error_hint: undefined,
        actions: undefined,
      },
    ]);
    let failDetail = "Не удалось начать даунгрейд";
    try {
      await run(`Даунгрейд ${item.package}…`, async () => {
        try {
          await installCachedApk(
            {
              serial,
              package_name: item.package,
              apk_path: apkPath,
              allow_downgrade: true,
            },
            (ev) => {
              applyInstallSse(ev);
              if (ev.type === "done") {
                setMsg(String(ev.message || "Готово"));
                setInstallProgressDone(true);
              }
              if (ev.type === "error") {
                failDetail = String(ev.message || failDetail);
                finalizeStuckInstallProgress(failDetail);
              }
            },
          );
        } catch (e) {
          failDetail = e instanceof Error ? e.message : String(e);
          throw e;
        }
        setInstallProgressDone(true);
        await loadCache();
      });
    } finally {
      finalizeStuckInstallProgress(failDetail);
    }
  }

  async function onReinstallUpdate(pkg: string) {
    if (!selected) return;
    const ok = window.confirm(
      "Переустановка удалит приложение и все его данные на устройстве, затем поставит APK заново. Продолжить?",
    );
    if (!ok) return;
    const appRow = apps.find((a) => a.package_name === pkg);
    const progRow = installProgressItems.find((i) => i.package === pkg);
    const label = appRow?.label || progRow?.app_label || pkg;
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(`Переустановка ${label}`);
    setInstallProgressItems([
      seedInstallItem({
        package: pkg,
        app_label: label,
        serial: selected,
        detail: "Подготовка…",
      }),
    ]);
    let failDetail = "Не удалось начать переустановку";
    try {
      await run(`Переустановка ${pkg}…`, async () => {
        try {
          await reinstallUpdate(selected, pkg, (ev) => {
            applyInstallSse(ev);
            if (ev.type === "done") {
              setMsg(String(ev.message || ""));
              setInstallProgressDone(true);
              if (Array.isArray(ev.apps)) {
                putAppsFromList(selected, ev.apps as AppRow[]);
              } else {
                void loadCache();
              }
            }
            if (ev.type === "error") {
              failDetail = String(ev.message || failDetail);
              finalizeStuckInstallProgress(failDetail);
            }
          });
        } catch (e) {
          failDetail = e instanceof Error ? e.message : String(e);
          throw e;
        }
        setInstallProgressDone(true);
      });
    } finally {
      finalizeStuckInstallProgress(failDetail);
    }
  }

  function formatBytes(n: number | null | undefined): string {
    if (n == null || !Number.isFinite(n) || n < 0) return "н/д";
    if (n < 1024) return `${n} Б`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} МБ`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} ГБ`;
  }

  function shortPermission(name: string): string {
    return name.replace(/^android\.permission\./, "");
  }

  async function loadDetailsStoreVersions(pkg: string, source: string) {
    if (!pkg || !source) {
      setDetailsStoreVersions([]);
      setDetailsStoreVersionCode("");
      return;
    }
    setDetailsVersionsLoading(true);
    try {
      const res = await storeVersions(pkg, source, 30);
      const versions = res.versions || [];
      setDetailsStoreVersions(versions);
      setDetailsStoreVersionCode(versions[0]?.version_code ?? "");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
      setDetailsStoreVersions([]);
      setDetailsStoreVersionCode("");
    } finally {
      setDetailsVersionsLoading(false);
    }
  }

  async function openAppDetails(pkg: string) {
    if (!selected) {
      setMsg("Выберите устройство");
      return;
    }
    const row = apps.find((a) => a.package_name === pkg);
    setAppDetailsPkg(pkg);
    setAppDetailsOpen(true);
    setAppDetails(null);
    setAppDetailsError("");
    setAppDetailsLoading(true);
    setDetailsStoreCard(null);
    setDetailsStoreSource("");
    setDetailsStoreVersions([]);
    setDetailsStoreVersionCode("");
    setDetailsStoreLoading(true);
    try {
      const d = await getAppDetails(selected, pkg);
      setAppDetails(d);
    } catch (err) {
      setAppDetailsError(err instanceof Error ? err.message : String(err));
    } finally {
      setAppDetailsLoading(false);
    }
    try {
      const card = await storeApp(pkg);
      setDetailsStoreCard(card);
      const preferred = (row?.install_source || row?.remote_source || "").trim();
      const src =
        (preferred && card.sources.includes(preferred) ? preferred : "") ||
        card.sources[0] ||
        "";
      setDetailsStoreSource(src);
      if (src) void loadDetailsStoreVersions(pkg, src);
    } catch {
      setDetailsStoreCard(null);
    } finally {
      setDetailsStoreLoading(false);
    }
  }

  async function onDetailsStoreInstall() {
    if (!selected || !appDetailsPkg || !detailsStoreSource) return;
    if (detailsStoreVersionCode === "") {
      setMsg("Выберите версию");
      return;
    }
    const versionCode = Number(detailsStoreVersionCode);
    const unsupported = detailsStoreVersions.find(
      (v) => v.version_code === versionCode,
    )?.unsupported_reason;
    if (unsupported) {
      const ok = window.confirm(
        `Эта версия может быть недоступна как APK (${unsupported}). Всё равно попробовать?`,
      );
      if (!ok) return;
    }
    const appLabel = appDetails?.label || detailsStoreCard?.title || appDetailsPkg;
    const seedItems: InstallProgressItem[] = [
      {
        id: `${appDetailsPkg}:${selected}`,
        package: appDetailsPkg,
        app_label: appLabel,
        serial: selected,
        device_label: selectedDevice?.label || selected,
        status: "queued",
        detail: "Подготовка…",
      },
    ];
    setAppDetailsOpen(false);
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(`Установка ${appLabel}`);
    setInstallProgressItems(seedItems);
    let failDetail = "Не удалось начать установку";
    try {
      await run(`Установка ${appDetailsPkg}…`, async () => {
        try {
          await storeInstall(
            {
              package_name: appDetailsPkg,
              source: detailsStoreSource,
              version_code: versionCode,
              serials: [selected],
            },
            (ev) => {
              applyInstallSse(ev);
              if (ev.type === "done") {
                setMsg(String(ev.message || "Установка завершена"));
                setInstallProgressDone(true);
              }
              if (ev.type === "error") {
                failDetail = String(ev.message || failDetail);
                finalizeStuckInstallProgress(failDetail);
              }
            },
          );
        } catch (e) {
          failDetail = e instanceof Error ? e.message : String(e);
          throw e;
        }
        setInstallProgressDone(true);
        try {
          await setAppSource(selected, appDetailsPkg, detailsStoreSource);
        } catch {
          /* source mark is best-effort; install already remembers on success */
        }
        await loadCache();
        const label =
          STORE_OPTIONS.find((o) => o.id === detailsStoreSource)?.label ||
          detailsStoreSource;
        setApps((prev) =>
          prev.map((a) =>
            a.package_name === appDetailsPkg
              ? {
                  ...a,
                  install_source: detailsStoreSource,
                  install_source_label: label,
                  install_source_tooltip: `Установлено из: ${label}`,
                }
              : a,
          ),
        );
      });
    } finally {
      finalizeStuckInstallProgress(failDetail);
    }
  }

  async function onAppDetailsAction(
    action: "force_stop" | "clear_cache" | "clear_data" | "uninstall" | "open_settings",
  ) {
    if (!selected || !appDetailsPkg) return;
    if (action === "clear_data") {
      const ok = window.confirm(
        "Очистить все данные приложения? Аккаунты и настройки внутри приложения будут удалены.",
      );
      if (!ok) return;
    }
    if (action === "uninstall") {
      const ok = window.confirm(
        `Удалить ${appDetails?.label || appDetailsPkg} с устройства?`,
      );
      if (!ok) return;
    }
    setAppDetailsBusy(true);
    try {
      const r = await appAction(selected, appDetailsPkg, action);
      setMsg(r.message);
      if (!r.ok) {
        setAppDetailsError(r.message);
        return;
      }
      setAppDetailsError("");
      if (action === "uninstall") {
        setApps((prev) => prev.filter((a) => a.package_name !== appDetailsPkg));
        setAppsBySerial((prev) => {
          const rows = prev[selected];
          if (!rows) return prev;
          return {
            ...prev,
            [selected]: rows.filter((a) => a.package_name !== appDetailsPkg),
          };
        });
        setUpdateCounts((prev) => {
          const row = apps.find((a) => a.package_name === appDetailsPkg);
          if (!row?.has_update) return prev;
          const n = Math.max(0, (prev[selected] || 0) - 1);
          return { ...prev, [selected]: n };
        });
        setAppDetailsOpen(false);
        setAppDetails(null);
        return;
      }
      if (r.details) {
        setAppDetails((prev) => ({
          ...(r.details as AppDetails),
          icon_url: (r.details as AppDetails).icon_url || prev?.icon_url,
          install_source: (r.details as AppDetails).install_source || prev?.install_source,
          install_source_label:
            (r.details as AppDetails).install_source_label || prev?.install_source_label,
        }));
      } else if (action === "clear_cache" || action === "clear_data") {
        const d = await getAppDetails(selected, appDetailsPkg);
        setAppDetails(d);
      }
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      setAppDetailsError(text);
      setMsg(text);
    } finally {
      setAppDetailsBusy(false);
    }
  }

  async function onTogglePermission(permission: string, granted: boolean) {
    if (!selected || !appDetailsPkg) return;
    setAppDetailsBusy(true);
    try {
      const r = await setAppPermission(selected, appDetailsPkg, permission, granted);
      setMsg(r.message);
      if (!r.ok) {
        setAppDetailsError(r.message);
        return;
      }
      setAppDetailsError("");
      if (r.details) {
        setAppDetails(r.details as AppDetails);
      } else {
        setAppDetails((prev) =>
          prev
            ? {
                ...prev,
                permissions: prev.permissions.map((p) =>
                  p.name === permission ? { ...p, granted } : p,
                ),
              }
            : prev,
        );
      }
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      setAppDetailsError(text);
      setMsg(text);
    } finally {
      setAppDetailsBusy(false);
    }
  }

  async function onStoreSearch(e?: FormEvent) {
    e?.preventDefault();
    const q = storeQuery.trim();
    if (!q) {
      setStoreResults([]);
      return;
    }
    setStoreSearching(true);
    setMsg(`Поиск: ${q}…`);
    try {
      const res = await storeSearch(q, 30);
      setStoreResults(res.results || []);
      setMsg(
        res.results?.length
          ? `Найдено: ${res.results.length}`
          : "Ничего не найдено в включённых сторах",
      );
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
      setStoreResults([]);
    } finally {
      setStoreSearching(false);
    }
  }

  async function openStoreCard(pkg: string) {
    setStoreCardOpen(true);
    setStoreCard(null);
    setStoreCardSource("");
    setMsg(`Карточка ${pkg}…`);
    try {
      const card = await storeApp(pkg);
      setStoreCard(card);
      const first = card.sources[0] || "";
      setStoreCardSource(first);
      setMsg(card.title || pkg);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
      setStoreCardOpen(false);
    }
  }

  async function loadStoreVersions(pkg: string, source: string) {
    if (!pkg || !source) {
      setStoreInstallVersions([]);
      setStoreInstallVersionCode("");
      return;
    }
    setStoreVersionsLoading(true);
    try {
      const res = await storeVersions(pkg, source, 30);
      const versions = res.versions || [];
      setStoreInstallVersions(versions);
      setStoreInstallVersionCode(versions[0]?.version_code ?? "");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
      setStoreInstallVersions([]);
      setStoreInstallVersionCode("");
    } finally {
      setStoreVersionsLoading(false);
    }
  }

  function openStoreInstallDialog() {
    if (!storeCard) return;
    const src = storeCardSource || storeCard.sources[0] || "";
    setStoreInstallSource(src);
    const defaults: Record<string, boolean> = {};
    for (const d of online) {
      defaults[d.serial] = selected ? d.serial === selected : false;
    }
    if (selected && online.some((d) => d.serial === selected)) {
      defaults[selected] = true;
    } else if (online.length === 1) {
      defaults[online[0].serial] = true;
    }
    setStoreInstallTargets(defaults);
    setStoreInstallOpen(true);
    void loadStoreVersions(storeCard.package_name, src);
  }

  async function onStoreInstallConfirm() {
    if (!storeCard || !storeInstallSource) return;
    const serials = Object.entries(storeInstallTargets)
      .filter(([, on]) => on)
      .map(([s]) => s);
    if (!serials.length) {
      setMsg("Выберите хотя бы одно устройство");
      return;
    }
    const versionCode =
      storeInstallVersionCode === "" ? null : Number(storeInstallVersionCode);
    const appLabel = storeCard.title || storeCard.package_name;
    const seedItems: InstallProgressItem[] = serials.map((serial) => {
      const d = devices.find((x) => x.serial === serial);
      return {
        id: `${storeCard.package_name}:${serial}`,
        package: storeCard.package_name,
        app_label: appLabel,
        serial,
        device_label: d?.label || serial,
        status: "queued",
        detail: "Подготовка…",
      };
    });
    setStoreInstallOpen(false);
    setStoreCardOpen(false);
    setInstallProgressOpen(true);
    setInstallProgressDone(false);
    setInstallProgressTitle(`Установка ${appLabel}`);
    setInstallProgressItems(seedItems);
    let failDetail = "Не удалось начать установку";
    try {
      await run(`Установка ${storeCard.package_name}…`, async () => {
        try {
          await storeInstall(
            {
              package_name: storeCard.package_name,
              source: storeInstallSource,
              version_code: versionCode,
              serials,
            },
            (ev) => {
              applyInstallSse(ev);
              if (ev.type === "done") {
                setMsg(String(ev.message || "Установка завершена"));
                setInstallProgressDone(true);
              }
              if (ev.type === "error") {
                failDetail = String(ev.message || failDetail);
                finalizeStuckInstallProgress(failDetail);
              }
            },
          );
        } catch (e) {
          failDetail = e instanceof Error ? e.message : String(e);
          throw e;
        }
        setInstallProgressDone(true);
        await loadCache();
      });
    } finally {
      finalizeStuckInstallProgress(failDetail);
    }
  }

  async function onToggleUpdateIgnore(pkg: string, ignored: boolean) {
    if (!selected) return;
    await run(ignored ? "Отключение обновлений…" : "Включение обновлений…", async () => {
      const r = await setUpdateIgnore(selected, pkg, ignored);
      setMsg(r.message);
      if (r.app) {
        const apply = (rows: AppRow[]) =>
          rows.map((a) => (a.package_name === pkg ? { ...a, ...r.app! } : a));
        setApps((prev) => apply(prev));
        setAppsBySerial((prev) => {
          const rows = prev[selected];
          if (!rows) return prev;
          const next = apply(rows);
          setUpdateCounts((c) => ({
            ...c,
            [selected]: next.filter((a) => a.has_update).length,
          }));
          return { ...prev, [selected]: next };
        });
      }
    });
  }

  async function onChangeInstallSource(pkg: string, source: string) {
    if (!selected) return;
    await run("Сохранение источника…", async () => {
      const r = await setAppSource(selected, pkg, source);
      setApps((prev) =>
        prev.map((a) => {
          if (a.package_name !== pkg) return a;
          const label =
            STORE_OPTIONS.find((o) => o.id === source)?.label || source || "";
          return {
            ...a,
            install_source: source,
            install_source_label: source ? label : "",
            install_source_tooltip: source
              ? `Установлено из: ${label}`
              : "Источник неизвестен",
          };
        }),
      );
      setAppsBySerial((prev) => {
        const rows = prev[selected];
        if (!rows) return prev;
        return {
          ...prev,
          [selected]: rows.map((a) => {
            if (a.package_name !== pkg) return a;
            const label =
              STORE_OPTIONS.find((o) => o.id === source)?.label || source || "";
            return {
              ...a,
              install_source: source,
              install_source_label: source ? label : "",
              install_source_tooltip: source
                ? `Установлено из: ${label}`
                : "Источник неизвестен",
            };
          }),
        };
      });
      setMsg(r.message);
    });
  }

  function installStatusLabel(status: string): string {
    switch (status) {
      case "queued":
        return "В очереди";
      case "downloading":
        return "Скачивание";
      case "installing":
        return "Установка";
      case "ok":
        return "Готово";
      case "fail":
        return "Ошибка";
      default:
        return status;
    }
  }

  function putAppsFromList(serial: string, rows: AppRow[]) {
    setAppsBySerial((prev) => ({ ...prev, [serial]: rows }));
    setUpdateCounts((prev) => ({
      ...prev,
      [serial]: rows.filter((a) => a.has_update).length,
    }));
    if (selectedRef.current === serial) setApps(rows);
  }

  async function onInstallApk() {
    if (!apkFile) {
      setMsg("Выберите APK");
      return;
    }
    const serials = Object.entries(installTargets)
      .filter(([, v]) => v)
      .map(([k]) => k);
    if (!serials.length) {
      setMsg("Выберите устройства");
      return;
    }
    setLog("");
    await run("Установка APK…", async () => {
      await installApk(apkFile, serials, (ev) => {
        if (ev.type === "progress") {
          setMsg(String(ev.message || ""));
          setLog((prev) => prev + String(ev.message || "") + "\n");
        }
        if (ev.type === "error") {
          setMsg(String(ev.message || ""));
          setLog((prev) => prev + String(ev.message || "") + "\n");
        }
        if (ev.type === "done") {
          setMsg(ev.ok ? "Установка завершена" : "Есть ошибки установки");
          setLog((prev) => prev + String(ev.message || "") + "\n");
        }
      });
    });
  }

  return (
    <div className="app">
      {updateBanner && (
        <div className="update-banner" role="status">
          <div className="update-banner-text">
            Доступна сборка <strong>{updateBanner.latest}</strong>
            <span className="muted"> (сейчас {updateBanner.local})</span>
          </div>
          <div className="update-banner-actions">
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy || appUpdateBusy}
              onClick={() => void onApplyAppUpdate()}
            >
              Обновить
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy || appUpdateBusy}
              onClick={() => void onDismissAppUpdate()}
            >
              Позже
            </button>
          </div>
        </div>
      )}

      <div className="header-row">
        <div>
          <h1 className="brand">APK Updater</h1>
          <p className="tagline">Локальный Wi‑Fi ADB · обновления · установка</p>
        </div>
        <div className="badge" title="Идентификатор сборки">
          Сборка {status?.build_id || settings.build_id || "dev"}
        </div>
      </div>

      <nav className="tabs">
        {(
          [
            ["apps", "Приложения"],
            ["store", "Магазин"],
            ["settings", "Настройки"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`tab ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "apps" && (
        <div className="apps-layout">
          <aside className="card apps-sidebar">
            <div className="row" style={{ marginBottom: 10, justifyContent: "space-between" }}>
              <h2 style={{ margin: 0 }}>Устройства</h2>
            </div>
            <div className="row" style={{ marginBottom: 8 }}>
              <button
                type="button"
                className="btn btn-accent"
                style={{ flex: 1 }}
                disabled={busy}
                onClick={() => setPairModalOpen(true)}
              >
                Сопряжение
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                style={{ flex: 1 }}
                disabled={busy}
                onClick={() => setInstallModalOpen(true)}
              >
                Установить APK
              </button>
            </div>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ width: "100%", marginBottom: 8 }}
              disabled={busy || online.length === 0}
              onClick={() => void onForceCheckAll()}
              title="Принудительная проверка на всех online-устройствах"
            >
              Проверить обновления (все)
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ width: "100%", marginBottom: 12 }}
              disabled={busy || totalUpdates === 0}
              onClick={() => void onInstallAll("all")}
              title="Установить найденные обновления на всех устройствах"
            >
              Установить всё (все)
              {totalUpdates > 0 ? ` (${totalUpdates})` : ""}
            </button>
            {pendingSerials.length > 0 && (
              <p className="muted" style={{ marginBottom: 10 }}>
                Ожидают связи: {pendingSerials.length}
              </p>
            )}
            {online.length === 0 ? (
              <p className="muted">Нет online — откройте «Сопряжение»</p>
            ) : (
              <div className="list">
                {online.map((d) => {
                  const n = updateCounts[d.serial] ?? 0;
                  return (
                    <div
                      key={d.serial}
                      className={`side-device ${selected === d.serial ? "selected" : ""}`}
                    >
                      <button
                        type="button"
                        className="side-device-main"
                        onClick={() => selectDevice(d.serial)}
                      >
                        {d.preview_url ? (
                          <img className="thumb sm" src={d.preview_url} alt="" />
                        ) : (
                          <div className="thumb sm placeholder">●</div>
                        )}
                        <div className="meta">
                          <strong>{d.label}</strong>
                          <span>{d.channels_text}</span>
                        </div>
                      </button>
                      <div className="side-device-tools">
                        <button
                          type="button"
                          className="btn-info"
                          title="Свойства устройства"
                          disabled={busy}
                          onClick={() => openDeviceInfo(d.serial)}
                        >
                          i
                        </button>
                        {n > 0 ? (
                          <span className="upd-badge" title="Есть обновления">
                            ↑ {n}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </aside>

          <div className="panel apps-main">
            {selectedDevice && (
              <div className="card profile">
                {selectedDevice.preview_url ? (
                  <img src={selectedDevice.preview_url} alt="" />
                ) : (
                  <div className="thumb placeholder" style={{ width: 72, height: 72 }}>
                    ●
                  </div>
                )}
                <div>
                  <div className="row" style={{ alignItems: "center", gap: 8 }}>
                    <strong style={{ fontFamily: "var(--brand)", fontSize: "1.2rem" }}>
                      {selectedDevice.label}
                    </strong>
                    <button
                      type="button"
                      className="btn-info"
                      title="Свойства устройства"
                      disabled={busy}
                      onClick={() => openDeviceInfo()}
                    >
                      i
                    </button>
                  </div>
                  <div className="muted">{selectedDevice.description}</div>
                  <div className="muted">{selectedDevice.channels_text}</div>
                  <div
                    className={`device-status ${busy ? "is-busy" : ""}`}
                    title={msg}
                  >
                    {busy ? `… ${msg}` : msg}
                  </div>
                </div>
                <div className="row" style={{ flexDirection: "column", gap: 8 }}>
                  <button
                    type="button"
                    className="btn btn-accent"
                    disabled={busy || !selectedDevice?.online}
                    onClick={() => void onForceCheckSelected()}
                    title="Только это устройство"
                  >
                    Проверить обновления
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={busy || selectedUpdateCount === 0}
                    onClick={() => void onInstallAll("selected")}
                    title="Только это устройство"
                  >
                    Установить всё
                    {selectedUpdateCount > 0 ? ` (${selectedUpdateCount})` : ""}
                  </button>
                </div>
              </div>
            )}

            {!selectedDevice && (
              <div className={`status-bar apps-status ${busy ? "is-busy" : ""}`}>
                {busy ? `… ${msg}` : msg}
              </div>
            )}

            <div className="card">
              <div className="row">
                <input
                  className="grow"
                  type="search"
                  placeholder="Фильтр"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
                <label className="check">
                  <input
                    type="checkbox"
                    checked={thirdParty}
                    onChange={(e) => setThirdParty(e.target.checked)}
                  />
                  только сторонние
                </label>
                <label className="check">
                  <input type="checkbox" checked={hidePlay} onChange={(e) => setHidePlay(e.target.checked)} />
                  скрыть Play
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={updatesOnly}
                    onChange={(e) => setUpdatesOnly(e.target.checked)}
                  />
                  только обновления
                </label>
              </div>
            </div>

            <div className="card" style={{ padding: "8px 12px 16px" }}>
              {filteredApps.length === 0 ? (
                <p className="muted">
                  {busy
                    ? msg || "Проверка обновлений…"
                    : "Нет данных в кеше — нажмите «Проверить обновления»"}
                </p>
              ) : (
                <table className="apps-table">
                  <thead>
                    <tr>
                      <th>Приложение</th>
                      <th>На устройстве</th>
                      <th>Стор</th>
                      <th>В сторе</th>
                      <th>Статус</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredApps.map((a) => {
                      const localVer = a.version_name || a.version_code || "—";
                      const storeVer = a.remote_version || "";
                      const storesTip =
                        a.stores_tooltip ||
                        (a.stores && a.stores.length
                          ? a.stores
                              .map((s) => {
                                const name =
                                  s.source === "rustore"
                                    ? "RuStore"
                                    : s.source === "apkpure"
                                      ? "APKPure"
                                      : s.source === "aptoide"
                                        ? "Aptoide"
                                        : s.source === "fdroid"
                                          ? "F-Droid"
                                          : s.source;
                                if (!s.version_name && !s.version_code) {
                                  return `${name}: не найдено`;
                                }
                                const mark = s.chosen ? " ← выбрано" : "";
                                const extra = s.unsupported_reason
                                  ? `, ${s.unsupported_reason}`
                                  : "";
                                return `${name}: ${s.version_name || s.version_code} (code ${s.version_code})${extra}${mark}`;
                              })
                              .join("\n")
                          : storeVer
                            ? `${storeVer}${a.remote_source ? ` (${a.remote_source})` : ""}`
                            : "");
                      const statusText = a.update_ignored
                        ? "не обновлять"
                        : a.install_flag
                          ? a.install_flag_label || "подпись"
                          : a.has_update
                            ? "есть обновление"
                            : a.manual_update
                              ? a.update_badge || "ручной выбор стора"
                              : a.update_badge || "—";
                      const rowClass = a.update_ignored
                        ? "row-update-ignored"
                        : a.install_flag
                          ? "row-install-flag"
                          : a.has_update
                            ? "row-update"
                            : a.manual_update
                              ? "row-manual-update"
                              : undefined;
                      return (
                        <tr
                          key={a.package_name}
                          className={rowClass}
                          title={
                            a.update_ignored
                              ? "Обновления отключены для этого приложения"
                              : a.install_flag
                                ? a.install_flag === "older_sdk"
                                  ? `Автоустановка остановлена: ${(a.install_flag_label || a.install_flag)}${
                                      a.install_flag_source
                                        ? ` (${a.install_flag_source})`
                                        : ""
                                    }. APK новее, чем Android на устройстве — нужен апгрейд ОС или другая версия APK.`
                                  : `Автоустановка: ${(a.install_flag_label || a.install_flag)}${
                                      a.install_flag_source
                                        ? ` (${a.install_flag_source})`
                                        : ""
                                    }. Можно обновить вручную или переустановить.`
                                : a.manual_update
                                  ? "Есть более новая версия в другом сторе — откройте «i» и выберите стор/версию"
                                  : undefined
                          }
                        >
                          <td>
                            <div className="app-name">
                              <AppListIcon
                                packageName={a.package_name}
                                iconUrl={a.icon_url}
                                preferredSource={
                                  a.remote_source || a.install_source || undefined
                                }
                              />
                              <div>
                                <strong title={a.label}>{a.label}</strong>
                                <div
                                  className="muted pkg-line"
                                  style={{ fontSize: "0.75rem" }}
                                  title={a.package_name}
                                >
                                  {a.package_name}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td
                            className="muted"
                            title={a.install_source_tooltip || "Источник неизвестен"}
                          >
                            <div className="cell-clip" title={a.install_source_tooltip || localVer}>
                              {localVer}
                            </div>
                          </td>
                          <td>
                            <select
                              className="store-select"
                              value={a.install_source || ""}
                              disabled={busy}
                              title={a.install_source_tooltip || "Источник установки"}
                              onChange={(e) =>
                                void onChangeInstallSource(a.package_name, e.target.value)
                              }
                            >
                              {STORE_OPTIONS.filter((o) => {
                                if (!o.settingKey) return true;
                                if (o.id === (a.install_source || "")) return true;
                                return Boolean(settings[o.settingKey]);
                              }).map((o) => (
                                <option key={o.id || "auto"} value={o.id}>
                                  {storeSelectOptionLabel(o, a)}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="muted" title={storesTip || undefined}>
                            {storeVer ? (
                              <>
                                <div className="cell-clip">{storeVer}</div>
                                {a.remote_source ? (
                                  <div className="cell-clip" style={{ fontSize: "0.75rem" }}>
                                    {a.remote_source}
                                  </div>
                                ) : null}
                              </>
                            ) : (
                              <span className="cell-clip">—</span>
                            )}
                          </td>
                          <td className="muted">
                            <div className="cell-clip" title={storesTip || a.update_badge || statusText}>
                              {statusText}
                            </div>
                          </td>
                          <td>
                            <div className="row app-actions" style={{ gap: 6, flexWrap: "nowrap" }}>
                              {a.has_update && (
                                <button
                                  type="button"
                                  className="btn btn-accent"
                                  disabled={busy}
                                  onClick={() => void onInstallUpdate(a.package_name)}
                                >
                                  Обновить
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-ghost btn-icon"
                                title="Свойства приложения"
                                disabled={busy || !selectedDevice?.online}
                                onClick={() => void openAppDetails(a.package_name)}
                              >
                                i
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === "store" && (
        <div className="panel store-panel">
          <div className="card">
            <h2>Магазин</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Поиск по включённым сторам (RuStore, APKPure, Aptoide, F-Droid). Живой запрос, не
              полный индекс.
            </p>
            <form className="row" onSubmit={(e) => void onStoreSearch(e)} style={{ gap: 8 }}>
              <input
                type="search"
                value={storeQuery}
                onChange={(e) => setStoreQuery(e.target.value)}
                placeholder="Название или package…"
                style={{ flex: 1, minWidth: 180 }}
                disabled={storeSearching || busy}
              />
              <button
                type="submit"
                className="btn btn-accent"
                disabled={storeSearching || busy || !storeQuery.trim()}
              >
                {storeSearching ? "Поиск…" : "Найти"}
              </button>
            </form>
          </div>

          <div className="card">
            <h2 style={{ marginBottom: 12 }}>Результаты</h2>
            {storeResults.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                {storeSearching ? "Ищем…" : "Введите запрос и нажмите «Найти»"}
              </p>
            ) : (
              <ul className="store-results">
                {storeResults.map((r) => (
                  <li key={r.package_name}>
                    <button
                      type="button"
                      className="store-result-row"
                      onClick={() => void openStoreCard(r.package_name)}
                      disabled={busy}
                    >
                      {r.icon_url ? (
                        <img src={r.icon_url} alt="" className="store-result-icon" />
                      ) : (
                        <div className="store-result-icon placeholder" />
                      )}
                      <div className="store-result-meta">
                        <strong>{r.title || r.package_name}</strong>
                        <span className="muted">{r.package_name}</span>
                        <div className="store-badges">
                          {(r.source_titles || r.sources || []).map((t) => (
                            <span key={t} className="store-badge">
                              {t}
                            </span>
                          ))}
                        </div>
                      </div>
                      {(r.version_name || r.version_code) && (
                        <span className="muted store-result-ver">
                          {r.version_name || r.version_code}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {tab === "settings" && (
        <div className="panel">
          <div className="card">
            <h2>Обновление программы</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Проверка новых сборок на GitHub Releases. Текущая сборка:{" "}
              <code>{settings.build_id || status?.build_id || "dev"}</code>
              {status?.app_frozen ? "" : " (режим исходников — установка ZIP только для EXE)"}.
            </p>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.app_update_check_enabled}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    app_update_check_enabled: e.target.checked,
                  }))
                }
              />
              Проверять новую сборку (раз в час)
            </label>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.app_auto_update_enabled}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    app_auto_update_enabled: e.target.checked,
                  }))
                }
              />
              Автоматически скачивать и устанавливать (только EXE)
            </label>
            <p className="muted">
              Последняя проверка сборки: {formatRunAt(settings.app_update_last_check)}
            </p>
            <div className="row" style={{ marginBottom: 16 }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy || appUpdateBusy}
                onClick={() => void onCheckAppUpdate()}
              >
                Проверить сборку сейчас
              </button>
            </div>

            <h2>Проверка обновлений приложений</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Сервер сам проверяет устройства по расписанию. По интервалу проверка всегда
              полная (force). «Пауза» влияет только на ручные/UI-запросы без force.
              Журнал: data/logs/updates.log на сервере.
            </p>
            <div className="row" style={{ marginBottom: 12 }}>
              <label className="muted" style={{ minWidth: 220 }}>
                Пауза после проверки (часы)
              </label>
              <input
                style={{ width: 100 }}
                type="text"
                inputMode="decimal"
                value={ttlHoursDraft}
                onChange={(e) => setTtlHoursDraft(e.target.value)}
                placeholder="1"
              />
              <span className="muted">для UI без force; лучше 1</span>
            </div>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.bg_updates_enabled}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, bg_updates_enabled: e.target.checked }))
                }
              />
              Включить фоновую проверку на сервере
            </label>
            <div className="row">
              <label className="muted" style={{ minWidth: 220 }}>
                Интервал проверки (часы)
              </label>
              <input
                style={{ width: 100 }}
                type="text"
                inputMode="decimal"
                value={intervalHoursDraft}
                onChange={(e) => setIntervalHoursDraft(e.target.value)}
                placeholder="6"
              />
              <span className="muted">от 0.25 до 168</span>
            </div>
            <p className="muted">
              Последняя проверка: {formatRunAt(settings.bg_updates_last_run)}
            </p>

            <h2 style={{ marginTop: 20 }}>Сторы</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Какие источники опрашивать при проверке обновлений. Нужен хотя бы один.
              F-Droid — только FOSS-приложения.
            </p>
            {(
              [
                ["store_rustore_enabled", "RuStore"],
                ["store_apkpure_enabled", "APKPure"],
                ["store_aptoide_enabled", "Aptoide"],
                ["store_fdroid_enabled", "F-Droid"],
              ] as const
            ).map(([key, label], i, arr) => (
              <label
                key={key}
                className="check"
                style={{ display: "flex", marginBottom: i === arr.length - 1 ? 8 : 8 }}
              >
                <input
                  type="checkbox"
                  checked={settings[key]}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      [key]: e.target.checked,
                    }))
                  }
                />
                {label}
              </label>
            ))}
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.prefer_release_updates}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    prefer_release_updates: e.target.checked,
                  }))
                }
              />
              Только релизные обновления (без beta/dev/rc)
            </label>
            <p className="muted" style={{ marginTop: -4, marginBottom: 12 }}>
              По versionName отсекает pre-release (например 7.2.2-dev.4). Если на
              устройстве уже стоит бета — более новые беты по-прежнему предлагаются.
            </p>

            <h2 style={{ marginTop: 20 }}>Автоустановка</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Перед установкой — принудительная проверка. Если стор выбран — только он;
              при «Авто» ставится из стора, который выбрал проверщик. Конфликт подписи
              помечается в статусе, автоповтор не крутится. Одна лишь «фоновая проверка»
              приложения не ставит.
            </p>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.auto_install_enabled}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, auto_install_enabled: e.target.checked }))
                }
              />
              Включить автоустановку обновлений
            </label>
            {settings.auto_install_enabled ? (
              <>
                <div className="row">
                  <label className="muted" style={{ minWidth: 220 }}>
                    Интервал автоустановки (часы)
                  </label>
                  <input
                    style={{ width: 100 }}
                    type="text"
                    inputMode="decimal"
                    value={autoInstallHoursDraft}
                    onChange={(e) => setAutoInstallHoursDraft(e.target.value)}
                    placeholder="24"
                  />
                  <span className="muted">по умолчанию 24 (сутки)</span>
                </div>
                <p className="muted">
                  Последняя автоустановка: {formatRunAt(settings.auto_install_last_run)}
                </p>
              </>
            ) : (
              <label className="check" style={{ display: "flex", marginBottom: 12 }}>
                <input
                  type="checkbox"
                  checked={settings.notify_updates_enabled}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      notify_updates_enabled: e.target.checked,
                    }))
                  }
                />
                Уведомлять в трее, если есть обновления
              </label>
            )}
            {pendingSerials.length > 0 && (
              <p className="muted">В очереди на связь: {pendingSerials.join(", ")}</p>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void onSaveAppSettings()}
              >
                Сохранить
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onRunBgNow()}
              >
                Проверить все сейчас
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void openActivityLog()}
              >
                Журнал авто
              </button>
            </div>
          </div>

          <div className="card">
            <h2>Хранилище</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              Скачанные APK удаляются автоматически по сроку. Кнопка ниже чистит
              APK, иконки и превью устройств сразу. База данных не трогается.
            </p>
            <div className="row" style={{ marginBottom: 12 }}>
              <label className="muted" style={{ minWidth: 220 }}>
                Хранить APK в кеше (дней)
              </label>
              <input
                style={{ width: 100 }}
                type="text"
                inputMode="decimal"
                value={apkKeepDaysDraft}
                onChange={(e) => setApkKeepDaysDraft(e.target.value)}
                placeholder="2"
              />
              <span className="muted">по умолчанию 2</span>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onSaveAppSettings()}
              >
                Сохранить срок
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onClearFileCache()}
              >
                Удалить кеш приложения
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onUnlockJob()}
                title="Если на всё пишет «уже выполняется другая задача»"
              >
                Сбросить блокировку задач
              </button>
            </div>
          </div>

          <div className="card">
            <h2>Интерфейс</h2>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={settings.open_browser_on_start}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    open_browser_on_start: e.target.checked,
                  }))
                }
              />
              Открывать страницу в браузере при запуске
            </label>
            <p className="muted" style={{ marginTop: 0, marginBottom: 16 }}>
              По умолчанию выключено — программа только в трее. Пункт «Открыть» в
              меню трея всегда доступен.
            </p>
            <h2>Список приложений</h2>
            <label className="check" style={{ display: "flex", marginBottom: 8 }}>
              <input
                type="checkbox"
                checked={thirdParty}
                onChange={(e) => setThirdParty(e.target.checked)}
              />
              Только сторонние (по умолчанию)
            </label>
            <label className="check" style={{ display: "flex", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={hidePlay}
                onChange={(e) => setHidePlay(e.target.checked)}
              />
              Скрывать Google Play (по умолчанию)
            </label>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy}
              onClick={() => void onSaveAppSettings()}
            >
              Сохранить
            </button>
          </div>

          <div className="card">
            <h2>Устройства в реестре</h2>
            <div className="row" style={{ marginBottom: 12 }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onRefresh()}
              >
                Обновить реестр
              </button>
            </div>
            {devices.length === 0 ? (
              <p className="muted">Пока нет устройств в реестре</p>
            ) : (
              <div className="list">
                {devices.map((d) => (
                  <div
                    key={d.serial}
                    className={`device ${selected === d.serial ? "selected" : ""}`}
                    onClick={() => void selectDevice(d.serial)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void selectDevice(d.serial);
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className={`dot ${d.online ? "on" : ""}`} />
                    {d.preview_url ? (
                      <img className="thumb" src={d.preview_url} alt="" />
                    ) : (
                      <div className="thumb placeholder">●</div>
                    )}
                    <div className="meta">
                      <strong>{d.label}</strong>
                      <span>{d.description}</span>
                      <span>{d.channels_text}</span>
                    </div>
                    <div className={`state ${d.online ? "" : "off"}`}>
                      {d.online ? "online" : d.state}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {selected && (
              <div className="row" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() =>
                    void run("Отключение…", async () => {
                      const r = await disconnectDevice(selected);
                      setMsg(r.message);
                      await loadDevices();
                    })
                  }
                >
                  Отключить
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy}
                  onClick={() =>
                    void run("Удаление…", async () => {
                      await removeDevice(selected);
                      setMsg("Удалено");
                      await loadDevices();
                    })
                  }
                >
                  Удалить из реестра
                </button>
              </div>
            )}
          </div>

          <div className="card">
            <h2>ADB</h2>
            <div
              className={`badge ${status?.adb_available ? "ok" : "bad"}`}
              title={status?.adb_message || ""}
              style={{ marginBottom: 12, display: "inline-flex", maxWidth: "100%" }}
            >
              adb {status?.adb_available ? "OK" : "нет"}
              {status?.adb_path ? ` · ${status.adb_path}` : ""}
            </div>
            {status?.adb_message && !status.adb_available ? (
              <p className="muted" style={{ marginTop: 0 }}>
                {status.adb_message}
              </p>
            ) : null}
            <div className="row">
              <input
                className="grow"
                value={adbPath}
                onChange={(e) => setAdbPathLocal(e.target.value)}
                placeholder="Путь к adb.exe"
              />
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onSaveAdb()}
              >
                Сохранить путь
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void onEnsureAdb()}
              >
                Скачать ADB
              </button>
            </div>
          </div>
        </div>
      )}

      {tab !== "apps" && (
        <div className={`status-bar ${busy ? "is-busy" : ""}`}>
          {busy ? `… ${msg}` : msg}
        </div>
      )}

      {deviceInfoOpen && (
        <div
          className="modal-backdrop"
          onClick={() => closeDeviceInfo()}
        >
          <div
            className="modal modal-wide device-info-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="device-info-head">
              <h2 style={{ fontFamily: "var(--brand)" }}>
                Устройство
                {deviceInfo?.device.label || selectedDevice?.label
                  ? ` · ${deviceInfo?.device.label || selectedDevice?.label}`
                  : ""}
              </h2>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={deviceInfoBusy || screenshotBusy}
                onClick={() => closeDeviceInfo()}
              >
                Закрыть
              </button>
            </div>

            <div className="device-info-layout">
              <div className="device-shot">
                {screenshotBusy ? (
                  <div className="device-shot-placeholder">Снимок…</div>
                ) : screenshotUrl ? (
                  <a href={screenshotUrl} target="_blank" rel="noreferrer" title="Открыть полностью">
                    <img src={screenshotUrl} alt="Скриншот экрана" />
                  </a>
                ) : (
                  <div className="device-shot-placeholder">Нет скрина</div>
                )}
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ width: "100%", marginTop: 8 }}
                  disabled={
                    screenshotBusy ||
                    deviceInfoBusy ||
                    !selectedDevice?.online
                  }
                  onClick={() => void onDeviceScreenshot()}
                >
                  {screenshotUrl ? "Обновить экран" : "Сделать скриншот"}
                </button>
              </div>

              <div className="device-info-body">
                {deviceInfoLoading && !deviceInfo ? (
                  <p className="muted">Загрузка…</p>
                ) : null}
                {deviceInfoError ? (
                  <p className="muted" style={{ color: "var(--danger)" }}>
                    {deviceInfoError}
                  </p>
                ) : null}

                {(() => {
                  const d = deviceInfo?.device || selectedDevice;
                  const live = deviceInfo?.live;
                  if (!d) return null;
                  const isTv = Boolean(live?.is_tv);
                  const actions = isTv ? DEVICE_ACTIONS_TV : DEVICE_ACTIONS_MOBILE;
                  const ringerLabel =
                    live?.ringer === "silent"
                      ? "беззвучный"
                      : live?.ringer === "vibrate"
                        ? "вибрация"
                        : live?.ringer === "normal"
                          ? "обычный"
                          : live?.ringer || "—";
                  const batt =
                    live?.battery_pct != null
                      ? `${live.battery_pct}%${
                          live.charging === true ? " · зарядка" : ""
                        }`
                      : "—";
                  const formLabel = isTv
                    ? `ТВ${live?.sdk_int ? ` · API ${live.sdk_int}` : ""}`
                    : live?.sdk_int && live.sdk_int < 29
                      ? `телефон/планшет · API ${live.sdk_int}`
                      : "";
                  return (
                    <>
                      <h3 style={{ margin: "0 0 8px" }}>Свойства</h3>
                      {formLabel ? (
                        <p className="muted" style={{ marginTop: 0 }}>
                          {formLabel}
                        </p>
                      ) : null}
                      <dl className="device-info-dl">
                        <div>
                          <dt>Бренд</dt>
                          <dd>{d.brand || "—"}</dd>
                        </div>
                        <div>
                          <dt>Модель</dt>
                          <dd>{d.model || "—"}</dd>
                        </div>
                        <div className="span-2">
                          <dt>Market name</dt>
                          <dd>{d.market_name || "—"}</dd>
                        </div>
                        <div>
                          <dt>Android</dt>
                          <dd>
                            {d.android_version || "—"}
                            {d.sdk ? ` (API ${d.sdk})` : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>Состояние</dt>
                          <dd>{d.online ? "online" : d.state || "offline"}</dd>
                        </div>
                        <div className="span-2">
                          <dt>Serial</dt>
                          <dd>{d.serial}</dd>
                        </div>
                        <div className="span-2">
                          <dt>UID</dt>
                          <dd>{d.device_uid || "—"}</dd>
                        </div>
                        <div className="span-2">
                          <dt>Каналы</dt>
                          <dd>{d.channels_text || "—"}</dd>
                        </div>
                        <div>
                          <dt>Батарея</dt>
                          <dd>{batt}</dd>
                        </div>
                        {!isTv ? (
                          <div>
                            <dt>Звонок</dt>
                            <dd>{ringerLabel}</dd>
                          </div>
                        ) : (
                          <div>
                            <dt>Тип</dt>
                            <dd>Android TV</dd>
                          </div>
                        )}
                        <div>
                          <dt>Экран</dt>
                          <dd>
                            {live?.screen || "—"}
                            {live?.density ? ` · ${live.density}dpi` : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>IP</dt>
                          <dd>{live?.ip || "—"}</dd>
                        </div>
                      </dl>

                      <h3 style={{ margin: "16px 0 8px" }}>Действия</h3>
                      <div className="device-actions">
                        {actions.map((a) => (
                          <button
                            key={a.action}
                            type="button"
                            className="btn btn-ghost"
                            disabled={
                              deviceInfoBusy ||
                              screenshotBusy ||
                              !selectedDevice?.online
                            }
                            onClick={() => void onDeviceInfoAction(a.action)}
                          >
                            {a.label}
                          </button>
                        ))}
                      </div>
                    </>
                  );
                })()}
              </div>
            </div>
          </div>
        </div>
      )}

      {storeCardOpen && (
        <div className="modal-backdrop" onClick={() => setStoreCardOpen(false)}>
          <div
            className="modal modal-wide store-card-modal"
            onClick={(e) => e.stopPropagation()}
          >
            {!storeCard ? (
              <p className="muted">Загрузка…</p>
            ) : (
              <>
                <div className="store-card-head">
                  {storeCard.icon_url ? (
                    <img src={storeCard.icon_url} alt="" className="store-card-icon" />
                  ) : (
                    <div className="store-card-icon placeholder" />
                  )}
                  <div>
                    <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>
                      {storeCard.title}
                    </h2>
                    <p className="muted" style={{ margin: "4px 0 0" }}>
                      {storeCard.package_name}
                    </p>
                  </div>
                </div>
                <div className="store-source-tabs" style={{ marginTop: 14 }}>
                  {storeCard.sources.map((src, i) => (
                    <button
                      key={src}
                      type="button"
                      className={`chip ${storeCardSource === src ? "active" : ""}`}
                      onClick={() => setStoreCardSource(src)}
                    >
                      {storeCard.source_titles[i] || src}
                    </button>
                  ))}
                </div>
                {(() => {
                  const d = storeCard.by_source[storeCardSource];
                  if (!d) {
                    return <p className="muted">Нет данных по стору</p>;
                  }
                  return (
                    <div className="store-card-body">
                      <p className="muted" style={{ marginBottom: 8 }}>
                        {[d.developer, d.version_name || d.version_code]
                          .filter(Boolean)
                          .join(" · ")}
                        {d.source_title ? ` · ${d.source_title}` : ""}
                      </p>
                      <div className="store-description">
                        {d.description || d.short_description || "Нет описания"}
                      </div>
                    </div>
                  );
                })()}
                <div
                  className="row"
                  style={{ marginTop: 16, justifyContent: "flex-end", gap: 8 }}
                >
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setStoreCardOpen(false)}
                  >
                    Закрыть
                  </button>
                  <button
                    type="button"
                    className="btn btn-accent"
                    disabled={busy || !storeCard.sources.length}
                    onClick={() => openStoreInstallDialog()}
                  >
                    Установить
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {storeInstallOpen && storeCard && (
        <div className="modal-backdrop" onClick={() => setStoreInstallOpen(false)}>
          <div
            className="modal modal-wide"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>
              Установка: {storeCard.title}
            </h2>
            <p className="muted" style={{ marginTop: 6 }}>
              {storeCard.package_name}
            </p>
            <label className="field-label">
              Стор
              <select
                value={storeInstallSource}
                onChange={(e) => {
                  const src = e.target.value;
                  setStoreInstallSource(src);
                  void loadStoreVersions(storeCard.package_name, src);
                }}
                disabled={busy}
              >
                {storeCard.sources.map((src, i) => (
                  <option key={src} value={src}>
                    {storeCard.source_titles[i] || src}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-label">
              Версия
              <select
                value={storeInstallVersionCode === "" ? "" : String(storeInstallVersionCode)}
                onChange={(e) => {
                  const v = e.target.value;
                  setStoreInstallVersionCode(v === "" ? "" : Number(v));
                }}
                disabled={busy || storeVersionsLoading || storeInstallVersions.length === 0}
              >
                {storeVersionsLoading ? (
                  <option value="">Загрузка…</option>
                ) : storeInstallVersions.length === 0 ? (
                  <option value="">Нет версий</option>
                ) : (
                  storeInstallVersions.map((v) => (
                    <option key={v.version_code} value={v.version_code}>
                      {v.label}
                      {v.unsupported_reason ? " (не APK)" : ""}
                    </option>
                  ))
                )}
              </select>
            </label>
            <div className="field-label" style={{ display: "block" }}>
              Устройства
              {online.length === 0 ? (
                <p className="muted" style={{ margin: "8px 0 0" }}>
                  Нет online-устройств
                </p>
              ) : (
                <div className="store-device-list">
                  {online.map((d) => (
                    <label key={d.serial} className="check-row">
                      <input
                        type="checkbox"
                        checked={Boolean(storeInstallTargets[d.serial])}
                        onChange={(e) =>
                          setStoreInstallTargets((prev) => ({
                            ...prev,
                            [d.serial]: e.target.checked,
                          }))
                        }
                      />
                      <span>
                        {d.label || d.serial}
                        <span className="muted"> · {d.serial}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div
              className="row"
              style={{ marginTop: 16, justifyContent: "flex-end", gap: 8 }}
            >
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setStoreInstallOpen(false)}
              >
                Отмена
              </button>
              <button
                type="button"
                className="btn btn-accent"
                disabled={
                  busy ||
                  !storeInstallSource ||
                  storeInstallVersionCode === "" ||
                  !Object.values(storeInstallTargets).some(Boolean)
                }
                onClick={() => void onStoreInstallConfirm()}
              >
                Установить
              </button>
            </div>
          </div>
        </div>
      )}

      {appDetailsOpen && (
        <div className="modal-backdrop" onClick={() => !appDetailsBusy && setAppDetailsOpen(false)}>
          <div
            className="modal modal-wide app-details-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
              <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>Свойства</h2>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={appDetailsBusy}
                onClick={() => setAppDetailsOpen(false)}
              >
                Закрыть
              </button>
            </div>
            {appDetailsLoading ? (
              <p className="muted">Загрузка…</p>
            ) : appDetailsError && !appDetails ? (
              <p className="muted" style={{ color: "var(--danger)" }}>
                {appDetailsError}
              </p>
            ) : appDetails ? (
              <>
                <div className="store-card-head" style={{ marginTop: 12 }}>
                  {appDetails.icon_url ? (
                    <img src={appDetails.icon_url} alt="" className="store-card-icon" />
                  ) : (
                    <div className="store-card-icon placeholder" />
                  )}
                  <div style={{ minWidth: 0 }}>
                    <strong style={{ fontSize: "1.05rem" }}>{appDetails.label}</strong>
                    <div className="muted" style={{ fontSize: "0.8rem", wordBreak: "break-all" }}>
                      {appDetails.package_name}
                    </div>
                    <div className="muted" style={{ fontSize: "0.8rem", marginTop: 4 }}>
                      {appDetails.version_name || "?"}
                      {appDetails.version_code ? ` (${appDetails.version_code})` : ""}
                      {appDetails.install_source_label
                        ? ` · ${appDetails.install_source_label}`
                        : appDetails.installer
                          ? ` · ${appDetails.installer}`
                          : ""}
                    </div>
                  </div>
                </div>

                <h3 style={{ margin: "16px 0 8px" }}>Хранилище</h3>
                <div className="app-details-grid">
                  <div>
                    <span className="muted">APK</span>
                    <strong>{formatBytes(appDetails.storage?.apk_bytes)}</strong>
                  </div>
                  <div>
                    <span className="muted">Данные</span>
                    <strong>{formatBytes(appDetails.storage?.data_bytes)}</strong>
                  </div>
                  <div>
                    <span className="muted">Кеш</span>
                    <strong>{formatBytes(appDetails.storage?.cache_bytes)}</strong>
                  </div>
                  <div>
                    <span className="muted">Всего</span>
                    <strong>{formatBytes(appDetails.storage?.total_bytes)}</strong>
                  </div>
                </div>

                <h3 style={{ margin: "16px 0 8px" }}>Сведения</h3>
                <dl className="app-details-dl">
                  <div>
                    <dt>Установлено</dt>
                    <dd>{appDetails.first_install_time || "—"}</dd>
                  </div>
                  <div>
                    <dt>Обновлено</dt>
                    <dd>{appDetails.last_update_time || "—"}</dd>
                  </div>
                  <div>
                    <dt>Включено</dt>
                    <dd>
                      {appDetails.enabled == null
                        ? "—"
                        : appDetails.enabled
                          ? "да"
                          : "нет"}
                    </dd>
                  </div>
                  <div>
                    <dt>targetSdk</dt>
                    <dd>{appDetails.target_sdk ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>minSdk</dt>
                    <dd>{appDetails.min_sdk ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>UID</dt>
                    <dd>{appDetails.uid ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Installer</dt>
                    <dd className="cell-clip" title={appDetails.installer || undefined}>
                      {appDetails.installer || "—"}
                    </dd>
                  </div>
                </dl>
                {(appDetails.notes || []).length > 0 && (
                  <p className="muted" style={{ fontSize: "0.8rem", marginTop: 8 }}>
                    {(appDetails.notes || []).join(" · ")}
                  </p>
                )}

                <h3 style={{ margin: "16px 0 8px" }}>Обновить из стора</h3>
                <p className="muted" style={{ margin: "0 0 8px", fontSize: "0.8rem" }}>
                  Выберите стор и версию и установите на текущее устройство
                  {selectedDevice ? ` (${selectedDevice.label || selected})` : ""}.
                </p>
                {detailsStoreLoading ? (
                  <p className="muted">Поиск в сторах…</p>
                ) : !detailsStoreCard || detailsStoreCard.sources.length === 0 ? (
                  <p className="muted">Пакет не найден во включённых сторах</p>
                ) : (
                  <div className="details-store-update">
                    <label className="field-label">
                      Стор
                      <select
                        value={detailsStoreSource}
                        disabled={appDetailsBusy || busy}
                        onChange={(e) => {
                          const src = e.target.value;
                          setDetailsStoreSource(src);
                          void loadDetailsStoreVersions(appDetailsPkg, src);
                        }}
                      >
                        {detailsStoreCard.sources.map((src, i) => (
                          <option key={src} value={src}>
                            {detailsStoreCard.source_titles[i] || src}
                            {detailsStoreCard.by_source[src]?.version_name
                              ? ` · ${detailsStoreCard.by_source[src].version_name}`
                              : ""}
                            {detailsStoreCard.by_source[src]?.unsupported_reason
                              ? " (не APK)"
                              : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field-label">
                      Версия
                      <select
                        value={
                          detailsStoreVersionCode === ""
                            ? ""
                            : String(detailsStoreVersionCode)
                        }
                        disabled={
                          appDetailsBusy ||
                          busy ||
                          detailsVersionsLoading ||
                          detailsStoreVersions.length === 0
                        }
                        onChange={(e) => {
                          const v = e.target.value;
                          setDetailsStoreVersionCode(v === "" ? "" : Number(v));
                        }}
                      >
                        {detailsVersionsLoading ? (
                          <option value="">Загрузка…</option>
                        ) : detailsStoreVersions.length === 0 ? (
                          <option value="">Нет версий</option>
                        ) : (
                          detailsStoreVersions.map((v) => (
                            <option key={v.version_code} value={v.version_code}>
                              {v.label}
                              {v.unsupported_reason ? " (не APK)" : ""}
                            </option>
                          ))
                        )}
                      </select>
                    </label>
                    <div className="row" style={{ marginTop: 10, gap: 8 }}>
                      <button
                        type="button"
                        className="btn btn-accent"
                        disabled={
                          appDetailsBusy ||
                          busy ||
                          !detailsStoreSource ||
                          detailsStoreVersionCode === "" ||
                          !selectedDevice?.online
                        }
                        onClick={() => void onDetailsStoreInstall()}
                      >
                        Установить на устройство
                      </button>
                    </div>
                  </div>
                )}

                <h3 style={{ margin: "16px 0 8px" }}>Действия</h3>
                <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                  <button
                    type="button"
                    className={`btn btn-ghost${appDetailsIgnored ? " btn-ignore-on" : ""}`}
                    title={
                      appDetailsIgnored
                        ? "Снова разрешить обновления"
                        : "Не обновлять это приложение"
                    }
                    disabled={appDetailsBusy || busy}
                    onClick={() =>
                      void onToggleUpdateIgnore(appDetailsPkg, !appDetailsIgnored)
                    }
                  >
                    {appDetailsIgnored ? "Разрешить обновления" : "Не обновлять"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={appDetailsBusy}
                    onClick={() => void onAppDetailsAction("force_stop")}
                  >
                    Остановить
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={appDetailsBusy}
                    onClick={() => void onAppDetailsAction("clear_cache")}
                  >
                    Очистить кеш
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={appDetailsBusy}
                    onClick={() => void onAppDetailsAction("clear_data")}
                  >
                    Очистить данные
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={appDetailsBusy}
                    onClick={() => void onAppDetailsAction("open_settings")}
                  >
                    Настройки на телефоне
                  </button>
                  <button
                    type="button"
                    className="btn"
                    style={{ borderColor: "var(--danger)", color: "var(--danger)" }}
                    disabled={appDetailsBusy}
                    onClick={() => void onAppDetailsAction("uninstall")}
                  >
                    Удалить
                  </button>
                </div>

                <h3 style={{ margin: "16px 0 8px" }}>Разрешения</h3>
                <p className="muted" style={{ margin: "0 0 8px", fontSize: "0.8rem" }}>
                  Grant/revoke через ADB; часть прав без root не меняется. Полный экран — «Настройки
                  на телефоне».
                </p>
                {(appDetails.permissions || []).length === 0 ? (
                  <p className="muted">Список разрешений пуст или недоступен</p>
                ) : (
                  <ul className="app-perm-list">
                    {appDetails.permissions.map((p) => (
                      <li key={p.name}>
                        <label className="check-row" title={p.name}>
                          <input
                            type="checkbox"
                            checked={p.granted}
                            disabled={appDetailsBusy}
                            onChange={(e) =>
                              void onTogglePermission(p.name, e.target.checked)
                            }
                          />
                          <span>
                            {shortPermission(p.name)}
                            {p.dangerous ? (
                              <span className="muted"> · dangerous</span>
                            ) : null}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
                {appDetailsError ? (
                  <p
                    className="muted"
                    style={{ color: "var(--danger)", marginTop: 10, marginBottom: 0 }}
                  >
                    {appDetailsError}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      )}

      {activityLogOpen && (
        <div className="modal-backdrop" onClick={() => setActivityLogOpen(false)}>
          <div
            className="modal modal-wide activity-log-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>
              Журнал автопроверки и автоустановки
            </h2>
            <p className="muted" style={{ marginTop: 8 }}>
              Фоновые запуски: что проверяли, что пытались поставить, на какое устройство
              и чем закончилось.
            </p>
            {activityLogBusy && activityLog.length === 0 ? (
              <p className="muted">Загрузка…</p>
            ) : activityLog.length === 0 ? (
              <p className="muted">Пока пусто — записи появятся после фоновых задач</p>
            ) : (
              <ul className="activity-log-list">
                {activityLog.map((row) => {
                  const isInstall = row.event === "install";
                  const title = isInstall
                    ? row.app_label || row.package_name || row.message || "Установка"
                    : row.message || activityKindLabel(row.kind);
                  const device = row.device_label || row.serial;
                  return (
                    <li
                      key={row.id}
                      className={`activity-log-item status-${row.status || "info"}`}
                    >
                      <div className="activity-log-main">
                        <div className="activity-log-meta">
                          <span className="muted">{formatTs(row.created_at)}</span>
                          <span className="activity-kind">{activityKindLabel(row.kind)}</span>
                          <span className={`install-pill status-${row.status || "info"}`}>
                            {activityStatusLabel(row.status)}
                          </span>
                        </div>
                        <strong>{title}</strong>
                        {isInstall && device ? (
                          <span className="muted">Устройство: {device}</span>
                        ) : null}
                        {isInstall && row.package_name && row.app_label !== row.package_name ? (
                          <span className="muted">{row.package_name}</span>
                        ) : null}
                        {row.detail ? (
                          <span className="muted activity-log-detail">{row.detail}</span>
                        ) : null}
                        {!isInstall && row.serial ? (
                          <span className="muted">
                            {row.device_label || row.serial}
                            {row.package_name ? ` · ${row.package_name}` : ""}
                          </span>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end", gap: 8 }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={activityLogBusy}
                onClick={() => void loadActivityLog()}
              >
                Обновить
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={activityLogBusy || activityLog.length === 0}
                onClick={() => void onClearActivityLog()}
              >
                Очистить
              </button>
              <button
                type="button"
                className="btn btn-accent"
                onClick={() => setActivityLogOpen(false)}
              >
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

      {installProgressOpen && (
        <div className="modal-backdrop">
          <div className="modal modal-wide install-progress-modal">
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>{installProgressTitle}</h2>
            <p className="muted" style={{ marginTop: 8 }}>
              {installProgressDone
                ? "Завершено"
                : "Идёт установка — можно свернуть; не закрывайте всю программу"}
            </p>
            {installProgressItems.length === 0 ? (
              <p className="muted">
                {busy
                  ? "Подготовка…"
                  : installProgressDone
                    ? "Задачи не поступили — смотрите статус внизу"
                    : "Подключение…"}
              </p>
            ) : (
              <ul className="install-progress-list">
                {installProgressItems.map((it) => (
                  <li key={it.id} className={`install-progress-item status-${it.status}`}>
                    <div className="install-progress-main">
                      <strong>{it.app_label || it.package}</strong>
                      <span className="muted">{it.device_label || it.serial}</span>
                    </div>
                    <div className="install-progress-state">
                      <span className={`install-pill status-${it.status}`}>
                        {installStatusLabel(it.status)}
                      </span>
                      {it.status === "fail" && it.error_title ? (
                        <div className="install-error-block">
                          <strong>{it.error_title}</strong>
                          {it.error_hint ? (
                            <p className="muted install-error-hint">{it.error_hint}</p>
                          ) : null}
                          <span className="muted install-progress-detail">{it.detail}</span>
                        </div>
                      ) : (
                        <span className="muted install-progress-detail">{it.detail}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end", gap: 8 }}>
              {installProgressDone &&
                installProgressItems
                  .filter((it) => it.status === "fail")
                  .map((failed) => {
                    const actions = failed.actions || [];
                    const wantDowngrade =
                      actions.includes("downgrade") ||
                      isVersionDowngrade(failed.detail || "", failed.error_code);
                    const wantReinstall =
                      actions.includes("reinstall") ||
                      isSignatureMismatch(failed.detail || "", failed.error_code);
                    return (
                      <span key={`act-${failed.id}`} className="row" style={{ gap: 8 }}>
                        {wantDowngrade ? (
                          <button
                            type="button"
                            className="btn btn-accent"
                            disabled={busy}
                            onClick={() => void onDowngradeInstall(failed)}
                          >
                            Установить с даунгрейдом
                          </button>
                        ) : null}
                        {wantReinstall && failed.package ? (
                          <button
                            type="button"
                            className="btn btn-accent"
                            disabled={busy}
                            onClick={() => void onReinstallUpdate(failed.package)}
                          >
                            Переустановить
                          </button>
                        ) : null}
                      </span>
                    );
                  })}
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setInstallProgressOpen(false)}
              >
                {installProgressDone ? "Закрыть" : "Свернуть"}
              </button>
            </div>
          </div>
        </div>
      )}

      {pairModalOpen && (
        <div className="modal-backdrop">
          <div className="modal modal-wide">
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>Сопряжение</h2>
            <div className="row" style={{ marginTop: 12 }}>
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void onDiscover()}
              >
                Скан сети
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => void openQr()}
              >
                Сопрячь по QR
              </button>
            </div>

            <h3 style={{ marginBottom: 8 }}>Сеть</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Ищет Wireless debugging (mDNS) и классический ADB на порту 5555 (ТВ /
              старый tcpip).
            </p>
            {discovered.length === 0 ? (
              <p className="muted">Пока пусто — нажмите «Скан сети»</p>
            ) : (
              <div className="list">
                {discovered.map((ep) => {
                  const kindLabel =
                    ep.kind === "legacy"
                      ? "классический ADB"
                      : ep.kind === "pairing"
                        ? "сопряжение"
                        : ep.kind === "connect"
                          ? "wireless"
                          : ep.kind;
                  return (
                  <div key={ep.key} className="device" style={{ cursor: "default" }}>
                    <div className="dot on" />
                    <div className="meta" style={{ gridColumn: "2 / 4" }}>
                      <strong>
                        {ep.label ? ep.label : `${kindLabel} · ${ep.host_port}`}
                      </strong>
                      <span>
                        {ep.label
                          ? `${kindLabel} · ${ep.host_port} · ${ep.name}`
                          : `${kindLabel} · ${ep.name}`}
                      </span>
                    </div>
                    <div className="row">
                      {ep.kind === "pairing" ? (
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={busy}
                          onClick={() => {
                            const code = window.prompt("6-значный код с телефона") || "";
                            if (!code.trim()) return;
                            void run("Сопряжение…", async () => {
                              const r = await discoveredAction(ep, code.trim());
                              setMsg(r.message);
                              await loadDevices();
                            });
                          }}
                        >
                          Pair
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={busy}
                          onClick={() =>
                            void run("Подключение…", async () => {
                              const r = await discoveredAction(ep);
                              setMsg(r.message);
                              await loadDevices();
                            })
                          }
                        >
                          Connect
                        </button>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>
            )}

            <h3 style={{ marginTop: 16, marginBottom: 8 }}>Вручную</h3>
            <div className="row">
              <input
                className="grow"
                placeholder="Pair IP"
                value={pairIp}
                onChange={(e) => setPairIp(e.target.value)}
              />
              <input
                style={{ width: 90 }}
                placeholder="port"
                value={pairPort}
                onChange={(e) => setPairPort(e.target.value)}
              />
              <input
                style={{ width: 100 }}
                placeholder="code"
                value={pairCode}
                onChange={(e) => setPairCode(e.target.value)}
              />
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() =>
                  void run("Pair…", async () => {
                    const r = await pairDevice(pairIp, pairPort, pairCode);
                    setMsg(r.message);
                    await loadDevices();
                  })
                }
              >
                Pair
              </button>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input
                className="grow"
                placeholder="Connect IP"
                value={connIp}
                onChange={(e) => setConnIp(e.target.value)}
              />
              <input
                style={{ width: 90 }}
                placeholder="port"
                value={connPort}
                onChange={(e) => setConnPort(e.target.value)}
              />
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() =>
                  void run("Connect…", async () => {
                    const r = await connectDevice(connIp, connPort);
                    setMsg(r.message);
                    await loadDevices();
                  })
                }
              >
                Connect
              </button>
            </div>

            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setPairModalOpen(false)}
              >
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

      {installModalOpen && (
        <div className="modal-backdrop">
          <div className="modal modal-wide">
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>Установка APK</h2>
            <div className="row" style={{ marginTop: 12 }}>
              <input
                type="file"
                accept=".apk"
                onChange={(e) => setApkFile(e.target.files?.[0] ?? null)}
              />
            </div>
            {apkFile && <p className="muted">{apkFile.name}</p>}

            <h3 style={{ marginBottom: 8 }}>Цели</h3>
            {online.length === 0 ? (
              <p className="muted">Нет online-устройств</p>
            ) : (
              <div className="list">
                {online.map((d) => (
                  <label key={d.serial} className="check">
                    <input
                      type="checkbox"
                      checked={!!installTargets[d.serial]}
                      onChange={(e) =>
                        setInstallTargets((prev) => ({
                          ...prev,
                          [d.serial]: e.target.checked,
                        }))
                      }
                    />
                    {d.label} · {d.channels_text}
                  </label>
                ))}
              </div>
            )}

            <h3 style={{ marginTop: 12, marginBottom: 8 }}>Лог</h3>
            <div className="log">{log || "—"}</div>

            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void onInstallApk()}
              >
                Установить
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setInstallModalOpen(false)}
              >
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

      {qrOpen && (
        <div className="modal-backdrop">
          <div className="modal">
            <h2 style={{ fontFamily: "var(--brand)", margin: 0 }}>Сопряжение по QR</h2>
            <p className="muted">
              На телефоне: Беспроводная отладка → Сопряжение устройства с помощью QR‑кода
            </p>
            {qrPng && <img src={`data:image/png;base64,${qrPng}`} alt="QR" />}
            <p className="muted">{qrStatus}</p>
            <button type="button" className="btn btn-ghost" onClick={() => void closeQr()}>
              Закрыть
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
