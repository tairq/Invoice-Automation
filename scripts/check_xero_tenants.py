"""Check available Xero tenant connections."""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./invoice_dev.db"


async def main():
    from sqlalchemy import select, text

    from app.database import async_session_factory
    from app.models.xero_credential import XeroCredential
    from app.services.xero_client import XeroClient, refresh_access_token

    async with async_session_factory() as db:
        r = await db.execute(text("SELECT * FROM xero_credentials"))
        cols = [c[0] for c in r.cursor.description]
        row = r.fetchone()
        if not row:
            print("No Xero credential found in DB. Run the OAuth flow first.")
            return

        cred_dict = {cols[i]: row[i] for i in range(len(cols))}
        r = await db.execute(
            select(XeroCredential).where(XeroCredential.id == uuid.UUID(cred_dict["id"]))
        )
        credential = r.scalar_one_or_none()

        # Refresh the token
        print("Refreshing Xero token...")
        await refresh_access_token(db, credential)

        # Get all Xero connections/tenants
        print("\n=== Available Xero Tenants ===")
        client = XeroClient(credential)
        connections = client.get_connections()

        if not connections:
            print("No Xero tenant connections found!")
            print("Go to https://developer.xero.com/app/manage and check your app has been connected to an org.")
            return

        for conn in connections:
            print(f"\n  Tenant ID: {conn.get('tenantId')}")
            print(f"  Name:       {conn.get('tenantName')}")
            print(f"  Type:       {conn.get('tenantType')}")
            print(f"  Active:     {conn.get('isActive')}")

        print(f"\n---")
        print(f"Currently configured tenant: {credential.tenant_id}")
        print(f"Demo orgs are typically named 'Demo Company'")

        if len(connections) > 1:
            print("\n⚠️  You have multiple Xero orgs available.")
            print("   The system is currently using the FIRST one from OAuth setup.")
            print("   To use a different org, you'd need to reconnect with that org.")


if __name__ == "__main__":
    asyncio.run(main())
