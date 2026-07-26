"""Xero invoice sync — push completed invoices to Xero via API."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import Invoice
from app.models.xero_credential import XeroCredential

logger = logging.getLogger(__name__)

XERO_AUTH_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_CONNECT_BASE = "https://login.xero.com/identity/connect/authorize"


# ─── OAuth2 Helpers ────────────────────────────────────────────────


def _xero_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _refresh_access_token(db: AsyncSession, credential: XeroCredential) -> str:
    """Refresh the Xero access token if expired or about to expire."""
    now = datetime.now(timezone.utc)
    # Refresh if expires within 5 minutes
    if credential.token_expires_at and credential.token_expires_at > now + timedelta(minutes=5):
        return credential.access_token  # Still valid

    logger.info("Refreshing Xero token for org %s", credential.organization_id)

    data = {
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "client_id": settings.xero_client_id,
        "client_secret": settings.xero_client_secret,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(XERO_AUTH_URL, data=data, timeout=15.0)
        resp.raise_for_status()
        token_data = resp.json()

    new_access = token_data["access_token"]
    new_refresh = token_data.get("refresh_token", credential.refresh_token)
    expires_in = token_data.get("expires_in", 1800)

    credential.access_token = new_access
    credential.refresh_token = new_refresh
    credential.token_expires_at = now + timedelta(seconds=expires_in)

    await db.flush()
    return new_access


async def _get_valid_credential(db: AsyncSession, org_id: UUID) -> Optional[XeroCredential]:
    """Fetch a valid Xero credential for an organization.

    Returns None if no credential is configured.
    """
    result = await db.execute(
        select(XeroCredential).where(XeroCredential.organization_id == org_id)
    )
    credential = result.scalar_one_or_none()
    if not credential:
        logger.warning("No Xero credential for org %s", org_id)
        return None
    return credential


# ─── Data Mapping ───────────────────────────────────────────────────


def _build_xero_invoice(invoice: Invoice) -> dict[str, Any]:
    """Map an invoice + extracted data to Xero's Invoices POST payload."""
    ed = invoice.extracted_data
    if not ed:
        raise ValueError(f"Invoice {invoice.id} has no extracted data")

    line_items = invoice.line_items or []

    # Map line items
    xero_line_items = []
    for item in line_items:
        xero_line_items.append({
            "Description": item.description or "",
            "Quantity": float(item.quantity) if item.quantity else 1.0,
            "UnitAmount": float(item.unit_price) if item.unit_price else 0.0,
            "TaxAmount": float(item.tax_amount) if item.tax_amount else None,
            "AccountCode": "200",  # Sales default — could be configurable
        })

    payload: dict[str, Any] = {
        "Type": "ACCREC",  # Accounts Receivable
        "Contact": {
            "Name": ed.vendor_name or "Unknown Vendor",
        },
        "Date": ed.issue_date.isoformat() if ed.issue_date else None,
        "DueDate": ed.due_date.isoformat() if ed.due_date else None,
        "LineAmountTypes": "Exclusive",
        "LineItems": xero_line_items or [{"Description": "Services", "Quantity": 1, "UnitAmount": 0}],
        "Reference": ed.invoice_number or "",
        "CurrencyCode": ed.currency or "USD",
        "Status": "AUTHORISED",
    }

    if ed.grand_total is not None:
        payload["Total"] = float(ed.grand_total)

    if ed.subtotal is not None:
        payload["SubTotal"] = float(ed.subtotal)

    if ed.tax_total is not None:
        payload["TotalTax"] = float(ed.tax_total)

    return payload


# ─── Push Invoice ──────────────────────────────────────────────────


async def push_invoice_to_xero(
    db: AsyncSession,
    invoice: Invoice,
) -> Optional[str]:
    """Push a completed invoice to Xero.

    Returns the Xero invoice ID if successful, None if skipped or failed.
    """
    if not settings.xero_enabled:
        return None

    if not settings.xero_client_id or not settings.xero_client_secret:
        logger.warning("Xero enabled but client_id/secret not configured")
        return None

    org_id = invoice.organization_id
    if not org_id:
        logger.info("Invoice %s has no organization — skipping Xero sync", invoice.id)
        return None

    credential = await _get_valid_credential(db, org_id)
    if not credential:
        return None

    # Refresh token if needed
    token = await _refresh_access_token(db, credential)

    # Build payload
    try:
        payload = _build_xero_invoice(invoice)
    except ValueError as exc:
        logger.error("Cannot push to Xero: %s", exc)
        return None

    tenant_id = credential.tenant_id
    headers = _xero_headers(token)
    headers["Xero-Tenant-Id"] = tenant_id

    url = f"{XERO_API_BASE}/Invoices"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)

        if resp.status_code == 401:
            # Token may have just expired — try refresh once more
            token = await _refresh_access_token(db, credential)
            headers = _xero_headers(token)
            headers["Xero-Tenant-Id"] = tenant_id
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=30.0)

        resp.raise_for_status()
        result = resp.json()

        # Extract Xero invoice ID from response
        xero_invoices = result.get("Invoices", [])
        if xero_invoices:
            xero_id = xero_invoices[0].get("InvoiceID")
            logger.info(
                "Pushed invoice %s to Xero, got InvoiceID=%s",
                invoice.id, xero_id,
            )
            return xero_id

        logger.warning("Xero response had no InvoiceID: %s", result)
        return None

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Xero API error for invoice %s: %s — %s",
            invoice.id, exc.response.status_code, exc.response.text,
        )
        return None
    except httpx.RequestError as exc:
        logger.error("Xero connection error for invoice %s: %s", invoice.id, exc)
        return None


# ─── OAuth2 URL Generation ────────────────────────────────────────


def build_authorization_url(state: str) -> str:
    """Build the Xero OAuth2 authorization URL."""
    from urllib.parse import quote

    params = {
        "response_type": "code",
        "client_id": settings.xero_client_id,
        "redirect_uri": settings.xero_redirect_uri,
        "scope": "openid profile email accounting.transactions accounting.settings",
        "state": state,
    }
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"{XERO_CONNECT_BASE}?{query}"


async def exchange_code_for_tokens(
    db: AsyncSession,
    org_id: UUID,
    code: str,
) -> bool:
    """Exchange OAuth2 authorization code for tokens and store them.

    Returns True if successful.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.xero_redirect_uri,
        "client_id": settings.xero_client_id,
        "client_secret": settings.xero_client_secret,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(XERO_AUTH_URL, data=data, timeout=15.0)
            resp.raise_for_status()
            token_data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Xero token exchange failed: %s — %s", exc.response.status_code, exc.response.text)
        return False
    except httpx.RequestError as exc:
        logger.error("Xero token exchange connection error: %s", exc)
        return False

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 1800)

    # Get tenant ID from connections endpoint
    tenant_id = await _resolve_tenant_id(access_token)
    if not tenant_id:
        logger.error("Could not resolve Xero tenant ID — user may need to accept organization invite")
        return False

    now = datetime.now(timezone.utc)

    # Upsert credential
    result = await db.execute(
        select(XeroCredential).where(XeroCredential.organization_id == org_id)
    )
    credential = result.scalar_one_or_none()

    if credential:
        credential.access_token = access_token
        credential.refresh_token = refresh_token
        credential.token_expires_at = now + timedelta(seconds=expires_in)
        credential.tenant_id = tenant_id
    else:
        credential = XeroCredential(
            organization_id=org_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=now + timedelta(seconds=expires_in),
            tenant_id=tenant_id,
        )
        db.add(credential)

    await db.flush()
    logger.info("Xero OAuth completed for org %s (tenant: %s)", org_id, tenant_id)
    return True


async def _resolve_tenant_id(access_token: str) -> Optional[str]:
    """Call Xero connections API to get the first available tenant ID."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.xero.com/connections",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            connections = resp.json()
            if connections:
                return connections[0].get("tenantId")
    except Exception as exc:
        logger.warning("Failed to resolve Xero tenant: %s", exc)

    return None
