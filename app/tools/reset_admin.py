# app/tools/reset_admin.py
import argparse
from app.db import get_conn
from app.auth import hash_password

def ensure_admin(username: str, password: str) -> int:
    username = (username or "").strip().lower()
    salt_hex, hash_hex = hash_password(password)

    with get_conn() as c:
        row = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            uid = row[0]
            c.execute(
                """
                UPDATE users
                   SET password_hash = ?, password_salt = ?, role = 'admin'
                 WHERE id = ?
                """,
                (hash_hex, salt_hex, uid),
            )
        else:
            c.execute(
                """
                INSERT INTO users(username, password_hash, password_salt, role)
                VALUES (?,?,?, 'admin')
                """,
                (username, hash_hex, salt_hex),
            )
            uid = c.lastrowid
        c.commit()
        return uid

def main():
    p = argparse.ArgumentParser(description="Create or reset an admin user")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    args = p.parse_args()

    uid = ensure_admin(args.username, args.password)
    print(f"OK: admin user '{args.username}' (id={uid}) reset/created.")

if __name__ == "__main__":
    main()
