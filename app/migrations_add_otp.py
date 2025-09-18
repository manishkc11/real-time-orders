# app/migrations_add_otp.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import get_conn

def _has_column(c, table, col):
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)

with get_conn() as c:
    # Add reset_code / reset_expires columns if missing
    if not _has_column(c, "users", "reset_code"):
        c.execute("ALTER TABLE users ADD COLUMN reset_code TEXT")
    if not _has_column(c, "users", "reset_expires"):
        c.execute("ALTER TABLE users ADD COLUMN reset_expires TEXT")
    # Add password_salt if your DB is older and missing it
    if not _has_column(c, "users", "password_salt"):
        # default '' then fill later when user changes password
        c.execute("ALTER TABLE users ADD COLUMN password_salt TEXT NOT NULL DEFAULT ''")
    c.commit()

print("OK: users table has password_salt, reset_code, reset_expires")
