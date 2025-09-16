1) Prerequisites

Python 3.11 or newer

Windows: download from https://www.python.org/downloads/
 (check “Add Python to PATH” during install).

macOS: brew install python@3.11 (or use the official pkg).

Linux: use your package manager or python.org.

Git (optional but recommended): https://git-scm.com/downloads

Alternatively, you can download the repo as a ZIP and extract it.

SQLite is already built into Python — no separate install needed.

2) Get the project
Option A — clone (recommended)
# Pick a folder where you want the project to live
git clone https://github.com/<your-org-or-user>/<repo-name>.git
cd <repo-name>

Option B — download ZIP

Click “Code → Download ZIP” on GitHub, unzip, then cd into the project folder.

3) Create a virtual environment & install packages

Run all commands from the project folder (where pyproject.toml and requirements.txt live).

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


If you see “No module named X” later, re-activate the venv and run python -m pip install <package> or re-install -r requirements.txt.

4) Initialize the database & user tables (one-time)

These commands ensure the DB file exists and that the password reset columns are present.

# Always run from the project root
# Use the venv you created above

# Make sure the migrations module runs in "package" mode:
python -m app.migrations_add_otp


This prints:

OK: users table has password_salt, reset_code, reset_expires


The app uses app.sqlite in the project root. Back it up like any file if you want to move users/history between machines.

5) Create the first admin (one-time)
Option A — simple interactive script
python create_admin.py
# it will prompt:
# Username: (press Enter for "admin" or type another)
# Password: *******
# Confirm password: *******
# prints: Admin created: id=<n>, username=<name>

Option B — force/reset an admin from the command line

(Useful if you need to reset a broken password)

# Replace values as you like
python -m app.tools.reset_admin --username admin --password "NewStrongPassword!"


If you see “no such column: is_active” it’s safe — we no longer require that column. The tool will still update password & role.

6) Start the app
Windows (PowerShell)
# From the project root with venv active
python -m streamlit run app/ui_app.py

macOS/Linux (bash/zsh)
python -m streamlit run app/ui_app.py


It will print a local URL (usually http://localhost:8501
). Open it in your browser.

7) Daily usage (for the client)

Sign in with the admin account you created.

If a user forgets the password:
Admin → “Forgot password (one-time code)” → generate a code → share it with the user → the user uses the “Forgot password?” expander on the login screen to reset.

Upload

Go to Upload, drop the weekly Excel/CSV.

The app auto-normalizes columns (maps Square field names), handles refunds, aggregates duplicates, and ingests into the DB.

After ingest, the system quietly trains per-item models where there’s enough data.

Configure (optional)

If you want, upload manual weather or events.

The Admin page also has buttons to refresh upcoming holidays and backup the DB.

Preview

Generate next week’s recommendations.

Optionally toggle Smart Forecasting (blended with the baseline).

Download the output from Download tab.

History

View previously generated forecast snapshots grouped by run time; select one to view.

8) What’s in requirements.txt

If you need to regenerate or check it, here’s a safe set that matches the code you’ve been running:

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


If you’re completely offline at the client site, you’ll need to pre-download these wheels and install them from a USB stick; otherwise pip will fetch them automatically.

9) Where passwords & users live

Users & passwords are stored in app.sqlite, table users.

Passwords use PBKDF2-HMAC-SHA256 with a per-user salt.

If your colleague can’t log in on their laptop, either:

They’re using a different app.sqlite that doesn’t have that user
→ copy your app.sqlite to their machine; or

They’re using the right DB but forgot the password
→ on their laptop, run the admin reset:

# With that laptop's venv active and inside the project
python -m app.tools.reset_admin --username admin --password "ResetMe!123"

10) Common problems & instant fixes

streamlit: command not found
Use the venv and module form:
python -m streamlit run app/ui_app.py

No module named 'app' or imports fail
You’re likely not in the project root. cd into the folder that contains the app/ directory and run commands as python -m app.xxx.

ModuleNotFoundError: No module named meteostat (or others)
Activate the venv and run: python -m pip install -r requirements.txt

binascii.Error: Non-hexadecimal digit found when logging in
Your DB has an old/invalid salt. Reset the admin password:
python -m app.tools.reset_admin --username admin --password "NewStrongPassword!"

NOT NULL constraint failed: users.created_at
We fixed the code to tolerate DBs without created_at/updated_at. If you still see this, either run a quick migration to add the columns, or recreate the DB:

# add nullable timestamps if you want them
python - <<'PY'


from app.db import get_conn
with get_conn() as c:
try: c.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
except: pass
try: c.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")
except: pass
c.commit()
print("Done.")
PY


---

# 11) Backups & moving to another laptop

- The entire database is the `app.sqlite` file in the project root.  
- Back it up from **Admin → Create & download DB backup**, or copy the file manually when the app isn’t running.
- Restore by replacing the `app.sqlite` file in the new machine’s project folder.

---

# 12) Updating the app

When you push changes to GitHub:

```bash
# on the client machine
cd <repo-name>
git pull
# ensure venv is active
python -m pip install -r requirements.txt
python -m streamlit run app/ui_app.py
