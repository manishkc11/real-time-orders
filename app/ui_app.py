from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
import mimetypes
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import streamlit as st
import smtplib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import get_conn
from app.io_utils import safe_replace_upload
from app.model_train import train_models_for_all_items
from app.pipeline import ingest_sales, upsert_events, upsert_weather, generate_forecast
from app.services.holiday_service import HolidayScope, upsert_holidays_to_db
from app.services.weather_service import (
    GeoPoint,
    upsert_weather_history_to_db,
    upsert_weather_forecast_to_db,
)
from app.validate import (
    read_any_table,
    maybe_unpivot_square_wide,
    normalize_sales_columns,
    coerce_and_aggregate_sales,
    validate_sales,
)

st.set_page_config(page_title="Hudson Bakery: Forecasting Pipeline", layout="wide")

LOADER_CSS = """
<style>
[data-testid='stToolbar'] {
    display: flex;
    align-items: center;
    column-gap: 0.75rem;
}
[data-testid='stStatusWidget'] {
    position: static !important;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    min-width: 32px;
    min-height: 32px;
    margin: 0;
}
[data-testid='stStatusWidget'] svg {
    display: none !important;
}
[data-testid='stStatusWidget']::before {
    content: '';
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: 3px solid rgba(31, 111, 235, 0.25);
    border-top-color: #1f6feb;
    animation: rt-spin 0.8s linear infinite;
    display: block;
}
@keyframes rt-spin {
    to { transform: rotate(360deg); }
}

</style>
"""

LOADER_HTML = '<div class="rt-dots-loader"><span></span><span></span><span></span></div>'

DEFAULT_LOCATION = GeoPoint(-33.8688, 151.2093)
HOLIDAY_COUNTRY = "AU"
HOLIDAY_SUBDIV = "NSW"


def _init_session_state() -> None:
    defaults = {
        "loading": False,
        "auth": None,
        "role": "",
        "uploaded_this_session": False,
        "latest_forecast": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_loader():
    st.markdown(LOADER_CSS, unsafe_allow_html=True)
    placeholder = st.container()
    if st.session_state.get("loading"):
        placeholder.markdown(LOADER_HTML, unsafe_allow_html=True)
    return placeholder



def _set_loading(placeholder, value: bool) -> None:
    st.session_state["loading"] = value
    if value:
        placeholder.markdown(LOADER_HTML, unsafe_allow_html=True)
    else:
        placeholder.empty()


def _display_validation_errors(errors: Iterable[str]) -> None:
    items = [str(e) for e in errors if e]
    if items:
        st.error(" | ".join(items))


def _train_models_after_upload() -> None:
    try:
        with st.spinner("Improving accuracy..."):
            results = train_models_for_all_items(min_samples=10)
        trained = sum(1 for r in results if getattr(r, "saved", False))
        st.info(f"Trained or updated models for {trained} items.")
    except Exception as exc:
        st.warning(f"Model training skipped: {exc}")


def login_ui() -> None:
    from app.auth import authenticate_user

    st.title("Hudson Bakery: Forecasting Pipeline")
    st.subheader("Sign in")

    with st.form("login-form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        try:
            user = authenticate_user(username, password)
        except Exception as exc:
            st.error(f"Login failed: {exc}")
            return

        if user:
            st.session_state["auth"] = user
            st.session_state["role"] = user.get("role", "")
            st.success(f"Welcome, {user.get('username', '')}!")
            st.rerun()
        else:
            st.error("Invalid username or password.")


def _sidebar_header(auth: dict) -> str:
    username = auth.get("username", "user")
    role = auth.get("role", "user")
    st.sidebar.markdown(f"**Signed in as:** {username} ({role})")
    if st.sidebar.button("Sign out"):
        st.session_state["auth"] = None
        st.session_state["role"] = ""
        st.session_state["latest_forecast"] = None
        st.session_state["uploaded_this_session"] = False
        st.session_state["loading"] = False
        st.rerun()

    tabs = ["Upload", "Configure", "Preview", "Download", "History"]
    if str(role).lower() == "admin":
        tabs.append("Admin")
    return st.sidebar.radio("Navigate", tabs)


def _render_upload_tab(loader_placeholder) -> None:
    st.subheader("Upload sales data")
    st.caption("Upload a CSV or Excel export of recent sales to refresh the system.")

    upload = st.file_uploader("Sales file", type=["csv", "xlsx"])
    if not upload:
        return

    tmp_path = Path("data") / f"_tmp_{upload.name}"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)

    _set_loading(loader_placeholder, True)
    try:
        with st.spinner("Processing file and updating the database..."):
            with open(tmp_path, "wb") as buffer:
                buffer.write(upload.getbuffer())

            df_raw = read_any_table(tmp_path, sheet=0)
            if df_raw is None or df_raw.empty:
                st.error("Uploaded file has no rows.")
                return

            df_raw = maybe_unpivot_square_wide(df_raw)

            refund_mask = None
            if "Event Type" in df_raw.columns:
                refund_mask = df_raw["Event Type"].astype(str).str.contains("refund", case=False, na=False)
            elif "Itemisation Type" in df_raw.columns:
                refund_mask = df_raw["Itemisation Type"].astype(str).str.contains("refund", case=False, na=False)

            df_norm, missing = normalize_sales_columns(df_raw)
            if missing:
                st.error("Missing required columns after auto-detect.")
                with st.expander("See detected columns and tips"):
                    st.write(
                        {
                            "Missing": missing,
                            "Detected headers in your file": list(df_raw.columns),
                        }
                    )
                    st.caption("Required columns (any order): date, item_name, quantity_sold.")
                return

            if refund_mask is not None and "quantity_sold" in df_norm.columns:
                quantities = pd.to_numeric(df_norm["quantity_sold"], errors="coerce")
                mask = refund_mask.reindex(df_norm.index).fillna(False).astype(bool)
                df_norm["quantity_sold"] = np.where(mask, -np.abs(quantities), quantities)

            df_final = coerce_and_aggregate_sales(df_norm)
            if df_final.empty:
                st.error("No valid rows remained after processing.")
                return

            errors = validate_sales(df_final)
            if errors:
                _display_validation_errors(errors)
                return

            dest = safe_replace_upload(tmp_path, "sales.xlsx")
            with pd.ExcelWriter(dest) as writer:
                df_final.to_excel(writer, index=False, sheet_name="sales")

            ingest_errors = ingest_sales(dest)
            if ingest_errors:
                _display_validation_errors(ingest_errors)
                return

            st.success("Sales data ingested successfully.")

            upload_dates = pd.to_datetime(df_final["date"], errors="coerce").dropna()
            if upload_dates.empty:
                st.warning("Could not determine upload date range for weather sync.")
            else:
                upload_start = upload_dates.min().date()
                upload_end = upload_dates.max().date()

                n_weather = 0
                try:
                    n_weather = upsert_weather_history_to_db(
                        DEFAULT_LOCATION, start=upload_start, end=upload_end
                    )
                except Exception as exc:
                    st.warning(f"Weather history not updated: {exc}")

                years = list(range(upload_start.year, upload_end.year + 1))
                n_holidays = 0
                try:
                    scope = HolidayScope(country=HOLIDAY_COUNTRY, subdiv=HOLIDAY_SUBDIV, years=years)
                    n_holidays = upsert_holidays_to_db(scope)
                except Exception as exc:
                    st.warning(f"Holidays not updated: {exc}")

                st.info(
                    f"Weather rows added: {n_weather}. "
                    f"Holidays ensured for {years[0]} to {years[-1]}."
                )

            _train_models_after_upload()
            st.session_state["uploaded_this_session"] = True
            st.session_state["latest_forecast"] = None
    finally:
        _set_loading(loader_placeholder, False)
        tmp_path.unlink(missing_ok=True)


def _render_configure_tab() -> None:
    st.subheader("Configure data sources")

    st.markdown("### Events (optional)")
    events_file = st.file_uploader(
        "Upload events (CSV/XLSX)",
        type=["csv", "xlsx"],
        key="events_file",
    )
    if events_file:
        tmp_path = Path("data") / f"_tmp_events_{events_file.name}"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(tmp_path, "wb") as buffer:
                buffer.write(events_file.getbuffer())
            events_df = read_any_table(tmp_path)
            if events_df is None or events_df.empty:
                st.warning("Uploaded events file had no rows.")
            else:
                st.dataframe(events_df.head(), use_container_width=True)
                if st.button("Save events", key="btn_save_events"):
                    try:
                        upsert_events(events_df)
                        st.success("Events saved. They will be considered in upcoming forecasts.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save events: {exc}")
        except Exception as exc:
            st.error(f"Could not read events file: {exc}")
        finally:
            tmp_path.unlink(missing_ok=True)

    st.markdown("### Manual weather for next week (optional)")
    weather_file = st.file_uploader(
        "Upload weather (CSV/XLSX) for the coming week",
        type=["csv", "xlsx"],
        key="weather_file",
    )
    if weather_file:
        tmp_path = Path("data") / f"_tmp_weather_{weather_file.name}"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(tmp_path, "wb") as buffer:
                buffer.write(weather_file.getbuffer())
            weather_df = read_any_table(tmp_path)
            if weather_df is None or weather_df.empty:
                st.warning("Uploaded weather file had no rows.")
            else:
                st.dataframe(weather_df.head(), use_container_width=True)
                if st.button("Save weather", key="btn_save_weather"):
                    try:
                        upsert_weather(weather_df, source="manual")
                        st.success("Weather saved. It will be used for the next forecast.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save weather: {exc}")
        except Exception as exc:
            st.error(f"Could not read weather file: {exc}")
        finally:
            tmp_path.unlink(missing_ok=True)

    st.markdown("---")
    st.markdown("### Auto-fetch data")

    with get_conn() as conn:
        w_min, w_max, w_rows = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM weather"
        ).fetchone()
        e_min, e_max, e_rows = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM events"
        ).fetchone()
        next_event = conn.execute(
            "SELECT date, event_name, event_type "
            "FROM events WHERE date >= date('now') "
            "ORDER BY date ASC LIMIT 1"
        ).fetchone()

    col1, col2, col3 = st.columns(3)
    col1.metric("Weather rows", w_rows or 0)
    col1.caption(f"{w_min or 'n/a'} to {w_max or 'n/a'}")
    col2.metric("Event rows", e_rows or 0)
    col2.caption(f"{e_min or 'n/a'} to {e_max or 'n/a'}")
    if next_event:
        col3.write(f"Next event: {next_event[0]} - {next_event[1]} ({next_event[2] or 'type n/a'})")
    else:
        col3.write("Next event: none scheduled")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Weather: refresh next 7 days"):
            try:
                count = upsert_weather_forecast_to_db(DEFAULT_LOCATION)
                st.success(f"7 day weather updated ({count} rows).")
                st.rerun()
            except Exception as exc:
                st.error(f"Weather update failed: {exc}")
    with col2:
        if st.button("Holidays: refresh upcoming (next 90 days)"):
            try:
                start = date.today()
                end = start + timedelta(days=90)
                years = list(range(start.year, end.year + 1))
                scope = HolidayScope(country=HOLIDAY_COUNTRY, subdiv=HOLIDAY_SUBDIV, years=years)
                count = upsert_holidays_to_db(scope)
                st.success(f"Holidays ensured for {years[0]} to {years[-1]} (covers the next 90 days).")
                st.rerun()
            except Exception as exc:
                st.error(f"Holiday update failed: {exc}")


def _render_preview_tab() -> None:
    st.subheader("Generate recommendations (next week)")

    use_ml = st.checkbox("Smart forecasting (improves accuracy)", value=True)
    ml_blend = st.slider(
        "AI emphasis",
        min_value=0.0,
        max_value=1.0,
        value=0.5,
        step=0.05,
        help="0 = rely on historical pattern only, 1 = rely fully on AI.",
    )

    with get_conn() as conn:
        min_d, max_d, rows = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM sales_data"
        ).fetchone()
        w_min, w_max, w_cnt = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM weather"
        ).fetchone()
        e_min, e_max, e_cnt = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM events"
        ).fetchone()

    if rows:
        st.caption(f"Sales history: {min_d or 'n/a'} to {max_d or 'n/a'} ({rows} rows).")
    else:
        st.warning("No sales data found. Upload history on the Upload tab.")

    st.caption(
        f"Weather data: {w_min or 'n/a'} to {w_max or 'n/a'} ({w_cnt or 0} rows). "
        f"Events data: {e_min or 'n/a'} to {e_max or 'n/a'} ({e_cnt or 0} rows)."
    )

    need_upload_now = not st.session_state.get("uploaded_this_session", False)
    if need_upload_now:
        st.info("Upload a sales file on the Upload tab to generate a fresh forecast.")

    if st.button("Generate forecast", disabled=need_upload_now):
        with st.spinner("Building forecast..."):
            df = generate_forecast(use_ml=use_ml, ml_blend=float(ml_blend))
        if df is None or df.empty:
            st.warning("Not enough data to forecast. Please upload more sales history.")
        else:
            st.success("Forecast ready.")
            st.dataframe(df, use_container_width=True)
            st.session_state["latest_forecast"] = df
            st.session_state["uploaded_this_session"] = False


def _send_email_with_attachment(
    file_path: Path,
    to_list: list[str],
    cc_list: list[str],
    bcc_list: list[str],
    subject: str,
    body: str,
) -> bool:
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
            "under a `[smtp]` section."
        )
        return False

    if not host:
        st.error("SMTP host is not configured.")
        return False

    message = EmailMessage()
    message["From"] = user or host
    message["To"] = ", ".join(to_list)
    if cc_list:
        message["Cc"] = ", ".join(cc_list)
    message["Subject"] = (subject or "").strip() or "Order sheet"
    message.set_content(body or "")

    file_bytes = file_path.read_bytes()
    mime, _ = mimetypes.guess_type(file_path.name)
    maintype, subtype = (mime or "application/octet-stream").split("/", 1)
    message.add_attachment(file_bytes, maintype=maintype, subtype=subtype, filename=file_path.name)

    recipients = list(dict.fromkeys(to_list + cc_list + bcc_list))
    try:
        if use_tls:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        if user and password:
            server.login(user, password)
        server.send_message(message, to_addrs=recipients)
        server.quit()
        return True
    except Exception as exc:
        st.error(f"Email send failed: {exc}")
        return False


def _render_download_tab() -> None:
    st.subheader("Export order sheet")

    df = st.session_state.get("latest_forecast")
    if df is None or df.empty:
        st.info("Go to Preview and generate a forecast first.")
        return

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_path = Path("outputs") / f"order_sheet_{ts}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path) as writer:
        df.to_excel(writer, index=False, sheet_name="OrderSheet")

    action = st.selectbox(
        "Choose an action",
        ["Download the file", "Email the file"],
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
        to_addr = st.text_input(
            "To (comma-separated)",
            placeholder="owner@example.com, manager@example.com",
        )
        subject = st.text_input("Subject", value=f"Order sheet - {ts}")
        body = st.text_area(
            "Message",
            value="Hi,\n\nPlease find this week's order sheet attached.\n\nThanks.",
        )

        col_cc, col_bcc = st.columns(2)
        with col_cc:
            cc_addr = st.text_input("CC (optional)", placeholder="ops@example.com")
        with col_bcc:
            bcc_addr = st.text_input("BCC (optional)", placeholder="")

        if st.button("Send email"):
            to_list = [x.strip() for x in (to_addr or "").split(",") if x.strip()]
            cc_list = [x.strip() for x in (cc_addr or "").split(",") if x.strip()]
            bcc_list = [x.strip() for x in (bcc_addr or "").split(",") if x.strip()]

            if not to_list:
                st.warning("Please enter at least one recipient.")
            else:
                with st.spinner("Sending email..."):
                    ok = _send_email_with_attachment(out_path, to_list, cc_list, bcc_list, subject, body)
                if ok:
                    st.success("Email sent successfully.")


def _render_history_tab() -> None:
    st.subheader("Recent forecasts")

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
        return

    hist["created_at"] = pd.to_datetime(hist["created_at"])
    hist["week_start_date"] = pd.to_datetime(hist["week_start_date"]).dt.date

    runs = (
        hist.groupby(["week_start_date", "created_at"])
        .size()
        .reset_index(name="items")
        .sort_values(["created_at", "week_start_date"], ascending=[False, False])
        .reset_index(drop=True)
    )

    def run_label(row):
        count = int(row["items"])
        created = row["created_at"].strftime("%Y-%m-%d %H:%M")
        return f"Week {row['week_start_date']} - created {created} - {count} items"

    choices = runs.index.tolist()
    selected_idx = st.selectbox(
        "Select a forecast run",
        options=choices,
        format_func=lambda i: run_label(runs.loc[i]),
    )

    sel = runs.loc[selected_idx]
    sel_week = sel["week_start_date"]
    sel_created = sel["created_at"]

    df_run = hist[
        (hist["week_start_date"] == sel_week)
        & (hist["created_at"] == sel_created)
    ].copy()

    df_view = df_run.rename(
        columns={
            "item_name": "Item Name",
            "mon": "MON",
            "tue": "TUE",
            "wed": "WED",
            "thu": "THURS",
            "fri": "FRI",
            "sat": "SAT",
            "alerts": "Notes",
        }
    )[["Item Name", "MON", "TUE", "WED", "THURS", "FRI", "SAT", "Notes"]]

    st.markdown(
        f"**Week:** {sel_week}  |  "
        f"**Created:** {sel_created:%Y-%m-%d %H:%M}  |  "
        f"**Items:** {len(df_view)}"
    )
    st.dataframe(df_view, use_container_width=True)

    ts = f"{sel_week}_{sel_created:%Y-%m-%d_%H%M%S}"
    out_path = Path("outputs") / f"order_sheet_{ts}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path) as writer:
        df_view.to_excel(writer, index=False, sheet_name="OrderSheet")

    st.download_button(
        "Download this run",
        data=out_path.read_bytes(),
        file_name=out_path.name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with st.expander("Previous runs (headlines)"):
        for _, row in runs.iloc[1:].head(15).iterrows():
            st.write(
                f"- Week {row['week_start_date']} - created {row['created_at']:%Y-%m-%d %H:%M} - {int(row['items'])} items"
            )


def _render_admin_tab() -> None:
    auth = st.session_state.get("auth") or {}
    if str(auth.get("role", "")).lower() != "admin":
        st.stop()

    from app.auth import (
        create_user,
        start_password_reset,
        complete_password_reset,
    )

    st.subheader("Admin")

    st.markdown("### User management")
    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown("**Create user**")
        new_username = st.text_input("Username (new)", key="admin_new_username")
        new_password = st.text_input("Password", type="password", key="admin_new_password")
        new_role = st.selectbox("Role", ["user", "admin"], index=0, key="admin_new_role")
        if st.button("Create user", key="admin_create_user"):
            if not new_username or not new_password:
                st.error("Username and password are required.")
            else:
                try:
                    create_user(new_username, new_password, new_role)
                    st.success(f"User created: {new_username} ({new_role})")
                except Exception as exc:
                    st.error(str(exc))

    with col2:
        st.markdown("**Forgot password (one-time code)**")
        reset_username = st.text_input("Username", key="admin_reset_username")
        col_gen, col_set = st.columns(2)

        with col_gen:
            if st.button("Generate code", key="admin_generate_code"):
                try:
                    code = start_password_reset(reset_username)
                except Exception as exc:
                    st.error(f"Could not start password reset: {exc}")
                else:
                    if code:
                        st.success("One-time code generated (valid 15 minutes).")
                        st.code(code, language="text")
                    else:
                        st.error("User not found or password reset not supported.")

        reset_code = st.text_input("One-time code", key="admin_reset_code")
        new_password_value = st.text_input("New password", type="password", key="admin_reset_new_password")

        with col_set:
            if st.button("Set new password", key="admin_set_new_password"):
                if not (reset_username and reset_code and new_password_value):
                    st.error("All fields are required.")
                else:
                    try:
                        ok = complete_password_reset(reset_username, reset_code, new_password_value)
                    except Exception as exc:
                        st.error(f"Could not reset password: {exc}")
                    else:
                        if ok:
                            st.success("Password updated.")
                        else:
                            st.error("Invalid or expired code.")

    st.divider()
    st.markdown("### Improve accuracy")
    st.caption("Rebuild recommendations by training or retraining per-item models.")
    if st.button("Train models now", key="admin_train_models"):
        try:
            with st.spinner("Training models..."):
                results = train_models_for_all_items(min_samples=10)
            trained = sum(1 for r in results if getattr(r, "saved", False))
            st.success(f"Trained or updated models for {trained} items.")
        except Exception as exc:
            st.error(f"Training failed: {exc}")


def main() -> None:
    _init_session_state()
    loader_placeholder = _render_loader()

    auth = st.session_state.get("auth")
    if not auth:
        login_ui()
        return

    tab = _sidebar_header(auth)
    st.title("Hudson Bakery: Forecasting Pipeline")

    if tab == "Upload":
        _render_upload_tab(loader_placeholder)
    elif tab == "Configure":
        _render_configure_tab()
    elif tab == "Preview":
        _render_preview_tab()
    elif tab == "Download":
        _render_download_tab()
    elif tab == "History":
        _render_history_tab()
    elif tab == "Admin":
        _render_admin_tab()
    else:
        st.write("Select an option from the sidebar.")


if __name__ == "__main__":
    main()

