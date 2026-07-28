"""Push the Keepa invoice to Xero SAIW tenant."""
import asyncio
import logging
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from xero_python.accounting import (
    AccountingApi,
    Contact,
    Invoice as SdkInvoice,
    Invoices,
    LineAmountTypes,
    LineItem,
)
from xero_python.api_client import ApiClient
from xero_python.api_client.configuration import Configuration
from xero_python.api_client.oauth2 import OAuth2Token

from app.config import settings
from app.services.xero_client import refresh_access_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xero_push")


def parse_date(val):
    """Parse a date value to datetime.date object."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        # Try common formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        # Try fromisoformat as fallback
        try:
            dt_val = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt_val.date()
        except (ValueError, AttributeError):
            pass
    return None


async def main():
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # 1. Find the Keepa invoice
        row = (
            await session.execute(
                text(
                    "SELECT id, organization_id, xero_invoice_id, original_filename, status "
                    "FROM invoices WHERE original_filename LIKE '%keepa%' LIMIT 1"
                )
            )
        ).one_or_none()

        if not row:
            print("ERROR: Could not find Keepa invoice")
            return

        inv_id, org_id, old_xero_id, filename, status = row
        print(f"Invoice: id={inv_id}, file={filename!r}, status={status}")
        print(f"  Current xero_invoice_id: {old_xero_id}")

        # 2. Get extracted data
        ed_result = await session.execute(
            text("SELECT * FROM extracted_data WHERE invoice_id = :inv_id"),
            {"inv_id": inv_id},
        )
        ed_row = ed_result.one_or_none()
        if not ed_row:
            print("ERROR: No extracted data for invoice")
            return

        ed_cols = list(ed_result.keys())
        ed = {col: ed_row[i] for i, col in enumerate(ed_cols)}
        print(
            f"Extracted: vendor={ed['vendor_name']}, "
            f"total={ed['grand_total']}, currency={ed['currency']}"
        )

        # 3. Get line items
        li_result = await session.execute(
            text("SELECT * FROM line_items WHERE invoice_id = :inv_id"),
            {"inv_id": inv_id},
        )
        li_rows = li_result.all()
        li_cols = list(li_result.keys())
        print(f"Line items: {len(li_rows)}")

        # 4. Get Xero credential
        default_org_id = UUID(int=0)
        db_org_id = default_org_id.hex
        cred_result = await session.execute(
            text(
                "SELECT * FROM xero_credentials WHERE organization_id = :org_id"
            ),
            {"org_id": db_org_id},
        )
        cred_row = cred_result.one_or_none()
        if not cred_row:
            print("ERROR: No Xero credential found")
            return

        cred_cols = list(cred_result.keys())
        print(
            f"Xero credential: tenant={cred_row[cred_cols.index('tenant_name')]} "
            f"({cred_row[cred_cols.index('tenant_id')]})"
        )

        # 5. Build a minimal credential-like object for refresh_access_token
        class FakeCred:
            pass

        cred = FakeCred()
        cred.access_token = cred_row[cred_cols.index("access_token")]
        cred.refresh_token = cred_row[cred_cols.index("refresh_token")]
        raw_expires = cred_row[cred_cols.index("token_expires_at")]
        if isinstance(raw_expires, str):
            parsed = datetime.fromisoformat(raw_expires)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            cred.token_expires_at = parsed
        else:
            cred.token_expires_at = raw_expires
        cred.tenant_id = cred_row[cred_cols.index("tenant_id")]
        cred.tenant_name = cred_row[cred_cols.index("tenant_name")]
        cred.organization_id = default_org_id

        # 6. Refresh token if needed
        await refresh_access_token(session, cred)
        print("Token refreshed/valid")

        # 7. Build SDK invoice model (SDK v15 requires proper enum/date types)
        line_items = []
        for li in li_rows:
            li_item = LineItem(
                description=li[li_cols.index("description")] or "",
                quantity=float(li[li_cols.index("quantity")] or 1.0),
                unit_amount=float(
                    li[li_cols.index("unit_price")] or 0.0
                ),
                tax_amount=(
                    float(li[li_cols.index("tax_amount")])
                    if li[li_cols.index("tax_amount")]
                    else None
                ),
                account_code="200",
            )
            line_items.append(li_item)

        if not line_items:
            line_items.append(
                LineItem(description="Services", quantity=1.0, unit_amount=0.0)
            )

        # Parse dates to datetime.date objects (SDK v15 requirement)
        sdk_issue_date = parse_date(ed.get("issue_date"))
        sdk_due_date = parse_date(ed.get("due_date"))

        grand_total = float(ed["grand_total"]) if ed.get("grand_total") is not None else None
        subtotal = float(ed["subtotal"]) if ed.get("subtotal") is not None else None

        # Xero requires a DueDate for AUTHORISED invoices
        if sdk_due_date is None and sdk_issue_date:
            from datetime import timedelta
            sdk_due_date = sdk_issue_date + timedelta(days=30)
            print(f"  Set DueDate: {sdk_due_date} (30 days from issue)")

        sdk_invoice = SdkInvoice(
            type="ACCREC",
            contact=Contact(name=ed.get("vendor_name", "Unknown Vendor")),
            date=sdk_issue_date,
            due_date=sdk_due_date,
            line_amount_types=LineAmountTypes.EXCLUSIVE,
            line_items=line_items,
            reference=ed.get("invoice_number", "") or "",
            # Don't set currency_code — let Xero use the org's default
            status="AUTHORISED",
            sub_total=subtotal,
            total_tax=(
                float(ed["tax_total"]) if ed.get("tax_total") else None
            ),
            total=grand_total,
        )

        # 8. Create API client and push
        config = Configuration(
            debug=settings.debug,
            oauth2_token=OAuth2Token(
                client_id=settings.xero_client_id or "",
                client_secret=settings.xero_client_secret or "",
            ),
        )
        api_client = ApiClient(config)

        # SDK v15 requires both getter AND saver
        @api_client.oauth2_token_saver
        def _save_token(token):
            pass

        # Set token data
        expires_at = (
            cred.token_expires_at.timestamp()
            if cred.token_expires_at
            else None
        )
        token_data = {
            "access_token": cred.access_token,
            "refresh_token": cred.refresh_token,
            "expires_at": expires_at,
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "offline_access accounting.invoices accounting.contacts",
        }
        api_client.set_oauth2_token(token_data)

        # SDK v15 requires a getter that returns the current token
        @api_client.oauth2_token_getter
        def _get_token():
            return token_data

        accounting = AccountingApi(api_client)

        print(
            f"Pushing to Xero tenant: {cred.tenant_name} "
            f"({cred.tenant_id})..."
        )

        created = accounting.create_invoices(
            xero_tenant_id=cred.tenant_id,
            invoices=Invoices(invoices=[sdk_invoice]),
        )

        if created and created.invoices:
            xero_id = created.invoices[0].invoice_id
            print(f"\nSUCCESS! Pushed to SAIW, Xero Invoice ID = {xero_id}")

            # Update invoice in DB
            await session.execute(
                text(
                    "UPDATE invoices SET xero_invoice_id = :xero_id "
                    "WHERE id = :inv_id"
                ),
                {"xero_id": xero_id, "inv_id": inv_id},
            )

            # Add processing log
            await session.execute(
                text(
                    "INSERT INTO processing_logs "
                    "(id, invoice_id, step, status, message, created_at) "
                    "VALUES (:id, :inv_id, 'xero_sync', 'success', :msg, :ts)"
                ),
                {
                    "id": uuid4().hex,
                    "inv_id": inv_id,
                    "msg": f"Pushed to Xero (SAIW), InvoiceID={xero_id}",
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )

            # Set organization_id if null so future syncs work too
            if org_id is None:
                await session.execute(
                    text(
                        "UPDATE invoices SET organization_id = :org_id "
                        "WHERE id = :inv_id"
                    ),
                    {"org_id": db_org_id, "inv_id": inv_id},
                )
                print("Updated invoice organization_id to match Xero credential")

            await session.commit()
            print(
                f"View in Xero: "
                f"https://go.xero.com/AccountsReceivable/"
                f"ViewInvoice.aspx?InvoiceID={xero_id}"
            )
        else:
            print(f"ERROR: Xero returned no invoices: {created}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
