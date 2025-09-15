real-time-orders/
├─ app/
│  ├─ __init__.py
│  ├─ ui_app.py                 # Streamlit UI
│  ├─ db.py                     # SQLite + migrations
│  ├─ pipeline.py               # ingest + forecast
│  ├─ io_utils.py               # file handling
│  ├─ validate.py               # input validation/auto-detect
│  ├─ model_train.py            # Smart Forecasting (per-item models)
│  ├─ auth.py                   # users, login, OTP reset
│  ├─ services/
│  │  ├─ weather_service.py     # Meteostat history + forecast
│  │  └─ holiday_service.py     # Public holidays
│  └─ tools/
│     └─ reset_admin.py         # create/reset an admin user (CLI)
├─ data/                        # (ignored) uploads, samples
│  └─ README.md
├─ outputs/                     # (ignored) exports & backups
├─ .streamlit/
│  └─ config.toml               # theme (optional)
├─ app.sqlite                   # (ignored) local database
├─ README.md                    # 👈 your main documentation
├─ requirements.txt             # or use pyproject.toml
├─ .gitignore
└─ LICENSE                      # (MIT suggested)

-----------------------------------------------
password NewStrongPassword! usnername sandra
================
# Python
__pycache__/
*.py[cod]
*.egg-info/

# venv / tooling
.venv/
.env
.env.*

# Streamlit
.streamlit/secrets.toml

# Project data
app.sqlite
outputs/
data/
!data/README.md

# OS/editor cruft
.DS_Store
Thumbs.db
.vscode/
---------------------------------------------------------
requirements.txt (if you’re not using pyproject.toml)
streamlit>=1.36
pandas>=2.2
numpy>=1.26
scikit-learn>=1.4
meteostat>=1.6
holidays>=0.52
python-dateutil>=2.9
-------------------------
# Real-Time Order Updating System

A Streamlit app that predicts next week’s baking quantities by learning from past sales, while adjusting for **weather** and **public holidays**. Designed for non-technical bakery staff with a simple “Upload → Preview → Download” workflow.

---

## ✨ Features

- **Upload** weekly sales (CSV/XLSX) — auto-detects/normalizes Square exports
- **Smart Forecasting**: blends a weekday baseline with per-item ML models
- **Weather & Holidays**: auto-fetch history/forecast; adjust recs
- **Download** a clean order sheet (XLSX)
- **History** of past forecasts
- **Admin**:
  - create users
  - one-time code password reset (OTP)
  - retrain models (“Improve accuracy”)

---

## 🔧 Quick start

### 1) Clone & install

```bash
git clone <your-repo-url>
cd real-time-orders

# Option A: requirements.txt
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

# Option B: if you use pyproject.toml (poetry/hatch/pip-tools), follow your team’s standard.
Run the app
streamlit run app/ui_app.py
The app creates/updates app.sqlite automatically. 🧭 How to use

Upload
Drop your sales file (CSV/XLSX). The app will:

auto-map headers (date, item_name, quantity_sold)

detect/refund rows (flip sign)

aggregate duplicates

ingest into the database

After upload, the app also fetches the required weather history and holidays that match your data range.

Configure (optional)

Upload events (e.g., promos) with % uplift

Upload manual weather for next week (optional override)

Buttons to refresh next 7-day weather and upcoming holidays

Preview

Toggle Smart Forecasting (improves accuracy)

Adjust AI emphasis slider (how much to trust ML vs. historical baseline)

Click Generate forecast to see Mon–Sat baking recs

Download

Export the current forecast as Excel

(Optional) email sharing (if you add that integration later)

History

Browse saved forecasts by week & timestamp

Admin

Create user

Forgot password (generate one-time reset code; user redeems it on login screen)

Improve accuracy (retrain models now)

🗂️ Data model (SQLite)

users(id, username, role, password_hash, password_salt, reset_code, reset_expires)

sales_data(date, item_name, quantity_sold, item_id, …)

items(id, canonical_name, …)

weather(date, max_temp, rain_mm, source)

events(date, event_name, event_type, uplift_pct)

holidays(date, name, country, subdiv)

models(item_id, algo, model_blob, features_json, n_samples, cv_mape, updated_at)

forecasts(week_start_date, item_name, mon, tue, wed, thu, fri, sat, alerts, reasoning, created_at)

Tables are created/migrated automatically by app/db.py when the app or tools touch the DB.

🔐 Security & privacy

Do not commit app.sqlite, data/, or outputs/ (already ignored).

Passwords are hashed with PBKDF2-HMAC-SHA256 and unique per-user salts.

One-time codes are short-lived (15 min). In production you’d email/SMS them.

🧪 Local tips

If you need to reset an admin again:
python -m app.tools.reset_admin --username admin --password "NewStrongPassword!"

If Meteostat blocks anonymous traffic at your IP, add a cache or run once on a different network. (The app tolerates brief outages.)
--------------------------------------------------------------
