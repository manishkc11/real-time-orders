# app/model_train.py
import json, pickle
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.db import get_conn



# ----------------------- Utilities -----------------------

def _fetch_item_history(conn, item_id: int) -> pd.DataFrame:
    """
    Return daily history for a single item_id with joined weather + holiday flag.
    Columns: date, y, weekday, max_temp, rain_mm, is_holiday
    """
    q = """
    WITH base AS (
        SELECT s.date, s.quantity_sold AS y
        FROM sales_data s
        WHERE s.item_id = ?
    ),
    w AS (
        SELECT date, max_temp, rain_mm
        FROM weather
    ),
    e AS (
        SELECT date, 1 AS is_holiday
        FROM events
        WHERE event_type = 'public_holiday' OR COALESCE(uplift_pct,0) > 0
    )
    SELECT
        DATE(b.date) AS date,
        CAST(b.y AS REAL) AS y,
        STRFTIME('%w', b.date) AS wk,  -- 0=Sun..6=Sat
        w.max_temp,
        w.rain_mm,
        COALESCE(e.is_holiday, 0) AS is_holiday
    FROM base b
    LEFT JOIN w  ON w.date = b.date
    LEFT JOIN e  ON e.date = b.date
    ORDER BY b.date
    """
    df = pd.read_sql_query(q, conn, params=(item_id,))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["weekday"] = df["wk"].astype(int)
    df.drop(columns=["wk"], inplace=True)
    return df


def _make_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add rolling features per weekday:
      - last4_same_wd, last8_same_wd (shifted to avoid leakage)
    """
    x = df.copy()
    x["last4_same_wd"] = np.nan
    x["last8_same_wd"] = np.nan

    for wd in range(7):
        mask = x["weekday"] == wd
        s = x.loc[mask, "y"].rolling(window=4, min_periods=2).mean().shift(1)
        x.loc[mask, "last4_same_wd"] = s
        s8 = x.loc[mask, "y"].rolling(window=8, min_periods=3).mean().shift(1)
        x.loc[mask, "last8_same_wd"] = s8

    return x


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add simple calendar features:
      - weekday one-hot (Mon..Sat; we’ll drop Sunday rows)
      - month_sin, month_cos (seasonality)
    """
    x = df.copy()
    # One-hot for weekdays 0..6 (we will later drop Sunday rows if bakery closed)
    wd_dummies = pd.get_dummies(x["weekday"], prefix="wd", dtype=int)
    x = pd.concat([x, wd_dummies], axis=1)

    # Month cyclic features
    m = x["date"].dt.month.astype(float)
    x["month_sin"] = np.sin(2 * np.pi * m / 12)
    x["month_cos"] = np.cos(2 * np.pi * m / 12)
    return x


def _prepare_xy(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series, List[str]]:
    """
    Prepare features X and target y.
    We exclude Sundays (weekday==0) assuming the shop is closed; adjust if not.
    """
    x = df.copy()
    # Drop Sundays if desired (0=Sun, 1=Mon,...,6=Sat)
    x = x[x["weekday"] != 0].reset_index(drop=True)

    # Fill missing weather with medians
    for col in ["max_temp", "rain_mm", "last4_same_wd", "last8_same_wd"]:
        if col in x.columns:
            x[col] = pd.to_numeric(x[col], errors="coerce")
            med = x[col].median()
            x[col] = x[col].fillna(med if pd.notna(med) else 0.0)

    # Feature columns
    feat_cols = [
        "max_temp", "rain_mm",
        "last4_same_wd", "last8_same_wd",
        "is_holiday",
        "month_sin", "month_cos",
        # weekday one-hots (keep Mon..Sat; wd_1..wd_6)
        "wd_1","wd_2","wd_3","wd_4","wd_5","wd_6"
    ]
    for c in feat_cols:
        if c not in x.columns:
            x[c] = 0

    y = x["y"].astype(float)
    X = x[feat_cols].astype(float)
    return X, y, feat_cols


def _time_series_cv_mape(X: pd.DataFrame, y: pd.Series, splits: int = 3) -> float:
    """
    Simple rolling-origin cross-validation MAPE.
    If not enough samples, returns NaN (caller can ignore or fall back).
    """
    n = len(y)
    if n < 30:
        return float("nan")

    # rolling windows
    fold_sizes = np.linspace(0.6, 0.9, splits)
    mape_list = []
    for frac in fold_sizes:
        k = int(n * frac)
        if k < 10 or k >= n:
            continue
        model = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=1.0))
        ])
        model.fit(X.iloc[:k], y.iloc[:k])
        pred = model.predict(X.iloc[k:])
        true = y.iloc[k:].to_numpy()
        denom = np.where(true == 0, 1.0, true)
        mape = (np.abs(true - pred) / np.abs(denom)).mean() * 100.0
        mape_list.append(mape)

    return float(np.mean(mape_list)) if mape_list else float("nan")


# ----------------------- Public API -----------------------

@dataclass
class TrainResult:
    item_id: int
    n_samples: int
    cv_mape: Optional[float]
    saved: bool


def train_model_for_item(conn, item_id: int) -> TrainResult:
    """
    Train a Ridge regression for a single item_id and save it into `models` table.
    Returns TrainResult with basic metrics.
    """
    df = _fetch_item_history(conn, item_id)
    if df.empty:
        return TrainResult(item_id, 0, None, False)

    # rolling features & calendars
    df = _make_rolling_features(df)
    df = _add_calendar_features(df)
    X, y, feat_cols = _prepare_xy(df)

    if len(y) < 20:
        return TrainResult(item_id, len(y), None, False)

    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("ridge", Ridge(alpha=1.0))
    ])
    pipe.fit(X, y)

    cv_mape = _time_series_cv_mape(X, y, splits=3)

    blob = pickle.dumps({
        "model": pipe,
        "feature_names": feat_cols,
    })

    meta = {
        "algo": "Ridge+Scaler",
        "feature_names": feat_cols,
    }

    conn.execute("""
        INSERT INTO models(item_id, algo, model_blob, features_json, n_samples, cv_mape, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(item_id) DO UPDATE SET
            algo=excluded.algo,
            model_blob=excluded.model_blob,
            features_json=excluded.features_json,
            n_samples=excluded.n_samples,
            cv_mape=excluded.cv_mape,
            updated_at=excluded.updated_at
    """.replace("ON CONFLICT(item_id)", "ON CONFLICT(item_id)"), (  # SQLite upsert by item_id
        item_id, meta["algo"], blob, json.dumps(meta),
        int(len(y)), None if np.isnan(cv_mape) else float(cv_mape),
        datetime.now().isoformat(timespec="seconds"),
    ))
    conn.commit()
    return TrainResult(item_id, len(y), None if np.isnan(cv_mape) else float(cv_mape), True)


def train_models_for_all_items(min_samples: int = 30) -> List[TrainResult]:
    """
    Train models for all items which have at least `min_samples` daily rows.
    """
    results: List[TrainResult] = []
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT item_id, COUNT(*) AS n
            FROM sales_data
            WHERE item_id IS NOT NULL
            GROUP BY item_id
            HAVING n >= ?
            ORDER BY n DESC
        """, (min_samples,)).fetchall()

        for (item_id, n) in rows:
            res = train_model_for_item(conn, int(item_id))
            results.append(res)
    return results


# --------- Optional: predict next week using a saved model (per item) ---------

def _load_model_for_item(conn, item_id: int):
    """
    Load a saved model for the given item_id from the `models` table.
    Returns (obj, feats) where obj is a dict containing the sklearn Pipeline
    under key 'model' and the list of feature names under 'feature_names'.
    Returns None if no model exists.
    """
    r = conn.execute(
        "SELECT model_blob, features_json FROM models WHERE item_id=?",
        (item_id,),
    ).fetchone()
    if not r:
        return None
    blob, feat_json = r
    obj = pickle.loads(blob)
    feats = json.loads(feat_json) if feat_json else {}
    return obj, feats

def predict_next_week_for_item(conn, item_id: int, week_start: date) -> Optional[np.ndarray]:
    """
    Return 6 predicted values (Mon..Sat) for one item if a model exists.
    If no model, returns None.
    """
    obj = _load_model_for_item(conn, item_id)
    if obj is None:
        return None
    model = obj[0]["model"]
    feat_names = obj[0]["feature_names"]

    # Build a tiny frame with upcoming dates Mon..Sat
    days = [week_start + timedelta(days=i) for i in range(6)]
    df_future = pd.DataFrame({"date": pd.to_datetime(days)})
    df_future["weekday"] = df_future["date"].dt.weekday

    # join forecast weather
    w = pd.read_sql_query(
        """
        SELECT date, max_temp, rain_mm FROM weather
        WHERE date BETWEEN ? AND ?
        """,
        conn,
        params=(week_start.isoformat(), (week_start + timedelta(days=5)).isoformat()),
    )
    if not w.empty:
        w["date"] = pd.to_datetime(w["date"])
    df_future = df_future.merge(w, on="date", how="left")

    # holiday flag
    h = pd.read_sql_query(
        """
        SELECT date, 1 AS is_holiday FROM events
        WHERE (event_type='public_holiday' OR COALESCE(uplift_pct,0)>0)
          AND date BETWEEN ? AND ?
        """,
        conn,
        params=(week_start.isoformat(), (week_start + timedelta(days=5)).isoformat()),
    )
    if not h.empty:
        h["date"] = pd.to_datetime(h["date"])
    df_future = df_future.merge(h, on="date", how="left").fillna({"is_holiday": 0})

    # calendar feats
    df_future = _add_calendar_features(df_future)

    # ensure columns exist & order
    for c in feat_names:
        if c not in df_future.columns:
            df_future[c] = 0.0
    X = df_future[feat_names].astype(float)

    # --- Safety guard for NaNs and old models ---
    X = X.apply(pd.to_numeric, errors="coerce")
    steps = getattr(model, "named_steps", {})
    if "imputer" not in steps:
        # Old models (without imputer) → fill NaNs manually
        X = X.fillna(X.median(numeric_only=True))
    # --------------------------------------------

    yhat = model.predict(X)
    yhat = np.maximum(0, np.round(yhat)).astype(int)
    return yhat