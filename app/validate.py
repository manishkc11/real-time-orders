# app/validate.py
from __future__ import annotations
import pandas as pd
from typing import List, Tuple
import re
import pandas as pd
from typing import List

DATE_COL_RE = re.compile(r"^\s*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\s*$")  # 07/07/2021, 7-7-21, etc.

def maybe_unpivot_square_wide(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detects Square 'wide' exports where each date is a column and unpivots to
    tidy rows with: date, item_name, quantity_sold.
    If not wide, returns df unchanged.
    """
    if df is None or df.empty:
        return df

    cols = [str(c) for c in df.columns]
    # date-like columns by header pattern
    date_like: List[str] = [c for c in cols if DATE_COL_RE.match(c)]
    # Heuristic: many date columns -> it's wide
    if len(date_like) < 5:
        return df  # not wide

    # Identify the item name columns available
    possible_item_cols = [c for c in cols if c.lower() in ("item name", "item", "product", "name")]
    item_col = possible_item_cols[0] if possible_item_cols else None
    if not item_col:
        # still try with 'Item Name' most common
        item_col = "Item Name" if "Item Name" in df.columns else None
    if not item_col:
        return df  # can't confidently unpivot

    # Optional 'Item Variation' to append to name
    var_col = None
    for v in ("Item Variation", "Variation", "Price Point Name"):
        if v in df.columns:
            var_col = v
            break

    id_vars = [item_col] + ([var_col] if var_col else [])
    wide = df.copy()

    # Melt dates into a single 'date' column
    long = wide.melt(id_vars=id_vars, value_vars=date_like,
                     var_name="date", value_name="quantity_sold")

    # Build item_name (append variation if present)
    if var_col:
        long["item_name"] = (long[item_col].astype(str).str.strip() + " - " +
                             long[var_col].astype(str).str.strip()).str.replace(r"\s+-\s+nan$", "", regex=True)
        long["item_name"] = long["item_name"].str.replace(r"\s+-\s+$", "", regex=True)
    else:
        long["item_name"] = long[item_col].astype(str).str.strip()

    # Clean types
    long["date"] = pd.to_datetime(long["date"], dayfirst=True, errors="coerce").dt.date
    long["quantity_sold"] = pd.to_numeric(long["quantity_sold"], errors="coerce").fillna(0)

    # Drop empties/zeros
    long = long.dropna(subset=["date", "item_name"])
    long = long[long["quantity_sold"] != 0]

    # Keep only tidy columns
    return long[["date", "item_name", "quantity_sold"]]


# ---------------------------------------------------------------------
# Required / optional schema
# ---------------------------------------------------------------------
REQUIRED_SALES_COLS = ["date", "item_name", "quantity_sold"]
OPTIONAL_SALES_COLS = ["category", "device_store", "is_promo"]

# ---------------------------------------------------------------------
# Robust header aliases (Square/Excel exports)
#   We map many common column names to our 3 required fields.
# ---------------------------------------------------------------------
_SALES_HEADER_CANDIDATES = {
    "date": [
        "date", "Date", "Business Date", "Order Date", "Sales Date",
        "Transaction Date", "Payment Date"
    ],
    "item_name": [
        "item_name", "Item Name", "Item", "Price Point Name", "Product",
        "Item/Variation", "Item - Variation", "Variation", "Item Variation",
        "Product Name", "SKU Name"
    ],
    "quantity_sold": [
        "quantity_sold", "Qty", "Quantity", "Count", "Units", "Unit",
        "Quantity Sold", "Net Quantity", "Qty Sold", "Sales Quantity"
    ],
}

# ---------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------
def read_any_table(path, sheet: int | str = 0) -> pd.DataFrame:
    """
    Read .csv or .xlsx into a DataFrame (tolerant for large CSVs).
    """
    p = str(path).lower()
    if p.endswith(".csv"):
        return pd.read_csv(path, low_memory=False)
    return pd.read_excel(path, sheet_name=sheet)

# ---------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------
def normalize_sales_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Try to rename incoming columns to REQUIRED_SALES_COLS.
    Returns (renamed_df, missing_required_after_normalization).
    """
    if df is None or df.empty:
        return df, REQUIRED_SALES_COLS[:]  # everything "missing" if empty

    # map of lowercase->original for quick lookup
    lower_to_orig = {str(c).strip().lower(): c for c in df.columns}
    rename_map: dict[str, str] = {}
    for target, aliases in _SALES_HEADER_CANDIDATES.items():
        found_orig = None
        for alias in aliases:
            key = alias.strip().lower()
            if key in lower_to_orig:
                found_orig = lower_to_orig[key]
                break
        if found_orig is not None:
            rename_map[found_orig] = target

    df2 = df.rename(columns=rename_map)

    # After renaming, determine what's still missing
    still_missing = [c for c in REQUIRED_SALES_COLS if c not in df2.columns]
    return df2, still_missing

def coerce_and_aggregate_sales(df: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce types and aggregate to unique (date, item_name) rows.
    - Parses 'date'
    - quantity_sold -> numeric (NaN→0)
    - sums duplicates
    - drops rows with invalid date
    """
    if df.empty:
        return df
    out = df.copy()

    # Parse date and quantity
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["quantity_sold"] = pd.to_numeric(out["quantity_sold"], errors="coerce").fillna(0).astype(int)
    out = out.dropna(subset=["date"])

    # Aggregate duplicate keys
    out = (out.groupby(["date", "item_name"], as_index=False)["quantity_sold"].sum())

    # Optional sanity: ensure required columns present
    for c in REQUIRED_SALES_COLS:
        if c not in out.columns:
            out[c] = pd.Series(dtype="int64") if c == "quantity_sold" else pd.Series(dtype="object")
    return out

# ---------------------------------------------------------------------
# Validation (post-normalization is recommended)
# ---------------------------------------------------------------------
def validate_sales(df: pd.DataFrame) -> List[str]:
    """
    Validate a DataFrame that is *supposed* to already have:
      ['date','item_name','quantity_sold']
    Returns a list of human-readable error messages (empty list = OK).
    """
    errors: List[str] = []

    # 1) Required columns present?
    missing = [c for c in REQUIRED_SALES_COLS if c not in df.columns]
    if missing:
        errors.append(f"Missing required columns: {missing}")
        # If they're missing we can stop here; follow-on checks would crash.
        return errors

    # 2) Types
    # date
    try:
        _ = pd.to_datetime(df["date"])
    except Exception:
        errors.append("The 'date' column is not parseable (use YYYY-MM-DD).")

    # quantity_sold numeric
    bad_q = pd.to_numeric(df["quantity_sold"], errors="coerce").isna()
    if bad_q.any():
        errors.append("The 'quantity_sold' column contains non-numeric values.")

    # 3) Empty / duplicates
    if len(df) == 0:
        errors.append("File has no rows.")
    else:
        dup = df.duplicated(subset=["date", "item_name"], keep=False)
        if dup.any():
            errors.append("Duplicate (date, item_name) pairs found. Consider aggregating first.")

    return errors

# ---------------------------------------------------------------------
# Convenience: normalize + validate in one call
# ---------------------------------------------------------------------
def normalize_and_validate_sales(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Convenience wrapper used by the UI:
      1) auto-rename headers,
      2) validate schema,
      3) coerce + aggregate duplicates.
    Returns (clean_df, errors).
    """
    df1, missing = normalize_sales_columns(df)
    if missing:
        return df1, [f"Missing required columns after auto-detect: {missing}"]

    errs = validate_sales(df1)
    if errs:
        return df1, errs

    df2 = coerce_and_aggregate_sales(df1)
    return df2, []
