Real-Time Order Updating System

Predict next week’s baking/ordering by blending a weekday baseline with weather, holidays, and per-item ML models (“Smart Forecasting”). Upload weekly sales, preview recommendations, export the order sheet, and manage users—no data science jargon required.

Features

Upload weekly sales (Excel/CSV). Auto-maps Square exports, handles refunds, aggregates duplicates.

Forecast next week (Mon–Sat) with:

Baseline: recent weekday pattern with decay/recency.

Smart Forecasting: optional ML for items with enough history (blend with baseline).

Adjustments: weather and events/holidays.

Download the order sheet as XLSX.

History of forecast snapshots (grouped by run time).

Admin

Create users, reset passwords via one-time code.

One-click DB backup (downloadable).

Prebuild/download next week snapshot.

SQLite storage (single portable file: app.sqlite).

How it works (at a glance)

Baseline → EW mean of last ~8 weeks per weekday.

Weather/Events → apply light multiplicative deltas by day.

Smart Forecasting → if an item has enough samples, we train and blend ML predictions with the baseline (you control the “AI emphasis” slider).

Batch floors & alerts → enforce minimums and flag outliers vs history.

Requirements

Python 3.11+

Optional: Git (for pulling updates)

Internet (for first-time pip install; weather/holiday auto-fetch uses public APIs)

SQLite ships with Python; nothing else to install.

Quick Start
1) Clone or download
git clone https://github.com/<your-org-or-user>/<repo-name>.git
cd <repo-name>


—or download ZIP → unzip → cd into the folder.

2) Create a virtual environment & install packages

Windows (PowerShell)

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt


macOS/Linux (bash/zsh)

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

3) Initialize the database (one-time)
python -m app.migrations_add_otp
# prints: OK: users table has password_salt, reset_code, reset_expires

4) Create the first admin (one-time)
python create_admin.py
# Username: admin
# Password: ******
# Confirm password: ******
# Admin created: id=..., username=admin


Forgot the admin password? You can force a reset:

python -m app.tools.reset_admin --username admin --password "NewStrongPassword!"

5) Run the app
python -m streamlit run app/ui_app.py


Open the URL shown (usually http://localhost:8501
) and sign in.

Typical Workflow (for staff)

Upload → drop your weekly sales file.
The app will normalize headers, process refunds, aggregate duplicates, ingest to DB, and quietly train models for items with enough history.

Preview → set AI emphasis slider, optionally enable Smart Forecasting, and click Generate forecast.

Download → export the order sheet as .xlsx.

History → browse previous forecasts by run time.

Admin (admins only) → create users, reset passwords, DB backup, prebuild next week snapshot.

Data Expectations

Required columns after auto-detect/normalize:
date, item_name, quantity_sold

Optional columns (auto-detected if present):
Event Type (to detect refunds), Itemisation Type, device_store, is_promo

Square exports with dates as columns are supported—the app unpivots to the normalized schema automatically.

Admin Guide
User management

Create user → set username, password, role (user or admin).

Forgot password (one-time code)

Enter username → Generate code → share code with the user.

User goes to Sign in → Forgot password? → enters username, code, new password.

Database backup

Admin → Create & download DB backup
Produces outputs/backup_YYYY-MM-DD_HHMMSS.sqlite and a “Download backup” button.

Prebuild next week snapshot

Admin → Prebuild next order sheet (snapshot)
Generates outputs/order_snapshot_YYYY-MM-DD_HHMM.xlsx for easy sharing.

Project Layout
app/
  __init__.py
  ui_app.py                 # Streamlit UI (main app)
  auth.py                   # users, hashing, one-time reset codes
  db.py                     # get_conn() for SQLite
  io_utils.py               # safe file ops
  pipeline.py               # ingestion + forecasting pipeline
  model_train.py            # training per-item models
  services/
    weather_service.py      # weather history/forecast upserts
    holiday_service.py      # public holidays upserts
  tools/
    reset_admin.py          # CLI admin reset tool
  migrations_add_otp.py     # adds reset_code/reset_expires if missing
app.sqlite                  # (auto-created) SQLite DB
requirements.txt
pyproject.toml (optional)

Troubleshooting

streamlit: command not found
Use the venv + module form: python -m streamlit run app/ui_app.py

No module named app or import errors
Ensure you’re in the repo root (the folder that contains app/) and the venv is active. Use python -m ... form (e.g., python -m app.migrations_add_otp).

ModuleNotFoundError: No module named 'meteostat'
Re-activate the venv and python -m pip install -r requirements.txt.

Login error: binascii.Error: Non-hexadecimal digit found
Reset the admin password on this machine’s DB:
python -m app.tools.reset_admin --username admin --password "ResetMe!123"

User can’t log in on another laptop
Either copy your app.sqlite to the other laptop, or create/reset the user there with the same tool:
python -m app.tools.reset_admin --username <user> --password "<NewPass>"

Security Notes

Passwords are hashed with PBKDF2-HMAC-SHA256 and per-user salts.

One-time reset codes expire after 15 minutes.

SQLite file (app.sqlite) contains all data (users, sales, models, forecasts). Treat it like any credential store: restrict access and back it up securely.

Updating the App
git pull
# ensure venv is active
python -m pip install -r requirements.txt
python -m streamlit run app/ui_app.py

License

Choose a license (MIT, Apache-2.0, etc.) and add it here.

Support / Contributing

Issues & PRs welcome!

Keep changes backwards-compatible with SQLite schema where possible.

For new dependencies, update requirements.txt.

requirements.txt (reference)
streamlit>=1.36
pandas>=2.2
numpy>=1.26
openpyxl>=3.1
XlsxWriter>=3.2
python-dateutil>=2.9
pytz>=2024.1
meteostat>=1.6
requests>=2.32
holidays>=0.53
scikit-learn>=1.5
statsmodels>=0.14
