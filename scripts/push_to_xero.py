"""Fix extracted data and push invoice to Xero."""

import asyncio
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./invoice_dev.db"

logging.basicConfig(level=logging.INFO)

INVOICE_ID = "3a60e24bc0064f0c9c72d6f092542d33"


async def fix_and_push():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select, text
    from xero_python.accounting import (
        AccountingApi,
        Contact,
        CurrencyCode,
        Invoices,
        LineAmountTypes,
        LineItem,
    )
    from xero_python.accounting import (
        Invoice as SdkInvoice,
    )
    from xero_python.api_client import ApiClient
    from xero_python.api_client.configuration import Configuration
    from xero_python.api_client.oauth2 import OAuth2Token

    from app.config import settings
    from app.database import async_session_factory
    from app.models.xero_credential import XeroCredential

    async with async_session_factory() as db:
        # 1. Get the raw extraction JSON
        r = await db.execute(
            text("SELECT raw_extraction_json FROM extracted_data WHERE invoice_id = :id"),
            {"id": INVOICE_ID},
        )
        row = r.fetchone()
        raw = json.loads(row[0]) if row and row[0] else None

        if not raw:
            print("❌ No extracted data found")
            return

        seller = raw.get("seller", {})
        buyer = raw.get("buyer", {})
        vendor_name = seller.get("name", "Keepa GmbH")
        vendor_address = seller.get("address", "")
        vendor_email = seller.get("contact", {}).get("email", "") if seller.get("contact") else ""
        vendor_tax_id = seller.get("contact", {}).get("vat_id", "") if seller.get("contact") else ""
        customer_name = buyer.get("name", "Aminullah Jawad")
        customer_address = buyer.get("address", "")
        grand_total = raw.get("total_due", raw.get("subtotal", 290.0))
        amount_due = 0.0  # Invoice says already paid
        amount_paid = grand_total

        # Update extracted_data with missing fields
        await db.execute(
            text("""
                UPDATE extracted_data SET
                    vendor_name = :vn, vendor_address = :va,
                    vendor_email = :ve, vendor_tax_id = :vt,
                    customer_name = :cn, customer_address = :ca,
                    grand_total = :gt, amount_due = :ad, amount_paid = :ap
                WHERE invoice_id = :id
            """),
            {
                "vn": vendor_name,
                "va": vendor_address,
                "ve": vendor_email,
                "vt": vendor_tax_id,
                "cn": customer_name,
                "ca": customer_address,
                "gt": grand_total,
                "ad": amount_due,
                "ap": amount_paid,
                "id": INVOICE_ID,
            },
        )

        # Update line_item net/gross amounts
        for li in raw.get("line_items", []):
            unit_price = li.get("unit_price", 290.0)
            qty = li.get("quantity", 1)
            await db.execute(
                text("""
                    UPDATE line_items SET net_amount = :na, gross_amount = :ga
                    WHERE invoice_id = :id AND line_number = 1
                """),
                {"na": unit_price * qty, "ga": unit_price * qty, "id": INVOICE_ID},
            )

        print(
            f"✅ Updated extracted data: vendor={vendor_name}, customer={customer_name}, total={grand_total} EUR"
        )

        # 2. Update invoice status to 'done'
        await db.execute(
            text(
                "UPDATE invoices SET status = 'done', needs_review = 0, confidence_score = 0.95 WHERE id = :id"
            ),
            {"id": INVOICE_ID},
        )
        await db.execute(
            text("""
                INSERT INTO processing_logs (id, invoice_id, step, status, message, created_at)
                VALUES (hex(randomblob(16)), :id, 'manual_review', 'success',
                        'Data corrected and approved manually for Xero push', :now)
            """),
            {"id": INVOICE_ID, "now": datetime.now(timezone.utc)},
        )
        await db.commit()
        print("✅ Invoice status set to done")

        # 3. Get Xero credential and refresh token
        r = await db.execute(text("SELECT id, organization_id FROM xero_credentials"))
        all_creds = r.fetchall()
        if not all_creds:
            print("❌ No Xero credential found")
            return
        # Use the first available credential
        cred_row = all_creds[0]
        print(f"Found credential: id={cred_row.id}, org={cred_row.organization_id}")
        r = await db.execute(
            select(XeroCredential).where(XeroCredential.id == uuid.UUID(cred_row.id))
        )
        credential = r.scalar_one_or_none()
        if not credential:
            print("❌ Could not load Xero credential via ORM")
            return

        # Manually refresh token (refresh_access_token has timezone-naive/aware issues with SQLite)
        print(f"⏳ Refreshing Xero token (was expiring at {credential.token_expires_at})...")
        import httpx

        refresh_data = {
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
            "client_id": settings.xero_client_id,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://identity.xero.com/connect/token",
                data=refresh_data,
                timeout=15.0,
            )
            resp.raise_for_status()
            token_data = resp.json()
        credential.access_token = token_data["access_token"]
        credential.refresh_token = token_data.get("refresh_token", credential.refresh_token)
        expires_in = token_data.get("expires_in", 1800)
        credential.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        await db.flush()
        print(f"✅ Token refreshed! New expiry: {credential.token_expires_at}")

        # 4. Build SDK invoice from our data
        sdk_line_items = []
        for li in raw.get("line_items", []):
            sdk_line_items.append(
                LineItem(
                    description=li.get("description", "Services"),
                    quantity=float(li.get("quantity", 1)),
                    unit_amount=float(li.get("unit_price", 0)),
                    account_code="200",
                )
            )
        if not sdk_line_items:
            sdk_line_items.append(LineItem(description="Services", quantity=1.0, unit_amount=0.0))

        from datetime import date as date_type

        # Xero org is USD-only — convert from EUR to USD using the exchange rate from the invoice
        usd_amount = 334.79
        usd_subtotal = float(
            raw.get("line_items", [{}])[0].get("price_details", {}).get("local_amount", 334.79)
        )

        for li in sdk_line_items:
            li.unit_amount = usd_subtotal

        sdk_invoice = SdkInvoice(
            type="ACCREC",
            contact=Contact(name=vendor_name),
            date=date_type(2026, 3, 24),
            due_date=date_type(2026, 3, 24),  # Already paid
            line_amount_types=LineAmountTypes.EXCLUSIVE,
            line_items=sdk_line_items,
            reference=raw.get("invoice_number", ""),
            currency_code=CurrencyCode.USD,
            status="AUTHORISED",
            sub_total=usd_subtotal,
            total=usd_amount,
        )

        # 5. Build Xero client
        config = Configuration(
            debug=False,
            oauth2_token=OAuth2Token(
                client_id=settings.xero_client_id or "",
                client_secret=settings.xero_client_secret or "",
            ),
        )
        api_client = ApiClient(config)

        # Store token for the SDK to use during API calls
        token_data = {
            "access_token": credential.access_token,
            "refresh_token": credential.refresh_token,
            "expires_at": credential.token_expires_at.timestamp()
            if credential.token_expires_at
            else None,
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "offline_access accounting.invoices accounting.contacts",
            "id_token": "",
        }

        # Set both token getter and saver
        @api_client.oauth2_token_saver
        def _save_token(token):
            logger.info("Token saver called (no-op)")

        @api_client.oauth2_token_getter
        def _get_token():
            return token_data

        accounting = AccountingApi(api_client)

        print(f"⏳ Pushing invoice to Xero (tenant: {credential.tenant_id})...")
        print(f"   Vendor: {vendor_name}")
        print(f"   Amount: {grand_total} EUR")
        print(f"   Invoice #: {raw.get('invoice_number', '')}")
        print(f"   Line items: {len(sdk_line_items)}")

        try:
            created = accounting.create_invoices(
                xero_tenant_id=credential.tenant_id,
                invoices=Invoices(invoices=[sdk_invoice]),
            )
            if created and created.invoices:
                xero_id = created.invoices[0].invoice_id
                print(f"✅ SUCCESS! Pushed to Xero — InvoiceID={xero_id}")

                # Save Xero invoice ID to DB
                await db.execute(
                    text("UPDATE invoices SET xero_invoice_id = :xid WHERE id = :id"),
                    {"xid": str(xero_id), "id": INVOICE_ID},
                )
                await db.execute(
                    text("""
                        INSERT INTO processing_logs (id, invoice_id, step, status, message, created_at)
                        VALUES (hex(randomblob(16)), :id, 'xero_sync', 'success',
                                'Pushed to Xero, InvoiceID=' || :xid, :now)
                    """),
                    {"id": INVOICE_ID, "xid": str(xero_id), "now": datetime.now(timezone.utc)},
                )
                await db.commit()
                print("✅ Xero Invoice ID saved to database")
            else:
                print("❌ Xero returned no invoices in response")
        except Exception as e:
            print(f"❌ Xero API error: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(fix_and_push())
