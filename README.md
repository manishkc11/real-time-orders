# Hudson Bakery: Forecasting Pipeline🍞📊

A **Streamlit application** for bakeries to predict next week’s baking quantities in real time.  
Built for Hudson’s Bakery (Bondi Junction) as part of a capstone project.

---

## 🚀 Features

- **Upload & Normalize Sales**: Upload CSV/XLSX exports (Square “wide” or tidy format).
- **Automatic Enrichment**: Weather and public holiday data pulled and merged.
- **Forecast Generation**: Predict next week’s baking quantities (Mon–Sat).
- **Export**: Save forecasts to Excel; automatic archiving of past runs.
- **Email Option**: (Configurable) send forecasts via email.
- **History Browsing**: View previous forecasts and database backups.
- **User Management**: Login with roles (admin/user), create users, OTP-based password reset.
- **Model Retraining**: Improve accuracy by retraining item-level models.

---

## 🛠️ Tech Stack

- [Python 3.10+](https://www.python.org/)
- [Streamlit](https://streamlit.io/)
- [SQLite](https://www.sqlite.org/)
- [scikit-learn](https://scikit-learn.org/stable/)
- [pandas](https://pandas.pydata.org/)
- [meteostat](https://meteostat.net/) + [holidays](https://pypi.org/project/holidays/) for weather & public holiday data

---

## 📂 Project Structure

```
real-time-orders-main/
├─ app/
│  ├─ ui_app.py             # Streamlit front-end
│  ├─ pipeline.py           # Ingestion → forecast → export workflow
│  ├─ model_train.py        # Per-item ML training & prediction
│  ├─ validate.py           # File schema checks & Square “wide” → tidy
│  ├─ db.py                 # SQLite schema, migrations, item aliasing
│  ├─ auth.py               # User login, roles, OTP reset
│  ├─ services/
│  │  ├─ weather_service.py # Meteostat + Open-Meteo forecast
│  │  └─ holiday_service.py # AU/NSW holidays & manual events
│  └─ tools/                # Admin helpers
├─ data/
│  ├─ active/               # Current upload (sales.xlsx)
│  └─ archive/              # Archived uploads
├─ outputs/                 # Forecast Excel files & DB backups
├─ create_admin.py          # Bootstrap first admin user
├─ train_models.py          # Train/retrain all item models
├─ requirements.txt         # Python dependencies
└─ pyproject.toml           # Alt. dependency definition
```

---

## ⚙️ Installation

Clone the repo:

```bash
git clone https://github.com/manishkc11/real-time-orders.git
cd real-time-orders-main
```


Install dependencies:

```bash
pip install -r requirements.txt
# or
pip install -e .
```

---
Initialize the database (one-time)
```bash
 
python -m app.migrations_add_otp
```


## ▶️ Usage

1. **Create the first admin user** (only once):
   ```bash
   python create_admin.py
   ```

2. **Run the app**:
   ```bash
    .\.venv\Scripts\Activate.ps1
   streamlit run app/ui_app.py 
   ```

3. **Login** with your admin account.

4. **Workflow**:
   - Upload sales CSV/XLSX.
   - Load/refresh weather and holidays.
   - Generate forecast (Mon–Sat).
   - Export to Excel (auto-saved in `outputs/`).
   - (Optional) Email the forecast.

---

## 📊 Models & Forecasting

- Each item gets its own **ridge regression model**.
- Features used:
  - Day of week (Mon–Sat)
  - Weather (temperature, rain)
  - Public holidays & manual events
- Models are stored in the SQLite DB.
- Retrain via:
  - Admin → “Improve accuracy”
  - or CLI:  
    ```bash
    python train_models.py
    ```

---

## 🔐 Security Notes

- Passwords hashed with PBKDF2-HMAC-SHA256 + per-user salt.
- Supports OTP-style password reset.
- **Secrets (SMTP, DB paths, etc.)** should be kept in `.streamlit/secrets.toml` and **never committed** to Git.

---

## 📜 License

This project was developed as part of an academic capstone of Wentworth Institute of Higher Education, Surry Hills.  
For commercial use, please contact the authors.

---

## 👥 Contributors

- Enosh Basnet 
- Rabin Shiwakoti
- Manish Chaudhary
- Ashok Pandey
