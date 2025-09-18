# app/pipeline.py
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple
import sqlite3

import numpy as np
import pandas as pd

from app.model_train import predict_next_week_for_item
from app.db import get_conn, resolve_item_id
from app.validate import read_any_table, validate_sales

# ---------------- Ingestion ----------------

def ingest_sales(file_path: Path) -> list[str]:
    """
    Read Excel/CSV, validate schema, resolve item variants to item_id, and insert into sales_data.
    Returns a list of human-readable errors (empty list means OK).
    """
    df = read_any_table(file_path, sheet=0)
    errors = validate_sales(df)
    if errors:
        return errors

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["quantity_sold"] = pd.to_numeric(df["quantity_sold"], errors="coerce").fillna(0).astype(int)
    df["device_store"] = df.get("device_store", pd.Series([None] * len(df)))
    df["is_promo"] = df.get("is_promo", pd.Series([0] * len(df))).fillna(0).astype(int)

    # Resolve item_id for each distinct item_name (cache to avoid repeated DB lookups)
    item_cache: Dict[str, int] = {}
    with get_conn() as conn:
        def _resolve(name: str) -> int:
            key = (name or "").strip()
            if key in item_cache:
                return item_cache[key]
            iid = resolve_item_id(conn, key, category=None)
            item_cache[key] = iid
            return iid

        df["item_id"] = df["item_name"].astype(str).map(_resolve)

        # Insert rows including item_id
        conn.executemany(
            """INSERT INTO sales_data(date,item_name,quantity_sold,device_store,is_promo,item_id)
               VALUES (?,?,?,?,?,?)""",
            df[["date","item_name","quantity_sold","device_store","is_promo","item_id"]]
              .astype({"quantity_sold": int, "is_promo": int})
              .itertuples(index=False, name=None)
        )
        conn.commit()
    return []

def upsert_events(events_df: pd.DataFrame):
    """Replace recent events with the provided dataframe contents."""
    if events_df is None or events_df.empty:
        return
    df = events_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["uplift_pct"] = pd.to_numeric(df["uplift_pct"], errors="coerce").fillna(0.0)

    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE date >= date('now','-1 year')")
        conn.executemany(
            "INSERT INTO events(date,event_name,event_type,uplift_pct) VALUES (?,?,?,?)",
            df[["date","event_name","event_type","uplift_pct"]].itertuples(index=False, name=None)
        )
        conn.commit()

def upsert_weather(weather_df: pd.DataFrame, source: str = "manual"):
    """Replace recent weather rows with the provided dataframe contents."""
    if weather_df is None or weather_df.empty:
        return
    df = weather_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["max_temp"] = pd.to_numeric(df["max_temp"], errors="coerce")
    df["rain_mm"] = pd.to_numeric(df["rain_mm"], errors="coerce")

    with get_conn() as conn:
        conn.execute("DELETE FROM weather WHERE date >= date('now','-60 days')")
        conn.executemany(
            "INSERT INTO weather(date,max_temp,rain_mm,source) VALUES (?,?,?,?)",
            df[["date","max_temp","rain_mm"]].assign(source=source).itertuples(index=False, name=None)
        )
        conn.commit()

# ---------------- Config helpers ----------------

def get_config() -> dict:
    with get_conn() as conn:
        cur = conn.execute("SELECT setting_name, setting_value FROM config")
        return {k: v for k, v in cur.fetchall()}

def next_monday(today=None):
    today = today or datetime.now().date()
    return today + timedelta(days=(7 - today.weekday()) % 7)

# ---------------- History & Baseline ----------------

def _fetch_history(conn: sqlite3.Connection, lookback_weeks: int) -> pd.DataFrame:
    """
    Return history with date, item_key (item_id if available else item_name), display_name, quantity_sold.
    display_name is canonical_name when item_id exists, otherwise item_name.
    """
    cur = conn.execute(
        """SELECT s.date, s.item_id, s.item_name, s.quantity_sold,
                  i.canonical_name
           FROM sales_data s
           LEFT JOIN items i ON i.id = s.item_id
           WHERE s.date >= date('now', ?)""",
        (f"-{lookback_weeks*7} days",)
    )
    df = pd.DataFrame(cur.fetchall(), columns=["date","item_id","item_name","quantity_sold","canonical_name"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["item_key"] = df["item_id"].where(df["item_id"].notna(), df["item_name"])
    df["display_name"] = df["canonical_name"].where(df["canonical_name"].notna(), df["item_name"])
    return df

def _weekday_baseline(history: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Build a weekday baseline (Mon–Sat).
    Returns (pivot_df, name_map):
      pivot_df index = item_key, columns = mon..sat
      name_map: pd.Series mapping item_key -> display_name
    """
    h = history.copy()
    if "item_key" not in h.columns:
        h["item_key"] = h["item_name"]
        h["display_name"] = h["item_name"]

    h["weekday"] = h["date"].dt.weekday  # 0=Mon
    h = h.sort_values("date")

    def ew_last8(s: pd.Series):
        s8 = s.tail(8).reset_index(drop=True)
        return s8.ewm(alpha=0.5).mean().iloc[-1] if len(s8) else 0.0

    agg = (h.groupby(["item_key","weekday"])["quantity_sold"]
             .apply(ew_last8).reset_index())

    pivot = agg.pivot(index="item_key", columns="weekday", values="quantity_sold").fillna(0.0)
    pivot = pivot.rename(columns={0:"mon",1:"tue",2:"wed",3:"thu",4:"fri",5:"sat"})
    for c in ["mon","tue","wed","thu","fri","sat"]:
        if c not in pivot.columns:
            pivot[c] = 0.0
    pivot = pivot[["mon","tue","wed","thu","fri","sat"]]

    name_map = h.drop_duplicates("item_key").set_index("item_key")["display_name"]
    return pivot, name_map

# ---------------- Weather & Events ----------------

def _load_week_weather(conn, week_start):
    dates = [(week_start + timedelta(days=i)).isoformat() for i in range(6)]
    q = f"SELECT date, max_temp, rain_mm FROM weather WHERE date IN ({','.join(['?']*6)})"
    cur = conn.execute(q, dates)
    w = pd.DataFrame(cur.fetchall(), columns=["date","max_temp","rain_mm"])
    if w.empty:
        return {}
    w["date"] = pd.to_datetime(w["date"])
    w["weekday"] = w["date"].dt.weekday
    return {int(r.weekday): (r.max_temp, r.rain_mm) for r in w.itertuples(index=False)}

def _apply_weather(base: pd.DataFrame, conn, week_start, coef_temp: float, coef_rain: float):
    base = base.copy()
    ctx = _load_week_weather(conn, week_start)
    avg_temp, avg_rain = 20.0, 1.0  # neutral anchors
    days = ["mon","tue","wed","thu","fri","sat"]
    for i, d in enumerate(days):
        if i in ctx:
            t, r = ctx[i]
            if pd.notna(t):
                base[d] = (base[d] * (1 + coef_temp * (float(t) - avg_temp) / 10)).round()
            if pd.notna(r):
                base[d] = (base[d] * (1 + coef_rain * (float(r) - avg_rain) / 10)).round()
    return base.clip(lower=0)

def _apply_events(base: pd.DataFrame, conn, week_start):
    base = base.copy()
    cur = conn.execute(
        "SELECT date, uplift_pct FROM events WHERE date BETWEEN ? AND ?",
        (week_start.isoformat(), (week_start + timedelta(days=5)).isoformat())
    )
    rows = cur.fetchall()
    notes = []
    if rows:
        for d, up in rows:
            d = pd.to_datetime(d)
            wk = d.weekday()  # 0=Mon
            col = ["mon","tue","wed","thu","fri","sat"][wk]
            factor = 1 + float(up)/100.0
            base[col] = (base[col] * factor).round()
            notes.append(f"{d.date()} +{up}% on {col.upper()}")
    return base, notes

# ---------------- Forecast ----------------

def generate_forecast(week_start=None, use_ml: bool = True, ml_blend: float = 0.5) -> pd.DataFrame:
    """
    Build the next week's order sheet (Mon–Sat) with Alerts & Reasoning.
    Also persists a copy into the forecasts table.
    """
    week_start = week_start or next_monday()
    cfg = get_config()
    coef_temp = float(cfg.get("coef_temp", 0.15))
    coef_rain = float(cfg.get("coef_rain", 0.10))
    lookback_weeks = int(cfg.get("lookback_weeks", 26))
    std_thresh = float(cfg.get("std_alert_threshold", 1.5))
    min_batch = int(cfg.get("min_batch_size", 6))

    with get_conn() as conn:
        hist = _fetch_history(conn, lookback_weeks)
        if hist.empty:
            return pd.DataFrame()

        base, name_map = _weekday_baseline(hist)
        base = _apply_weather(base, conn, week_start, coef_temp, coef_rain)
        base, event_notes = _apply_events(base, conn, week_start)

        # ---- ML blend (optional, friendly default) ----
        # If we have a trained model for an item (identified by numeric item_key),
        # blend model predictions with the baseline for Mon..Sat.
        if use_ml:
            days = ["mon", "tue", "wed", "thu", "fri", "sat"]
            with get_conn() as conn2:
                for key in base.index:
                    # keys coming from items are ints; legacy keys are strings
                    if isinstance(key, (int, np.integer)):
                        yhat = predict_next_week_for_item(conn2, int(key), week_start)
                        if yhat is not None and len(yhat) == 6:
                            arr = base.loc[key, days].to_numpy(dtype=float)
                            blended = (ml_blend * np.array(yhat, dtype=float) + (1.0 - ml_blend) * arr)
                            base.loc[key, days] = np.maximum(0, np.round(blended)).astype(int)
        # -----------------------------------------------
        # Batch floors (non-negative)
        for c in base.columns:
            base[c] = base[c].apply(lambda x: max(int(round(x)), min_batch) if x > 0 else 0)

        # ---------- Customer-facing Notes ONLY (no "Why") ----------

        # 1) Build WEEKLY stats from history for a true "typical week" comparison
        wk_hist = hist.copy()
        wk_hist["week_start"] = wk_hist["date"].dt.to_period("W-MON").apply(lambda p: p.start_time.date())
        weekly_totals = (wk_hist.groupby(["item_key", "week_start"])["quantity_sold"]
                                   .sum().reset_index())
        weekly_stats = (weekly_totals.groupby("item_key")["quantity_sold"]
                                      .agg(["mean", "std"]).rename(columns={"mean":"wmean","std":"wstd"})
                                      .fillna(0.0))

        # 2) Per-item notes
        days = ["mon","tue","wed","thu","fri","sat"]
        notes = {}
        for key in base.index:
            weekly_forecast = float(base.loc[key, days].sum())
            wmean = weekly_stats.loc[key, "wmean"] if key in weekly_stats.index else 0.0
            wstd  = weekly_stats.loc[key, "wstd"] if key in weekly_stats.index else 0.0

            if wmean <= 0:
                note_txt = "No typical week yet"
            else:
                diff_pct = (weekly_forecast - wmean) / wmean * 100.0
                if wstd > 0 and weekly_forecast > wmean + std_thresh * wstd:
                    note_txt = f"Higher than usual (+{diff_pct:.0f}%)"
                elif wstd > 0 and weekly_forecast < max(wmean - std_thresh * wstd, 0):
                    note_txt = f"Lower than usual ({diff_pct:.0f}%)"
                else:
                    note_txt = "As expected"

            notes[key] = note_txt
        # -----------------------------------------------------------

        # Build display frame
        out = base.reset_index().rename(columns={"item_key":"Item Key"})
        out["Item Name"] = out["Item Key"].map(name_map).fillna(out["Item Key"].astype(str))
        out["Weekly Baking"] = out[["mon","tue","wed","thu","fri","sat"]].sum(axis=1)
        out["Notes"] = out["Item Key"].map(notes)


              # persist to forecasts table (store display name for human readability)
        now = datetime.now().isoformat(timespec="seconds")
        for _, r in out.iterrows():
            conn.execute("""
                INSERT INTO forecasts(week_start_date,item_name,mon,tue,wed,thu,fri,sat,alerts,reasoning,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                week_start.isoformat(), r["Item Name"],
                int(r["mon"]), int(r["tue"]), int(r["wed"]),
                int(r["thu"]), int(r["fri"]), int(r["sat"]),
                r["Notes"], "", now  # reasoning left empty
            ))
        conn.commit()

    # Pretty columns for UI/export
    out = out[["Item Name","Weekly Baking","mon","tue","wed","thu","fri","sat","Notes"]]
    out.columns = ["Item Name","Weekly Baking","MON","TUE","WED","THURS","FRI","SAT","Notes"]
    return out
