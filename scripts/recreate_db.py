"""Recreate the SQLite database with current model schemas and restore data."""

import asyncio
import json
import os
import uuid
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./invoice_dev.db"

from sqlalchemy import text as sa_text

from app.database import async_session_factory, engine, init_db
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.models.organization import Organization
from app.models.xero_credential import XeroCredential


async def recreate_db():
    # 1. Delete old DB file
    db_path = "invoice_dev.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print("Deleted old database")

    # 2. Connect to new empty DB and create all tables
    await init_db()
    print("Created all tables from current models")

    # 3. Restore backup data
    with open("scripts/db_backup.json") as f:
        backup = json.load(f)

    async with async_session_factory() as session:
        # Restore organization
        for org_data in backup["organizations"]:
            obj = Organization(
                id=uuid.UUID(org_data["id"]),
                name=org_data["name"],
            )
            session.add(obj)
            print(f"Restored organization: {org_data['name']}")

        # Restore Xero credentials
        for cred_data in backup["xero_credentials"]:
            token_expires = datetime.fromisoformat(cred_data["token_expires_at"])
            obj = XeroCredential(
                organization_id=uuid.UUID(cred_data["organization_id"]),
                access_token=cred_data["access_token"],
                refresh_token=cred_data["refresh_token"],
                token_expires_at=token_expires,
                tenant_id=cred_data["tenant_id"],
                tenant_name=cred_data.get("tenant_name"),
            )
            session.add(obj)
            print(f"Restored Xero credential: {cred_data.get('tenant_name', 'unknown')}")

        # Restore processed invoice (from Keepa that was already done)
        for inv_data in backup["invoices"]:
            obj = Invoice(
                id=uuid.UUID(inv_data["id"]),
                organization_id=uuid.UUID(inv_data["organization_id"])
                if inv_data.get("organization_id")
                else None,
                status=InvoiceStatus.done,
                source=InvoiceSource.email,
                file_path=inv_data["file_path"],
                original_filename=inv_data["original_filename"],
                file_type=inv_data["file_type"],
                file_size=int(inv_data["file_size"]),
                confidence_score=float(inv_data["confidence_score"])
                if inv_data.get("confidence_score")
                else None,
                xero_invoice_id=inv_data["xero_invoice_id"],
            )
            session.add(obj)
            print(f"Restored invoice: {inv_data['original_filename']}")

        await session.commit()
        print("Data restored successfully")

    # Verify
    async with async_session_factory() as session:
        orgs = (await session.execute(sa_text("SELECT id, name FROM organizations"))).all()
        creds = (
            await session.execute(
                sa_text("SELECT id, tenant_name, tenant_id FROM xero_credentials")
            )
        ).all()
        invs = (
            await session.execute(
                sa_text("SELECT id, original_filename, xero_invoice_id FROM invoices")
            )
        ).all()

        print("\nVerification:")
        print(f"  Organizations: {len(orgs)}")
        print(f"  Xero Credentials: {len(creds)}")
        print(f"  Invoices: {len(invs)}")

    await engine.dispose()
    print("Done!")


asyncio.run(recreate_db())
