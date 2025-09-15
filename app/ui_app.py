# app/ui_app.py

# --- ensure project root is on sys.path so "from app.*" works ---

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# ----------------------------------------------------------------
from datetime import timedelta
from app.services.weather_service import GeoPoint, upsert_weather_history_to_db
from app.services.holiday_service import HolidayScope, upsert_holidays_to_db
import numpy as np
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

# core app imports

from app.db import get_conn
from app.io_utils import safe_replace_upload
from app.pipeline import ingest_sales, upsert_events, upsert_weather, generate_forecast
from app.auth import authenticate_user



# validation helpers
from app.validate import (
    read_any_table,
    normalize_sales_columns,
    validate_sales,
    coerce_and_aggregate_sales,
    maybe_unpivot_square_wide,   # auto-unpivot wide Square exports
)

# services for automated data
from app.services.weather_service import (
    GeoPoint,
    upsert_weather_history_to_db,
    upsert_weather_forecast_to_db,
)
from app.services.holiday_service import (
    HolidayScope,
    upsert_holidays_to_db,
)

st.set_page_config(page_title="Real-Time Order Updating System", layout="wide")
st.title("Real-Time Order Updating System")

# ----------------------------- Auth gate -----------------------------
if "auth" not in st.session_state:
    st.session_state["auth"] = None
if "role" not in st.session_state:
    st.session_state["role"] = ""

def login_ui():
    from app.auth import authenticate_user  # ensure available when form submits

    st.subheader("Sign in")
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in")
    if ok:
        user = authenticate_user(u, p)
        if user:
            # save user and role for later
            st.session_state["auth"] = user
            st.session_state["role"] = (user.get("role") or "").lower()
            st.success(f"Welcome, {user['username']}")
            st.rerun()
        else:
            st.error("Invalid username or password")

    # ----- Forgot password flow -----
    with st.expander("Forgot password?"):
        st.caption("Ask an admin for a one-time code, then reset here.")
        with st.form("forgot"):
            fu  = st.text_input("Username", key="fu")
            fc  = st.text_input("One-time code", key="fc")
            np1 = st.text_input("New password", type="password", key="np1")
            np2 = st.text_input("Confirm new password", type="password", key="np2")
            reset = st.form_submit_button("Reset password")
        if reset:
            if np1 != np2:
                st.error("Passwords do not match.")
            elif len(np1) < 6:
                st.error("Choose a longer password (min 6 chars).")
            else:
                from app.auth import complete_password_reset
                if complete_password_reset(fu, fc, np1):
                    st.success("Password updated. You can sign in now.")
                else:
                    st.error("Invalid or expired code / username.")

def logout_ui():
    if st.sidebar.button("Log out"):
        # clear all session state to avoid stale role/menu issues
        st.session_state.clear()
        st.rerun()

# Require login
if not st.session_state["auth"]:
    login_ui()
    st.stop()

user = st.session_state["auth"]
st.sidebar.markdown(f"**Signed in as:** {user['username']} ({user.get('role','')})")
logout_ui()

# ----------------------------- Tabs -----------------------------
# Build the menu once, based on the saved role
# ----------------------------- Tabs -----------------------------
# Helper to read the current role from the session (set by login)
def current_role() -> str:
    auth = st.session_state.get("auth") or {}
    return str(auth.get("role", "viewer")).lower()

role = current_role()

# Build the sidebar menu once
base_menu = ["Upload", "Configure", "Preview", "Download", "History"]
menu = base_menu + (["Admin"] if role == "admin" else [])
TAB = st.sidebar.radio("Navigate", menu)



# ----------------------------- Upload -----------------------------
if TAB == "Upload":
    st.subheader("Upload Weekly Sales Excel/CSV")
    up = st.file_uploader("Choose a file (.xlsx or .csv)", type=["xlsx", "csv"])

    st.caption(
        "Required columns (auto-detected & renamed from Square exports): "
        "`date`, `item_name`, `quantity_sold`"
    )

    if up:
        tmp = Path("data") / f"_tmp_{up.name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(up.getbuffer())

        # 1) Read the raw file
        df_raw = read_any_table(tmp, sheet=0)

        # Auto-handle Square "wide" exports (dates as columns)
        df_raw = maybe_unpivot_square_wide(df_raw)

        # Optional: detect refunds in the raw Square columns
        refund_mask = None
        if "Event Type" in df_raw.columns:
            refund_mask = df_raw["Event Type"].astype(str).str.contains("refund", case=False, na=False)
        elif "Itemisation Type" in df_raw.columns:
            refund_mask = df_raw["Itemisation Type"].astype(str).str.contains("refund", case=False, na=False)

        # 2) Auto-normalize headers to: date / item_name / quantity_sold
        df_norm, missing = normalize_sales_columns(df_raw)
        if missing:
            st.error("Missing required columns after auto-detect.")
            with st.expander("See detected columns & tips"):
                st.write({
                    "Missing": missing,
                    "Detected headers in your file": list(df_raw.columns),
                })
                st.caption("We need these columns (any order): date, item_name, quantity_sold.")
            tmp.unlink(missing_ok=True)
            st.stop()  # stop the Upload flow cleanly

        # 3) Apply refund negativity BEFORE aggregation (row-wise)
        if refund_mask is not None and "quantity_sold" in df_norm.columns:
            q = pd.to_numeric(df_norm["quantity_sold"], errors="coerce")
            mask = refund_mask.reindex(df_norm.index).fillna(False).astype(bool)
            q = np.where(mask, -np.abs(q), q)  # flip sign for refunds
            df_norm["quantity_sold"] = q

        # 4) Aggregate to unique (date, item_name)
        df_final = coerce_and_aggregate_sales(df_norm)
        errs = validate_sales(df_final)
        if errs:
            st.error(" • ".join(errs))
            tmp.unlink(missing_ok=True)
            st.stop()

        # 5) Save normalized+aggregated XLSX and ingest
        dest = safe_replace_upload(tmp, "sales.xlsx")
        with pd.ExcelWriter(dest) as xw:
            df_final.to_excel(xw, index=False, sheet_name="sales")

        st.success(f"Stored normalized file to {dest}. Ingesting to database…")

        # Ingest into DB (pipeline resolves item variants -> item_id)
        ingest_errs = ingest_sales(dest)
        if ingest_errs:
            st.error(" ; ".join(ingest_errs))
        else:
            st.success("Sales data ingested successfully.")

            # --- Auto-load matching history (weather + holidays) ---
            upload_start = pd.to_datetime(df_final["date"]).min().date()
            upload_end   = pd.to_datetime(df_final["date"]).max().date()

            BAKERY_LOC = GeoPoint(-33.8688, 151.2093)  # TODO: set your bakery lat/lon

            try:
                n_w = upsert_weather_history_to_db(BAKERY_LOC, start=upload_start, end=upload_end)
            except Exception as e:
                n_w = 0
                st.warning(f"Weather history not updated: {e}")

            years = list(range(upload_start.year, upload_end.year + 1))
            try:
                n_h = upsert_holidays_to_db(HolidayScope(country="AU", subdiv="NSW", years=years))
            except Exception as e:
                n_h = 0
                st.warning(f"Holidays not updated: {e}")

            st.info(
                f"Auto-loaded {n_w} weather days ({upload_start} → {upload_end}) "
                f"and ensured holidays for {years[0]}–{years[-1]}."
            )

            # flag for Preview guard (if you use it)
            st.session_state["uploaded_this_session"] = True

            # --- Quietly improve accuracy: train/update models after upload ---
            from app.model_train import train_models_for_all_items
            with st.spinner("Improving accuracy…"):
                results = train_models_for_all_items(min_samples=10)
            trained = sum(1 for r in results if r.saved)
            st.info(f"Accuracy improved for {trained} items based on your latest upload.")

        # Always clean up the temp file at the end
        tmp.unlink(missing_ok=True)


# ----------------------------- Configure -----------------------------
elif TAB == "Configure":
    st.subheader("Real-Time Order Updating System")

    # ---------- Manual Events (optional) ----------
    st.markdown("### Events (optional)")
    ev_file = st.file_uploader("Upload events (CSV/XLSX)", type=["csv", "xlsx"], key="events_file")
    if ev_file:
        tmp = Path("data") / f"_tmp_events_{ev_file.name}"
        with open(tmp, "wb") as f:
            f.write(ev_file.getbuffer())
        try:
            ev_df = read_any_table(tmp)
            # Expecting columns: date, event_name, event_type, uplift_pct
            st.dataframe(ev_df.head(), use_container_width=True)
            if st.button("Save events"):
                upsert_events(ev_df)
                st.success("Events saved and will be considered in upcoming forecasts.")
                st.rerun()
        except Exception as e:
            st.error(f"Could not read events file: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    # ---------- Manual Weather for next week (optional) ----------
    st.markdown("### Manual Weather for next week (optional)")
    w_file = st.file_uploader("Upload weather (CSV/XLSX) for the coming week", type=["csv", "xlsx"], key="weather_file")
    if w_file:
        tmp = Path("data") / f"_tmp_weather_{w_file.name}"
        with open(tmp, "wb") as f:
            f.write(w_file.getbuffer())
        try:
            w_df = read_any_table(tmp)
            # Expecting columns: date, max_temp, rain_mm
            st.dataframe(w_df.head(), use_container_width=True)
            if st.button("Save weather"):
                upsert_weather(w_df, source="manual")
                st.success("Weather saved. It will be used for the next week’s forecast.")
                st.rerun()
        except Exception as e:
            st.error(f"Could not read weather file: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    st.markdown("---")
    st.markdown("### Auto-fetch data")

    # ---------- Friendly status peek ----------
    from datetime import date, timedelta
    with get_conn() as conn:
        w_min, w_max, w_rows = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM weather"
        ).fetchone()
        # events table may include general events + holidays
        e_min, e_max, e_rows = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM events"
        ).fetchone()
        next_evt = conn.execute(
            "SELECT date, event_name, event_type FROM events WHERE date >= date('now') ORDER BY date ASC LIMIT 1"
        ).fetchone()


 

    # ---------- Action buttons ----------
    col1, col2 = st.columns(2)
    with col1:
        from app.services.weather_service import GeoPoint, upsert_weather_forecast_to_db
        BAKERY_LOC = GeoPoint(-33.8688, 151.2093)  # TODO: set your bakery lat/lon once

        if st.button("Weather: refresh next 7 days"):
            try:
                n = upsert_weather_forecast_to_db(BAKERY_LOC)
                st.success(f"7-day weather updated ({n} rows).")
                st.rerun()
            except Exception as e:
                st.error(f"Weather update failed: {e}")

    with col2:
        from app.services.holiday_service import HolidayScope, upsert_holidays_to_db
        if st.button("Holidays: refresh upcoming (next 90 days)"):
            try:
                start = date.today()
                end   = start + timedelta(days=90)
                years = list(range(start.year, end.year + 1))
                n = upsert_holidays_to_db(HolidayScope(country='AU', subdiv='NSW', years=years))
                st.success(f"Holidays ensured for {years[0]}–{years[-1]} (covers the next 90 days).")
                st.rerun()
            except Exception as e:
                st.error(f"Holiday update failed: {e}")


# ----------------------------- Preview -----------------------------
elif TAB == "Preview":
    st.subheader("Generate Recommendations (Next Week)")

    use_ml = st.checkbox("Smart Forecasting (improves accuracy)", value=True)
    ml_blend = st.slider("AI emphasis", 0.0, 1.0, 0.5, 0.05,
                         help="0 = rely on historical pattern only, 1 = rely fully on AI")

    # Show DB status
    with get_conn() as c:
        min_d, max_d, rows = c.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM sales_data"
        ).fetchone()

   

    # Require a fresh upload this session
    need_upload_now = not st.session_state.get("uploaded_this_session", False)

    if need_upload_now:
        st.info("Please upload a sales file on the **Upload** tab to generate a new forecast.")
    with get_conn() as conn:
        wmin, wmax, wcnt = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM weather"
        ).fetchone()
        emin, emax, ecnt = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM events"
        ).fetchone()



    if st.button("Generate forecast", disabled=need_upload_now):
        df = generate_forecast(use_ml=use_ml, ml_blend=float(ml_blend))
        if df is None or df.empty:
            st.warning("Not enough data to forecast. Please upload more sales history.")
        else:
            st.success("Forecast ready.")
            st.dataframe(df, use_container_width=True)
            st.session_state["latest_forecast"] = df
            # optional: once used, clear the flag so they must upload again
            st.session_state["uploaded_this_session"] = False


# ----------------------------- Download -----------------------------
elif TAB == "Download":
    st.subheader("Export Order Sheet")
    df = st.session_state.get("latest_forecast")

    if df is None or df.empty:
        st.info("Go to Preview and generate a forecast first.")
    else:
        from email.message import EmailMessage
        import smtplib
        import mimetypes

        # 1) Write the file
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        out_path = Path("outputs") / f"order_sheet_{ts}.xlsx"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(out_path) as xw:
            df.to_excel(xw, index=False, sheet_name="OrderSheet")

        # 2) Let user choose what to do
        action = st.selectbox(
            "Choose an action",
            ["Download the file", "Email the file"]
        )

        if action == "Download the file":
            st.success(f"Saved: {out_path}")
            st.download_button(
                "Download file",
                data=out_path.read_bytes(),
                file_name=out_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        else:
            st.caption("Send the generated order sheet to one or more recipients.")
            to_addr = st.text_input("To (comma-separated)", placeholder="owner@example.com, manager@example.com")
            subject = st.text_input("Subject", value=f"Order Sheet — {ts}")
            body = st.text_area("Message", value="Hi,\n\nPlease find this week's order sheet attached.\n\nThanks.")

            # Optional CC/BCC
            col_cc, col_bcc = st.columns(2)
            with col_cc:
                cc_addr = st.text_input("CC (optional)", placeholder="ops@example.com")
            with col_bcc:
                bcc_addr = st.text_input("BCC (optional)", placeholder="")

            # A small helper to send email with attachment via SMTP
            def _send_email_with_attachment(to_list, cc_list, bcc_list):
                # Load SMTP creds from Streamlit secrets
                try:
                    smtp_conf = st.secrets["smtp"]
                    host = smtp_conf.get("host")
                    port = int(smtp_conf.get("port", 587))
                    user = smtp_conf.get("user")
                    password = smtp_conf.get("password")
                    use_tls = bool(smtp_conf.get("use_tls", True))
                except Exception:
                    st.error(
                        "SMTP settings are missing. Please add them to `.streamlit/secrets.toml` "
                        "(see example below)."
                    )
                    return False

                # Build the message
                msg = EmailMessage()
                msg["From"] = user
                msg["To"] = ", ".join(to_list)
                if cc_list:
                    msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject.strip()
                msg.set_content(body or "")

                # Attach the Excel file
                file_bytes = out_path.read_bytes()
                mime, _ = mimetypes.guess_type(out_path.name)
                maintype, subtype = (mime or "application/octet-stream").split("/", 1)
                msg.add_attachment(file_bytes, maintype=maintype, subtype=subtype, filename=out_path.name)

                # Send
                all_rcpts = to_list + cc_list + bcc_list
                try:
                    if use_tls:
                        server = smtplib.SMTP(host, port, timeout=30)
                        server.starttls()
                    else:
                        server = smtplib.SMTP_SSL(host, port, timeout=30)

                    server.login(user, password)
                    server.send_message(msg, to_addrs=all_rcpts)
                    server.quit()
                    return True
                except Exception as e:
                    st.error(f"Email send failed: {e}")
                    return False

            if st.button("Send email"):
                to_list  = [x.strip() for x in to_addr.split(",") if x.strip()]
                cc_list  = [x.strip() for x in cc_addr.split(",") if x.strip()]
                bcc_list = [x.strip() for x in bcc_addr.split(",") if x.strip()]

                if not to_list:
                    st.warning("Please enter at least one recipient.")
                else:
                    with st.spinner("Sending…"):
                        ok = _send_email_with_attachment(to_list, cc_list, bcc_list)
                    if ok:
                        st.success("Email sent ✅")


# ----------------------------- History -----------------------------
elif TAB == "History":
    st.subheader("Recent Forecasts")

    with get_conn() as conn:
        hist = pd.read_sql_query(
            """
            SELECT week_start_date, item_name, mon, tue, wed, thu, fri, sat, alerts, created_at
            FROM forecasts
            ORDER BY created_at DESC, item_name ASC
            """,
            conn,
        )

    if hist.empty:
        st.info("No forecasts saved yet.")
    else:
        hist["created_at"] = pd.to_datetime(hist["created_at"])
        hist["week_start_date"] = pd.to_datetime(hist["week_start_date"]).dt.date

        runs = (
            hist.groupby(["week_start_date", "created_at"])
                .size().reset_index(name="items")          # <- column named 'items'
                .sort_values(["created_at", "week_start_date"], ascending=[False, False])
                .reset_index(drop=True)
        )

        def run_label(row):
            # IMPORTANT: use row["items"] (not row.items)
            count = int(row["items"])
            created = row["created_at"].strftime("%Y-%m-%d %H:%M")
            return f"Week {row['week_start_date']} • created {created} • {count} items"

        # build choices as indices, but format them with our label
        choices = runs.index.tolist()
        selected_idx = st.selectbox(
            "Select a forecast run",
            options=choices,
            format_func=lambda i: run_label(runs.loc[i]),
        )

        sel = runs.loc[selected_idx]
        df_run = hist[
            (hist["week_start_date"] == sel.week_start_date)
            & (hist["created_at"] == sel.created_at)
        ].copy()

        df_view = df_run.rename(
            columns={
                "item_name": "Item Name",
                "mon": "MON", "tue": "TUE", "wed": "WED",
                "thu": "THURS", "fri": "FRI", "sat": "SAT",
                "alerts": "Notes",
            }
        )[["Item Name", "MON", "TUE", "WED", "THURS", "FRI", "SAT", "Notes"]]

        st.markdown(
            f"**Week:** {sel.week_start_date}  |  "
            f"**Created:** {sel.created_at:%Y-%m-%d %H:%M}  |  "
            f"**Items:** {len(df_view)}"
        )
        st.dataframe(df_view, use_container_width=True)

        ts = f"{sel.week_start_date}_{sel.created_at:%Y-%m-%d_%H%M%S}"
        out_path = Path("outputs") / f"order_sheet_{ts}.xlsx"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(out_path) as xw:
            df_view.to_excel(xw, index=False, sheet_name="OrderSheet")

        st.download_button(
            "Download this run",
            data=out_path.read_bytes(),
            file_name=out_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        with st.expander("Previous runs (headlines)"):
            for _, r in runs.iloc[1:].head(15).iterrows():
                st.write(
                    f"- Week {r['week_start_date']} • created {r['created_at']:%Y-%m-%d %H:%M} • {int(r['items'])} items"
                )



# ----------------------------- Admin -----------------------------
elif TAB == "Admin":
    # Only render for admins
    auth = st.session_state.get("auth") or {}
    if auth.get("role", "viewer").lower() != "admin":
        st.stop()

    st.header("Admin")

    # ---------- User management ----------
    st.subheader("User management")

    col1, col2 = st.columns(2, gap="large")

    # Create user
    with col1:
        st.markdown("**Create user**")
        nu = st.text_input("Username (new)", key="nu")
        npw = st.text_input("Password", type="password", key="npw")
        nrole = st.selectbox("Role", ["user", "admin"], index=0, key="nrole")
        if st.button("Create user", key="btn_create_user"):
            from app.auth import create_user
            try:
                if not nu or not npw:
                    st.error("Username and password are required.")
                else:
                    uid = create_user(nu, npw, nrole)
                    st.success(f"User created: {nu} ({nrole})")
            except Exception as e:
                st.error(str(e))

    # Forgot password (One-time code)
    with col2:
        st.markdown("**Forgot password (one-time code)**")
        from app.auth import start_password_reset, complete_password_reset
        r_user = st.text_input("Username", key="r_user")
        gen, setpw = st.columns([1, 1])

        with gen:
            if st.button("Generate code", key="btn_gen_otp"):
                code = start_password_reset(r_user)
                if code:
                    st.success("One-time code generated (valid 15 minutes).")
                    # In production: email/SMS this code to the user
                    st.code(code, language="text")
                else:
                    st.error("User not found.")

        r_code = st.text_input("One-time code", key="r_code")
        r_new = st.text_input("New password", type="password", key="r_new")
        with setpw:
            if st.button("Set new password", key="btn_set_new"):
                if not (r_user and r_code and r_new):
                    st.error("All fields are required.")
                else:
                    ok = complete_password_reset(r_user, r_code, r_new)
                    st.success("Password updated.") if ok else st.error("Invalid/expired code.")

    st.divider()

    # ---------- Improve accuracy (train models) ----------
    st.subheader("Improve accuracy")
    st.caption("Rebuild recommendations by training/retraining per-item models.")

    if st.button("Train models now"):
        from app.model_train import train_models_for_all_items
        with st.spinner("Training models…"):
            results = train_models_for_all_items(min_samples=10)
        trained = sum(1 for r in results if getattr(r, "saved", False))
        st.success(f"Trained/updated models for {trained} items.")
