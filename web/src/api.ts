export type Device = {
  serial: string;
  label: string;
  description: string;
  channels_text: string;
  state: string;
  online: boolean;
  model: string;
  brand: string;
  market_name: string;
  android_version: string;
  sdk: string;
  preview_url: string | null;
  transports: string[];
  device_uid: string;
};

export type Discover = {
  key: string;
  kind: string;
  name: string;
  ip: string;
  port: number;
  host_port: string;
  label?: string;
};

export type StoreHit = {
  source: string;
  version_name: string;
  version_code: number;
  unsupported_reason: string;
  chosen: boolean;
};

export type AppRow = {
  package_name: string;
  label: string;
  version_name: string;
  version_code: string;
  installer: string;
  icon_url: string | null;
  update_status: string;
  update_badge: string;
  has_update: boolean;
  manual_update?: boolean;
  update_ignored?: boolean;
  install_flag?: string;
  install_flag_label?: string;
  install_flag_source?: string;
  remote_version: string;
  remote_source: string;
  stores?: StoreHit[];
  stores_tooltip?: string;
  install_source?: string;
  install_source_label?: string;
  install_source_tooltip?: string;
};

export type Status = {
  adb_available: boolean;
  adb_path: string;
  adb_message: string;
  job_running?: boolean;
  job_label?: string;
  job_age_sec?: number;
  build_id?: string;
  latest_build?: string;
  update_available?: boolean;
  app_frozen?: boolean;
  github_repo?: string;
};

export type AppSettings = {
  bg_updates_enabled: boolean;
  bg_updates_interval_hours: number;
  update_check_ttl_hours: number;
  auto_install_enabled: boolean;
  auto_install_interval_hours: number;
  notify_updates_enabled: boolean;
  store_rustore_enabled: boolean;
  store_apkpure_enabled: boolean;
  store_aptoide_enabled: boolean;
  store_fdroid_enabled: boolean;
  prefer_release_updates: boolean;
  open_browser_on_start: boolean;
  hide_play_store: boolean;
  third_party_only: boolean;
  apk_cache_keep_days: number;
  bg_updates_last_run: string;
  auto_install_last_run: string;
  app_update_check_enabled: boolean;
  app_auto_update_enabled: boolean;
  app_update_check_interval_hours: number;
  app_update_last_check: string;
  build_id: string;
};

export type AppUpdateStatus = {
  ok: boolean;
  local_build: string;
  latest_build: string;
  update_available: boolean;
  download_url: string;
  message: string;
  frozen: boolean;
  github_repo: string;
  last_check: string;
};

export type UpdatesCache = {
  devices: Record<string, AppRow[]>;
  update_counts: Record<string, number>;
  pending_serials: string[];
  bg_updates_last_run: string;
  auto_install_last_run: string;
};

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function getStatus() {
  return json<Status>("/api/status");
}

export function setAdbPath(path: string) {
  return json<{ message: string; ok: boolean }>("/api/adb/path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export function ensureAdb() {
  return json<{ message: string; ok: boolean }>("/api/adb/ensure", { method: "POST" });
}

export function getSettings() {
  return json<AppSettings>("/api/settings");
}

export function saveSettings(patch: Partial<AppSettings>) {
  return json<AppSettings>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export type BgActivityEntry = {
  id: number;
  created_at: number;
  kind: string;
  event: string;
  status: string;
  message: string;
  package_name: string;
  app_label: string;
  serial: string;
  device_label: string;
  detail: string;
};

export type BgActivityLog = {
  entries: BgActivityEntry[];
};

export function getActivityLog(limit = 200) {
  return json<BgActivityLog>(`/api/settings/activity-log?limit=${limit}`);
}

export function clearActivityLog() {
  return json<{ message: string; ok: boolean }>("/api/settings/activity-log", {
    method: "DELETE",
  });
}

export type ClearCacheResult = {
  ok: boolean;
  message: string;
  files: number;
  total_bytes: number;
  by_kind: Record<string, { files: number; bytes: number }>;
};

export function clearFileCache() {
  return json<ClearCacheResult>("/api/settings/clear-cache", { method: "POST" });
}

export function unlockJob() {
  return json<{ message: string; ok: boolean }>("/api/settings/unlock-job", {
    method: "POST",
  });
}

export function getAppUpdateStatus() {
  return json<AppUpdateStatus>("/api/app-update/status");
}

export function checkAppUpdate() {
  return json<AppUpdateStatus>("/api/app-update/check", { method: "POST" });
}

export function applyAppUpdate() {
  return json<{ ok: boolean; message: string; restarting: boolean }>(
    "/api/app-update/apply",
    { method: "POST" },
  );
}

export function dismissAppUpdate() {
  return json<{ message: string; ok: boolean }>("/api/app-update/dismiss", {
    method: "POST",
  });
}

export function listDevices() {
  return json<Device[]>("/api/devices");
}

export function refreshDevices() {
  return json<Device[]>("/api/devices/refresh", { method: "POST" });
}

export function discoverNetwork() {
  return json<Discover[]>("/api/devices/discover", { method: "POST" });
}

export function pairDevice(ip: string, port: string, code: string) {
  return json<{ message: string }>("/api/devices/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip, port, code }),
  });
}

export function connectDevice(ip: string, port: string) {
  return json<{ message: string }>("/api/devices/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip, port }),
  });
}

export function disconnectDevice(serial: string) {
  return json<{ message: string }>("/api/devices/disconnect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
  });
}

export function removeDevice(serial: string) {
  return json<{ message: string }>("/api/devices/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
  });
}

export function discoveredAction(ep: Discover, code = "") {
  return json<{ message: string }>("/api/devices/discovered/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      key: ep.key,
      kind: ep.kind,
      ip: ep.ip,
      port: ep.port,
      code,
    }),
  });
}

export function fetchProfile(serial: string) {
  return json<Device>("/api/devices/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
  });
}

export type DeviceLive = {
  battery_pct: number | null;
  charging: boolean | null;
  screen: string;
  density: string;
  ringer: string;
  ip: string;
  form_factor?: string;
  sdk_int?: number;
  is_tv?: boolean;
};

export type DeviceInfo = {
  device: Device;
  live: DeviceLive;
  screenshot_url?: string;
};

export type DeviceActionName =
  | "wake"
  | "play_timer"
  | "play_alarm"
  | "mute"
  | "unmute"
  | "volume_up"
  | "volume_down"
  | "stay_awake_on"
  | "stay_awake_off";

export function fetchDeviceInfo(serial: string) {
  const q = new URLSearchParams({ serial });
  return json<DeviceInfo>(`/api/devices/info?${q}`);
}

export function deviceAction(serial: string, action: DeviceActionName) {
  return json<{ ok: boolean; message: string }>("/api/devices/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial, action }),
  });
}

export function deviceScreenshot(serial: string) {
  return json<{ ok: boolean; message: string; url: string }>("/api/devices/screenshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
  });
}

export function deviceScrcpy(serial: string) {
  return json<{ ok: boolean; message: string }>("/api/devices/scrcpy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
  });
}

export function listApps(serial: string, thirdPartyOnly = true) {
  const q = new URLSearchParams({
    serial,
    third_party_only: String(thirdPartyOnly),
  });
  return json<AppRow[]>(`/api/apps?${q}`);
}

export type AppPermission = {
  name: string;
  granted: boolean;
  dangerous: boolean;
};

export type AppDetails = {
  package_name: string;
  label: string;
  version_name: string;
  version_code: string;
  installer: string;
  enabled: boolean | null;
  target_sdk: number | null;
  min_sdk: number | null;
  uid: number | null;
  first_install_time: string;
  last_update_time: string;
  apk_path: string;
  data_dir: string;
  icon_url?: string | null;
  install_source?: string;
  install_source_label?: string;
  install_source_tooltip?: string;
  storage: {
    apk_bytes: number | null;
    data_bytes: number | null;
    cache_bytes: number | null;
    total_bytes: number | null;
  };
  permissions: AppPermission[];
  notes: string[];
};

export type AppActionResult = {
  ok: boolean;
  message: string;
  details?: AppDetails | null;
};

export function getAppDetails(serial: string, packageName: string) {
  const q = new URLSearchParams({ serial, package: packageName });
  return json<AppDetails>(`/api/apps/details?${q}`);
}

export function appAction(
  serial: string,
  packageName: string,
  action: "force_stop" | "clear_cache" | "clear_data" | "uninstall" | "open_settings",
) {
  return json<AppActionResult>("/api/apps/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      serial,
      package_name: packageName,
      action,
    }),
  });
}

export function setAppPermission(
  serial: string,
  packageName: string,
  permission: string,
  granted: boolean,
) {
  return json<AppActionResult>("/api/apps/permission", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      serial,
      package_name: packageName,
      permission,
      granted,
    }),
  });
}

export function qrStart() {
  return json<{ png_base64: string; service_name: string; message: string }>("/api/qr/start", {
    method: "POST",
  });
}

export function qrCancel() {
  return json<{ message: string }>("/api/qr/cancel", { method: "POST" });
}

export type SseEvent = Record<string, unknown> & { type: string };

export async function readSse(
  response: Response,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      onEvent(JSON.parse(line.slice(6)) as SseEvent);
    }
  }
}

export async function qrWait(onEvent: (ev: SseEvent) => void) {
  const res = await fetch("/api/qr/wait");
  await readSse(res, onEvent);
}

export function getUpdatesCache() {
  return json<UpdatesCache>("/api/updates/cache");
}

export async function checkUpdates(
  serial: string,
  hidePlayStore: boolean,
  onEvent: (ev: SseEvent) => void,
  opts?: { force?: boolean },
) {
  const res = await fetch("/api/updates/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      serial,
      hide_play_store: hidePlayStore,
      force: Boolean(opts?.force),
    }),
  });
  await readSse(res, onEvent);
}

export async function checkUpdatesBatch(
  onEvent: (ev: SseEvent) => void,
  opts?: { force?: boolean; serials?: string[]; hidePlayStore?: boolean },
) {
  const res = await fetch("/api/updates/check-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      force: Boolean(opts?.force),
      serials: opts?.serials ?? null,
      hide_play_store: opts?.hidePlayStore ?? true,
    }),
  });
  await readSse(res, onEvent);
}

export async function installUpdate(
  serial: string,
  packageName: string,
  onEvent: (ev: SseEvent) => void,
) {
  const res = await fetch("/api/updates/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial, package_name: packageName }),
  });
  await readSse(res, onEvent);
}

export async function reinstallUpdate(
  serial: string,
  packageName: string,
  onEvent: (ev: SseEvent) => void,
) {
  const res = await fetch("/api/updates/reinstall", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial, package_name: packageName }),
  });
  await readSse(res, onEvent);
}

export function setAppSource(serial: string, packageName: string, source: string) {
  return json<{ message: string; ok: boolean }>("/api/apps/source", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      serial,
      package_name: packageName,
      source,
    }),
  });
}

export function setUpdateIgnore(
  serial: string,
  packageName: string,
  ignored: boolean,
) {
  return json<{ message: string; ok: boolean; app: AppRow | null }>(
    "/api/apps/update-ignore",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serial,
        package_name: packageName,
        ignored,
      }),
    },
  );
}

export async function installAllUpdates(
  onEvent: (ev: SseEvent) => void,
  opts?: { serials?: string[] },
) {
  const res = await fetch("/api/updates/install-all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serials: opts?.serials ?? null }),
  });
  await readSse(res, onEvent);
}

export async function installApk(
  file: File,
  serials: string[],
  onEvent: (ev: SseEvent) => void,
) {
  const form = new FormData();
  form.append("apk", file);
  form.append("serials", JSON.stringify(serials));
  const res = await fetch("/api/install", { method: "POST", body: form });
  await readSse(res, onEvent);
}

export async function installCachedApk(
  body: {
    serial: string;
    package_name?: string;
    apk_path: string;
    allow_downgrade?: boolean;
  },
  onEvent: (ev: SseEvent) => void,
) {
  const res = await fetch("/api/install/cached", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await readSse(res, onEvent);
}

export type StoreSearchResult = {
  package_name: string;
  title: string;
  icon_url: string;
  developer: string;
  short_description: string;
  sources: string[];
  source_titles: string[];
  version_name: string;
  version_code: number;
};

export type StoreAppDetails = {
  package_name: string;
  title: string;
  source: string;
  source_title: string;
  description: string;
  short_description: string;
  developer: string;
  icon_url: string;
  version_name: string;
  version_code: number;
  app_id: string;
  download_url: string;
  unsupported_reason: string;
};

export type StoreAppCard = {
  package_name: string;
  title: string;
  icon_url: string;
  sources: string[];
  source_titles: string[];
  by_source: Record<string, StoreAppDetails>;
};

export type StoreVersion = {
  version_name: string;
  version_code: number;
  source: string;
  source_title: string;
  download_url: string;
  app_id: string;
  unsupported_reason: string;
  label: string;
};

export function storeSearch(q: string, limit = 20) {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return json<{ query: string; results: StoreSearchResult[] }>(
    `/api/store/search?${params}`,
  );
}

export function storeApp(packageName: string) {
  const params = new URLSearchParams({ package: packageName });
  return json<StoreAppCard>(`/api/store/app?${params}`);
}

export function storeVersions(packageName: string, source: string, limit = 20) {
  const params = new URLSearchParams({
    package: packageName,
    source,
    limit: String(limit),
  });
  return json<{
    package_name: string;
    source: string;
    source_title: string;
    versions: StoreVersion[];
  }>(`/api/store/versions?${params}`);
}

export async function storeInstall(
  body: {
    package_name: string;
    source: string;
    version_code?: number | null;
    serials: string[];
  },
  onEvent: (ev: SseEvent) => void,
) {
  const res = await fetch("/api/store/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await readSse(res, onEvent);
}
