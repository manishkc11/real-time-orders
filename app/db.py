# app/db.py
from __future__ import annotations
from pathlib import Path
import os
import sqlite3
from typing import Optional

# Put data under a user folder (clear separation from code)
APP_DIR = Path.home() / "BakeryApp"
APP_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DIR / "app.sqlite"

BASE_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- v1 tables
CREATE TABLE IF NOT EXISTS sales_data (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  item_name TEXT NOT NULL,
  quantity_sold INTEGER NOT NULL,
  device_store TEXT,
  is_promo INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_data(date);
CREATE INDEX IF NOT EXISTS idx_sales_item_name ON sales_data(item_name);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  event_name TEXT NOT NULL,
  event_type TEXT,
  uplift_pct REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);

CREATE TABLE IF NOT EXISTS weather (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  max_temp REAL,
  rain_mm REAL,
  source TEXT
);
CREATE INDEX IF NOT EXISTS idx_weather_date ON weather(date);

CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY,
  week_start_date TEXT NOT NULL,
  item_name TEXT NOT NULL,
  mon INTEGER, tue INTEGER, wed INTEGER,
  thu INTEGER, fri INTEGER, sat INTEGER,
  alerts TEXT,
  reasoning TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecasts_week ON forecasts(week_start_date);

CREATE TABLE IF NOT EXISTS config (
  id INTEGER PRIMARY KEY,
  setting_name TEXT UNIQUE NOT NULL,
  setting_value TEXT
);
"""

DEFAULT_CONFIG = {
    "coef_temp": "0.15",
    "coef_rain": "0.10",
    "min_batch_size": "6",
    "std_alert_threshold": "1.5",
    "lookback_weeks": "26",

    # OPTIONAL: simple canonicalization rules (regex) for auto-grouping
    # Format: pattern => canonical name
    "canon_rule_1": r"(?i)hot\s*choc.* => Hot Chocolate",
    "canon_rule_2": r"(?i)matcha.* => Matcha",
    "canon_rule_3": r"(?i)coffee.*(reg|regular).* => Coffee (Regular)",
    "canon_rule_4": r"(?i)coffee.*(large|l)\b.* => Coffee (Large)",
}

def get_conn() -> sqlite3.Connection:
    first_time = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    if first_time:
        conn.executescript(BASE_SCHEMA)
        for k, v in DEFAULT_CONFIG.items():
            conn.execute("INSERT INTO config(setting_name, setting_value) VALUES (?,?)", (k, v))
        conn.commit()
    _migrate(conn)   # ensure we’re on latest schema
    return conn

# ------------------------ Migrations ------------------------

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())

def _migrate(conn: sqlite3.Connection) -> None:
    """
    v2: add items + item_aliases, add sales_data.item_id, indexes.
    """
    # items & item_aliases tables
    if not _table_exists(conn, "items"):
        conn.execute("""
            CREATE TABLE items (
              id INTEGER PRIMARY KEY,
              canonical_name TEXT UNIQUE NOT NULL,
              category TEXT,
              active INTEGER DEFAULT 1
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(canonical_name)")

    if not _table_exists(conn, "item_aliases"):
        conn.execute("""
            CREATE TABLE item_aliases (
              id INTEGER PRIMARY KEY,
              alias TEXT UNIQUE NOT NULL,
              item_id INTEGER NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias_item ON item_aliases(item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias_alias ON item_aliases(alias)")
        # ensure one row per (date,item_id)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_date_item ON sales_data(date, item_id)")
            # --- password reset tokens ---
    if not _table_exists(conn, "password_resets"):
        conn.execute("""
            CREATE TABLE password_resets (
              id INTEGER PRIMARY KEY,
              user_id INTEGER NOT NULL,
              salt BLOB NOT NULL,
              token_hash BLOB NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pwreset_user ON password_resets(user_id)")
    conn.commit()

            # --- users table for auth ---
    if not _table_exists(conn, "users"):
        conn.execute("""
            CREATE TABLE users (
              id INTEGER PRIMARY KEY,
              username TEXT NOT NULL UNIQUE,
              password_salt BLOB NOT NULL,
              password_hash BLOB NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('admin','user')),
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username ON users(username)")
    conn.commit()



    # sales_data.item_id (nullable; we gradually backfill)
    if not _column_exists(conn, "sales_data", "item_id"):
        conn.execute("ALTER TABLE sales_data ADD COLUMN item_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_item_id ON sales_data(item_id)")
        # models table (stores one model per item_id)
    def _migrate(conn: sqlite3.Connection) -> None:
    # ...existing migrations...

    # models table (stores one model per item_id)
     if not _table_exists(conn, "models"):
        conn.execute("""
            CREATE TABLE models (
              id INTEGER PRIMARY KEY,
              item_id INTEGER NOT NULL,
              algo TEXT NOT NULL,
              model_blob BLOB NOT NULL,
              features_json TEXT,
              n_samples INTEGER,
              cv_mape REAL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            );
        """)
        # (old non-unique index is fine to keep)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_item ON models(item_id)")

    # ✅ make item_id unique so ON CONFLICT(item_id) works
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_models_item ON models(item_id)")

    conn.commit()


# ------------------------ Catalog helpers ------------------------

def _get_item_id_by_alias(conn: sqlite3.Connection, alias: str) -> Optional[int]:
    cur = conn.execute("SELECT item_id FROM item_aliases WHERE alias = ?", (alias,))
    row = cur.fetchone()
    return int(row[0]) if row else None

def _get_item_id_by_canonical(conn: sqlite3.Connection, canonical: str) -> Optional[int]:
    cur = conn.execute("SELECT id FROM items WHERE canonical_name = ?", (canonical,))
    row = cur.fetchone()
    return int(row[0]) if row else None

def _create_item_with_alias(conn: sqlite3.Connection, canonical: str, alias: str, category: Optional[str] = None) -> int:
    cur = conn.execute("INSERT INTO items(canonical_name, category, active) VALUES (?,?,1)", (canonical, category))
    item_id = cur.lastrowid
    conn.execute("INSERT INTO item_aliases(alias, item_id) VALUES (?,?)", (alias, item_id))
    conn.commit()
    return int(item_id)

def upsert_alias(conn: sqlite3.Connection, alias: str, item_id: int) -> None:
    """
    Ensure alias -> item_id exists (create if missing).
    """
    cur = conn.execute("SELECT id FROM item_aliases WHERE alias=?", (alias,))
    if cur.fetchone() is None:
        conn.execute("INSERT INTO item_aliases(alias, item_id) VALUES (?,?)", (alias, item_id))
        conn.commit()

def resolve_item_id(conn: sqlite3.Connection, raw_name: str, category: Optional[str] = None) -> int:
    """
    Given any incoming item name/variant, return a stable item_id.
    If alias or canonical exists -> reuse; else create new canonical == alias.
    """
    alias = (raw_name or "").strip()
    if not alias:
        raise ValueError("Empty item name cannot be resolved")

    # 1) alias direct hit?
    item_id = _get_item_id_by_alias(conn, alias)
    if item_id:
        return item_id

    # 2) try simple canonicalization rules from config (regex => canonical)
    canonical = None
    try:
        rules = dict(conn.execute("SELECT setting_name, setting_value FROM config WHERE setting_name LIKE 'canon_rule_%'").fetchall())
        import re
        for _, rule in sorted(rules.items()):
            if "=>" in rule:
                pat, canon = [x.strip() for x in rule.split("=>", 1)]
                if re.search(pat, alias):
                    canonical = canon
                    break
    except Exception:
        canonical = None

    if canonical:
        # If canonical item exists, link alias; else create item + alias
        existing = _get_item_id_by_canonical(conn, canonical)
        if existing:
            upsert_alias(conn, alias, existing)
            return existing
        else:
            return _create_item_with_alias(conn, canonical, alias, category=category)

    # 3) fallback: treat alias as its own canonical entry (one-off new item)
    return _create_item_with_alias(conn, alias, alias, category=category)
