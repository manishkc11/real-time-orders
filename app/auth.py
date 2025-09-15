# app/auth.py
from app.db import get_conn

import hashlib
import secrets
import binascii
from datetime import datetime, timedelta

PBKDF_ROUNDS = 100_000


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """
    Return (salt_hex, hash_hex) using PBKDF2-HMAC-SHA256.
    """
    if salt_hex:
        salt = binascii.unhexlify(salt_hex)
    else:
        salt = secrets.token_bytes(16)
        salt_hex = binascii.hexlify(salt).decode()

    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF_ROUNDS)
    hash_hex = binascii.hexlify(dk).decode()
    return salt_hex, hash_hex


def verify_password(password: str, salt_hex: str, stored_hash_hex: str) -> bool:
    _, test_hash = hash_password(password, salt_hex)
    return secrets.compare_digest(test_hash, stored_hash_hex)


def create_user(username: str, password: str, role: str = "user") -> int:
    username = (username or "").strip().lower()
    salt_hex, hash_hex = hash_password(password)
    with get_conn() as c:
        c.execute(
            "INSERT INTO users(username, password_hash, password_salt, role) "
            "VALUES (?,?,?,?)",
            (username, hash_hex, salt_hex, role),
        )
        c.commit()
        return c.lastrowid


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Return {"id","username","role"} if credentials are valid, else None.
    """
    username = (username or "").strip().lower()
    with get_conn() as c:
        row = c.execute(
            "SELECT id, username, role, password_hash, password_salt "
            "FROM users WHERE username=?",
            (username,),
        ).fetchone()

    if not row:
        return None

    uid, uname, role, pwd_hash, salt_hex = row
    if salt_hex and pwd_hash and verify_password(password, salt_hex, pwd_hash):
        return {"id": uid, "username": uname, "role": role}
    return None


# Backward-compat so old code importing `authenticate` still works
authenticate = authenticate_user


def list_users() -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    return [{"id": r[0], "username": r[1], "role": r[2]} for r in rows]


# ---- One-time password reset ----

def start_password_reset(username: str, *, ttl_minutes: int = 15) -> str | None:
    """
    Generate a one-time code valid for ttl_minutes. Returns the code (caller
    should send it to the user via email/SMS). Returns None if user not found.
    """
    username = (username or "").strip().lower()
    with get_conn() as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return None
        uid = row[0]
        code = secrets.token_urlsafe(8)
        expires = (datetime.utcnow() + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")
        c.execute("UPDATE users SET reset_code=?, reset_expires=? WHERE id=?",
                  (code, expires, uid))
        c.commit()
        return code


def complete_password_reset(username: str, code: str, new_password: str) -> bool:
    """
    Validate code and set a new password. Returns True on success.
    """
    username = (username or "").strip().lower()
    code = (code or "").strip()

    with get_conn() as c:
        row = c.execute(
            "SELECT id, reset_code, reset_expires FROM users WHERE username=?",
            (username,),
        ).fetchone()

        if not row:
            return False

        uid, stored_code, expires = row
        if not stored_code or stored_code != code:
            return False
        if expires and datetime.utcnow() > datetime.fromisoformat(expires):
            return False

        salt_hex, hash_hex = hash_password(new_password)
        c.execute(
            "UPDATE users "
            "SET password_hash=?, password_salt=?, reset_code=NULL, reset_expires=NULL "
            "WHERE id=?",
            (hash_hex, salt_hex, uid),
        )
        c.commit()
        return True


# For places that used an older name:
redeem_password_reset = complete_password_reset
