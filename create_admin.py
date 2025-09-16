# create_admin.py
from app.auth import start_password_reset, complete_password_reset

from getpass import getpass
from app.db import get_conn  # ensures DB/migrations exist
from app.auth import create_user

if __name__ == "__main__":
    # touch DB so migrations/tables exist
    with get_conn() as c:
        pass

    print("Create first admin user")
    username = input("Username: ").strip().lower() or "admin"
    p1 = getpass("Password: ")
    p2 = getpass("Confirm password: ")
    if p1 != p2:
        print("Passwords do not match.")
        raise SystemExit(1)

    uid = create_user(username, p1, role="admin")
    print(f"Admin created: id={uid}, username={username}")
