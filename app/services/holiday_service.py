# app/services/holiday_service.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional
import pandas as pd
import holidays  # pip install holidays

from app.db import get_conn

# Default uplift to apply on holiday dates (can be tuned in config later)
DEFAULT_HOLIDAY_UPLIFT = 15.0  # percent

@dataclass(frozen=True)
class HolidayScope:
    country: str = "AU"         # Australia
    subdiv: str = "NSW"         # New South Wales (Sydney)
    years: Optional[Iterable[int]] = None  # e.g. [2024, 2025]

def build_holiday_frame(scope: HolidayScope) -> pd.DataFrame:
    """
    Build a DataFrame of holidays with columns:
      date (date), event_name (str), event_type (str), uplift_pct (float)
    """
    if scope.years is None:
        # default: current year, previous and next (3-year window)
        from datetime import datetime
        y = date.today().year
        years = [y - 1, y, y + 1]
    else:
        years = list(scope.years)

    au = holidays.country_holidays(scope.country, subdiv=scope.subdiv, years=years)

    rows = []
    for d, name in au.items():
        # Skip Sundays if you don't trade; else keep everything
        rows.append({
            "date": pd.to_datetime(d).date(),
            "event_name": name,
            "event_type": "public_holiday",
            "uplift_pct": DEFAULT_HOLIDAY_UPLIFT,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df

def upsert_holidays_to_db(scope: HolidayScope) -> int:
    """
    Delete any overlapping rows in `events` for the selected years/subdiv
    and insert the holiday set. Returns number of rows written.
    """
    df = build_holiday_frame(scope)
    if df.empty:
        return 0
    with get_conn() as conn:
        d0 = df["date"].min().isoformat()
        d1 = df["date"].max().isoformat()
        conn.execute(
            "DELETE FROM events WHERE date BETWEEN ? AND ? AND event_type = 'public_holiday'",
            (d0, d1),
        )
        conn.executemany(
            "INSERT INTO events(date, event_name, event_type, uplift_pct) VALUES (?,?,?,?)",
            df[["date","event_name","event_type","uplift_pct"]].itertuples(index=False, name=None)
        )
        conn.commit()
    return len(df)
