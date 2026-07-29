"""Push the corrected new invoice to Xero after restoring OAuth credentials."""

import asyncio
import os
import uuid

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./invoice_dev.db"

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_factory, engine
from app.models.invoice import Invoice
from app.models.xero_credential import XeroCredential

INVOICE_ID = "8e2439a4d424490a8b493be01a9b12f8"
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def main():
    async with async_session_factory() as db:
        result = await db.execute(
            select(Invoice)
            .options(
                selectinload(Invoice.extracted_data),
                selectinload(Invoice.line_items),
            )
            .where(Invoice.id == uuid.UUID(INVOICE_ID))
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            print("Invoice not found")
            return

        cred_result = await db.execute(
            select(XeroCredential).where(XeroCredential.organization_id == ORG_ID)
        )
        credential = cred_result.scalar_one_or_none()
        if not credential:
            print("NEED_AUTH")
            return

        print(f"Using tenant: {credential.tenant_name} ({credential.tenant_id})")
        from app.services.xero_sync import push_invoice_to_xero

        xero_id = await push_invoice_to_xero(db, invoice)
        if xero_id:
            invoice.xero_invoice_id = xero_id
            await db.commit()
            print(f"SUCCESS:{xero_id}")
        else:
            print("FAILED")

    await engine.dispose()


asyncio.run(main())
