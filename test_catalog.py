from app.db import get_conn, resolve_item_id, DB_PATH
c = get_conn()
print("DB:", DB_PATH)
iid1 = resolve_item_id(c, "Hot Choc R")
iid2 = resolve_item_id(c, "Hot Choc L")
iid3 = resolve_item_id(c, "Hot Chocolate")  # canonical target if rule matches
print("IDs:", iid1, iid2, iid3)
aliases = c.execute("SELECT alias, item_id FROM item_aliases ORDER BY 1").fetchmany(10)
items = c.execute("SELECT id, canonical_name FROM items ORDER BY 1").fetchmany(10)
print("Aliases sample:", aliases)
print("Items sample:", items)
c.close()
