from app.db import get_conn
with get_conn() as c:
    rows = c.execute("""
        SELECT COALESCE(i.canonical_name, s.item_name) AS name,
               s.item_id,
               COUNT(*) AS n_rows
        FROM sales_data s
        LEFT JOIN items i ON i.id = s.item_id
        GROUP BY s.item_id, COALESCE(i.canonical_name, s.item_name)
        ORDER BY n_rows DESC
        LIMIT 20
    """).fetchall()
    print("Top 20 items by rows:")
    for name, iid, n in rows:
        print(f"{n:4}  id={iid}  {name}")
