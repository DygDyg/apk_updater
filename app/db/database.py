from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.paths import BUNDLE_DIR, DB_PATH, ensure_data_dirs


def _load_schema() -> str:
    """DDL for migrate(); file next to module, PyInstaller datas, or embedded fallback."""
    candidates = (
        Path(__file__).with_name("schema.sql"),
        BUNDLE_DIR / "app" / "db" / "schema.sql",
    )
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    try:
        from app.db._schema_embed import SCHEMA_SQL

        if SCHEMA_SQL.strip():
            return SCHEMA_SQL
    except Exception:  # noqa: BLE001
        pass
    tried = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"schema.sql not found (tried: {tried}; embed missing). Rebuild with build.bat."
    )


_SCHEMA = _load_schema()

# Backoff minutes by attempt index (capped)
_PENDING_BACKOFF_MIN = (1, 5, 15, 60)


class Database:
    def __init__(self, path: Path | None = None) -> None:
        ensure_data_dirs()
        self.path = path or DB_PATH
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._ensure_device_columns()
        self._ensure_install_flag_columns()
        self._conn.commit()

    def _ensure_device_columns(self) -> None:
        """Add profile columns on existing DBs created before schema expansion."""
        rows = self._conn.execute("PRAGMA table_info(devices)").fetchall()
        existing = {r[1] for r in rows}
        extras = {
            "manufacturer": "TEXT",
            "brand": "TEXT",
            "android_version": "TEXT",
            "sdk": "TEXT",
            "market_name": "TEXT",
            "preview_path": "TEXT",
            "device_uid": "TEXT",
        }
        for col, typ in extras.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {typ}")

    def _ensure_install_flag_columns(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(package_install_flags)").fetchall()
        if not rows:
            return
        existing = {r[1] for r in rows}
        if "version_code" not in existing:
            self._conn.execute(
                "ALTER TABLE package_install_flags ADD COLUMN version_code INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    def executemany(self, sql: str, seq: list[tuple]) -> sqlite3.Cursor:
        cur = self._conn.executemany(sql, seq)
        self._conn.commit()
        return cur

    def fetchone(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return list(self._conn.execute(sql, params).fetchall())

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        return row["value"]

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def enqueue_pending(
        self,
        serial: str,
        kind: str,
        *,
        package_name: str = "",
        reason: str = "",
    ) -> None:
        now = time.time()
        pkg = package_name or ""
        self.execute(
            "INSERT INTO pending_jobs(serial, kind, package_name, reason, created_at, updated_at, attempts, next_attempt_at) "
            "VALUES(?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(serial, kind, package_name) DO UPDATE SET "
            "reason=excluded.reason, updated_at=excluded.updated_at, next_attempt_at=excluded.next_attempt_at",
            (serial, kind, pkg, reason, now, now, now),
        )

    def list_pending(self, *, due_only: bool = False) -> list[sqlite3.Row]:
        if due_only:
            now = time.time()
            return self.fetchall(
                "SELECT * FROM pending_jobs WHERE next_attempt_at <= ? ORDER BY next_attempt_at ASC",
                (now,),
            )
        return self.fetchall("SELECT * FROM pending_jobs ORDER BY updated_at DESC")

    def list_pending_serials(self) -> list[str]:
        rows = self.fetchall("SELECT DISTINCT serial FROM pending_jobs ORDER BY serial")
        return [str(r["serial"]) for r in rows]

    def clear_pending(
        self,
        serial: str,
        *,
        kind: str | None = None,
        package_name: str | None = None,
    ) -> None:
        if kind is None:
            self.execute("DELETE FROM pending_jobs WHERE serial = ?", (serial,))
            return
        if package_name is None:
            self.execute(
                "DELETE FROM pending_jobs WHERE serial = ? AND kind = ?",
                (serial, kind),
            )
            return
        self.execute(
            "DELETE FROM pending_jobs WHERE serial = ? AND kind = ? AND package_name = ?",
            (serial, kind, package_name or ""),
        )

    def bump_pending_attempt(self, serial: str, kind: str, package_name: str = "") -> None:
        row = self.fetchone(
            "SELECT id, attempts FROM pending_jobs WHERE serial = ? AND kind = ? AND package_name = ?",
            (serial, kind, package_name or ""),
        )
        if row is None:
            return
        attempts = int(row["attempts"]) + 1
        idx = min(attempts - 1, len(_PENDING_BACKOFF_MIN) - 1)
        delay = _PENDING_BACKOFF_MIN[idx] * 60
        now = time.time()
        self.execute(
            "UPDATE pending_jobs SET attempts = ?, updated_at = ?, next_attempt_at = ? WHERE id = ?",
            (attempts, now, now + delay, row["id"]),
        )
