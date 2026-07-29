"""Verify the invoice was updated in the database after Xero push."""

import sqlite3

conn = sqlite3.connect("invoice_dev.db")
cursor = conn.cursor()

cursor.execute(
    "SELECT id, original_filename, status, xero_invoice_id, organization_id "
    "FROM invoices WHERE original_filename LIKE ?",
    ("%keepa%",),
)
r = cursor.fetchone()
print(f"Invoice: {r[1]!r}")
print(f"Status: {r[2]}")
print(f"Xero Invoice ID (SAIW): {r[3]}")
print(f"Organization ID: {r[4]}")

cursor.execute(
    "SELECT message FROM processing_logs "
    "WHERE invoice_id = ? AND step = 'xero_sync' "
    "ORDER BY created_at DESC LIMIT 1",
    (r[0],),
)
log = cursor.fetchone()
print(f"Latest log: {log[0]}")

cursor.execute("SELECT tenant_name, tenant_id FROM xero_credentials")
c = cursor.fetchone()
print(f"Xero tenant: {c[0]} ({c[1]})")

conn.close()
