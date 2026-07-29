"""Check and recreate backup from the existing database."""

import sqlite3

# Try the original DB file
for db_name in ["invoice_dev.db", "invoice_dev_new.db", "invoice_dev_locked.db"]:
    try:
        conn = sqlite3.connect(db_name)
        cur = conn.cursor()
        tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print(f"{db_name}: {len(tables)} tables")
        for t in tables:
            count = cur.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            print(f"  {t[0]}: {count} rows")
        conn.close()
    except Exception as e:
        print(f"{db_name}: ERROR - {e}")
