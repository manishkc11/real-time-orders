# app/tools/audit_users.py
from app.db import get_conn

def is_hex(s):
    try:
        if not s:
            return False
        bytes.fromhex(s)
        return True
    except ValueError:
        return False

with get_conn() as c:
    rows = c.execute("SELECT id, username, role, password_salt FROM users ORDER BY id").fetchall()

bad = []
for r in rows:
    uid, u, role, salt = r
    if not is_hex(salt):
        bad.append((uid, u, role, salt))

if not bad:
    print("All users have valid salts.")
else:
    print("Users with invalid salts:")
    for row in bad:
        print(row)
