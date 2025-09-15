# app/services/weather_service.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Iterable
import pandas as pd

# Meteostat: historical weather via Python API (no key needed)
from meteostat import Point, Daily

# For forecast we’ll default to Open-Meteo (no key).
# If you want Meteostat via RapidAPI, we can add that later.
import requests


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    tz: str = "Australia/Sydney"  # default; doesn’t affect Meteostat Daily


def fetch_historical_weather(
    where: GeoPoint,
    start: date,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """
    Historical daily weather (inclusive) using Meteostat.
    Returns DataFrame with columns: date, max_temp, rain_mm, source='meteostat'
    """
    end = end or date.today()

    # Meteostat Daily() wants datetime, not date
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt   = datetime.combine(end,   datetime.min.time())

    p = Point(where.lat, where.lon)
    df = Daily(p, start_dt, end_dt).fetch()
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "max_temp", "rain_mm", "source"])

    out = pd.DataFrame({
        "date": df.index.date,
        "max_temp": df["tmax"].astype(float) if "tmax" in df.columns else pd.NA,
        "rain_mm": df["prcp"].astype(float) if "prcp" in df.columns else pd.NA,
        "source": "meteostat",
    })
    return out


def fetch_forecast_next_7_days(where: GeoPoint, start_from: Optional[date] = None) -> pd.DataFrame:
    """
    7-day daily forecast. Default implementation uses Open-Meteo (free, no key).
    If you want to force Meteostat via RapidAPI, we can add a separate function.
    Returns DataFrame with columns: date, max_temp, rain_mm, source='open-meteo'
    """
    start_from = start_from or date.today()
    # Open-Meteo daily API
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={where.lat}&longitude={where.lon}"
        "&daily=temperature_2m_max,precipitation_sum"
        "&timezone=auto"
        "&forecast_days=7"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    j = r.json()
    daily = j.get("daily", {})
    dates = daily.get("time", [])
    tmax = daily.get("temperature_2m_max", [])
    prcp = daily.get("precipitation_sum", [])

    out = pd.DataFrame({"date": dates, "max_temp": tmax, "rain_mm": prcp})
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["source"] = "open-meteo"
    # clip to >= start_from just in case
    out = out[out["date"] >= start_from].reset_index(drop=True)
    return out


# Convenience to upsert into our SQLite weather table
def upsert_weather_history_to_db(where: GeoPoint, start: date, end: Optional[date] = None):
    from app.db import get_conn  # local import to avoid cycles
    df = fetch_historical_weather(where, start, end)
    if df.empty:
        return 0
    with get_conn() as conn:
        # delete overlap then insert
        d0 = df["date"].min()
        d1 = df["date"].max()
        conn.execute("DELETE FROM weather WHERE date BETWEEN ? AND ?", (d0.isoformat(), d1.isoformat()))
        conn.executemany(
            "INSERT INTO weather(date,max_temp,rain_mm,source) VALUES (?,?,?,?)",
            df[["date","max_temp","rain_mm","source"]].itertuples(index=False, name=None)
        )
        conn.commit()
    return len(df)


def upsert_weather_forecast_to_db(where: GeoPoint, start_from: Optional[date] = None):
    from app.db import get_conn
    df = fetch_forecast_next_7_days(where, start_from)
    if df.empty:
        return 0
    with get_conn() as conn:
        # delete the same 7-day range then insert
        d0 = df["date"].min()
        d1 = df["date"].max()
        conn.execute("DELETE FROM weather WHERE date BETWEEN ? AND ?", (d0.isoformat(), d1.isoformat()))
        conn.executemany(
            "INSERT INTO weather(date,max_temp,rain_mm,source) VALUES (?,?,?,?)",
            df[["date","max_temp","rain_mm","source"]].itertuples(index=False, name=None)
        )
        conn.commit()
    return len(df)
