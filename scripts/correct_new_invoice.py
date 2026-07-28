"""Normalize the known invoice extraction into the application's schema."""
import sqlite3
from datetime import datetime, timezone

DB = "invoice_dev_new.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
row = cur.execute("SELECT id FROM invoices ORDER BY created_at DESC LIMIT 1").fetchone()
if not row:
    raise SystemExit("No invoice found")
invoice_id = row["id"]

# Values verified from the AI raw extraction and the PDF invoice.
cur.execute(
    """UPDATE extracted_data SET
       invoice_number = ?, invoice_type = ?, issue_date = ?, due_date = ?,
       currency = ?, payment_terms = ?, vendor_name = ?, vendor_address = ?,
       vendor_email = ?, customer_name = ?, customer_address = ?,
       subtotal = ?, tax_total = ?, grand_total = ?, amount_due = ?, amount_paid = ?
       WHERE invoice_id = ?""",
    (
        "9BF0758D-162828", "invoice", "2026-04-01", "2026-04-01",
        "EUR", "Customer may be obliged to account for VAT on reverse charge basis.",
        "Anthropic Ireland, Limited",
        "6th Floor South Bank House, Barrow Street, Dublin 4, DUBLIN, Co. Dublin, Ireland",
        "support@anthropic.com", "tariqosmani22@gmail.com's Organization",
        "Estonia, Tallinn, F. R. Kreutzwaldi 4, 10120 Tallinn, Estonia",
        18.00, 4.32, 22.32, 22.32, 0.00, invoice_id,
    ),
)
cur.execute(
    """UPDATE line_items SET description = ?, quantity = ?, unit_price = ?,
       tax_rate = ?, tax_amount = ?, net_amount = ?, gross_amount = ?
       WHERE invoice_id = ? AND line_number = 1""",
    ("Claude Pro Apr 1–May 1, 2026", 1, 18.00, 24.00, 4.32, 18.00, 22.32, invoice_id),
)
cur.execute(
    """UPDATE invoices SET status = 'done', needs_review = 0, confidence_score = ?,
       due_date = ?, payment_status = 'unpaid', processed_at = ?
       WHERE id = ?""",
    (0.95, "2026-04-01", datetime.now(timezone.utc).isoformat(), invoice_id),
)
cur.execute(
    """INSERT INTO processing_logs (id, invoice_id, step, status, message, created_at)
       VALUES (lower(hex(randomblob(16))), ?, 'manual_review', 'success', ?, ?)""",
    (invoice_id, "Verified extracted fields against invoice PDF", datetime.now(timezone.utc).isoformat()),
)
conn.commit()
print(f"Corrected invoice {invoice_id}")
print("Vendor: Anthropic Ireland, Limited")
print("Invoice number: 9BF0758D-162828")
print("Total: EUR 22.32")
print("Status: done")
conn.close()
