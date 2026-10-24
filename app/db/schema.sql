-- MVP schema + placeholders for phase 2

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT NOT NULL UNIQUE,
    address TEXT,
    model TEXT,
    alias TEXT,
    last_seen TEXT,
    manufacturer TEXT,
    brand TEXT,
    android_version TEXT,
    sdk TEXT,
    market_name TEXT,
    preview_path TEXT,
    device_uid TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Phase 2: APK download cache metadata
CREATE TABLE IF NOT EXISTS apk_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    version_code INTEGER NOT NULL,
    version_name TEXT,
    source TEXT,
    file_path TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(package_name, version_code)
);

-- Phase 2: last-launch / usage hints
CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT NOT NULL,
    package_name TEXT NOT NULL,
    last_used_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(device_serial, package_name)
);

CREATE TABLE IF NOT EXISTS install_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT NOT NULL,
    package_name TEXT,
    apk_path TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);

-- Offline / failed jobs to retry when device comes back online
CREATE TABLE IF NOT EXISTS pending_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT NOT NULL,
    kind TEXT NOT NULL,
    package_name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    UNIQUE(serial, kind, package_name)
);

-- Survives process restart: last inventory + update check per device
CREATE TABLE IF NOT EXISTS device_ui_cache (
    serial TEXT PRIMARY KEY,
    apps_json TEXT NOT NULL DEFAULT '[]',
    updates_json TEXT NOT NULL DEFAULT '{}',
    checked_at REAL NOT NULL DEFAULT 0
);

-- Shared store lookup results across devices
CREATE TABLE IF NOT EXISTS remotes_ui_cache (
    package_name TEXT PRIMARY KEY,
    best_json TEXT,
    stores_json TEXT NOT NULL DEFAULT '[]',
    fetched_at REAL NOT NULL DEFAULT 0
);

-- Preferred store source per device + package (survives PC DB via on-device mirror)
CREATE TABLE IF NOT EXISTS package_sources (
    device_serial TEXT NOT NULL,
    package_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    installer TEXT NOT NULL DEFAULT '',
    manual INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_serial, package_name)
);

-- Packages the user does not want auto/manual «Обновить» for (per device)
CREATE TABLE IF NOT EXISTS update_ignores (
    device_serial TEXT NOT NULL,
    package_name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_serial, package_name)
);

-- Auto-install notes (e.g. signature_mismatch) — skip retry until cleared
CREATE TABLE IF NOT EXISTS package_install_flags (
    device_serial TEXT NOT NULL,
    package_name TEXT NOT NULL,
    flag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_serial, package_name)
);

-- Background auto-check / auto-install journal (UI settings modal)
CREATE TABLE IF NOT EXISTS bg_activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,
    event TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    package_name TEXT NOT NULL DEFAULT '',
    app_label TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    device_label TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_bg_activity_created
    ON bg_activity_log(created_at DESC, id DESC);
