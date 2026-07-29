"""Refresh Xero OAuth token and push the verified invoice."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./invoice_dev.db"

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session_factory, engine
from app.models.invoice import Invoice
from app.models.xero_credential import XeroCredential

INVOICE_ID = uuid.UUID("8e2439a4d424490a8b493be01a9b12f8")
ORG_ID = uuid.UUID(int=0)


async def main():
    import httpx

    async with async_session_factory() as db:
        result = await db.execute(
            select(XeroCredential).where(XeroCredential.organization_id == ORG_ID)
        )
        credential = result.scalar_one_or_none()
        if not credential:
            print("No credential")
            return

        print("Refreshing token for tenant:", credential.tenant_name)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
            "client_id": settings.xero_client_id,
        }
        if settings.xero_client_secret:
            data["client_secret"] = settings.xero_client_secret
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://identity.xero.com/connect/token", data=data, timeout=20
            )
            print("Refresh response:", resp.status_code)
            if resp.status_code != 200:
                print(resp.text)
                return
            td = resp.json()
        credential.access_token = td["access_token"]
        credential.refresh_token = td.get("refresh_token", credential.refresh_token)
        credential.token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=td.get("expires_in", 1800)
        )
        await db.flush()
        print("Token refreshed")

        result = await db.execute(
            select(Invoice)
            .options(selectinload(Invoice.extracted_data), selectinload(Invoice.line_items))
            .where(Invoice.id == INVOICE_ID)
        )
        invoice = result.scalar_one()
        from app.services.xero_client import XeroClient

        xero_id = XeroClient(credential).push_invoice(invoice)
        if xero_id:
            invoice.xero_invoice_id = xero_id
            await db.commit()
            print("SUCCESS:", xero_id)
        else:
            print("PUSH_FAILED")

    await engine.dispose()


asyncio.run(main())
