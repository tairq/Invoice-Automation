"""Xero invoice sync — push completed invoices to Xero via API.

Uses the ``xero-python`` SDK for Accounting API calls and manages OAuth2
tokens (PKCE Desktop flow or standard Web app flow) via async DB access.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from xero_python.accounting import Invoice as SdkInvoice  # noqa: N812 — for attribute_map

from app.config import settings
from app.models.invoice import Invoice
from app.models.xero_credential import XeroCredential
from app.services.xero_client import (
    XeroClient,
    build_invoice_model,
    get_valid_credential,
    refresh_access_token,
)

logger = logging.getLogger(__name__)

XERO_AUTH_URL = "https://identity.xero.com/connect/token"
XERO_CONNECT_BASE = "https://login.xero.com/identity/connect/authorize"


# ─── PKCE Helpers ────────────────────────────────────────────────────


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``code_verifier`` and ``code_challenge`` pair.

    The ``code_challenge`` is an S256 hash of the verifier (base64url-encoded
    with no padding), as required by Xero's Desktop OAuth2 flow.

    Returns ``(code_verifier, code_challenge)``.
    """
    code_verifier = secrets.token_urlsafe(64)  # 86 chars — well within 43–128
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return code_verifier, code_challenge


# ─── OAuth2 URL Generation ──────────────────────────────────────────


def build_authorization_url(state: str, code_challenge: str) -> str:
    """Build the Xero OAuth2 authorization URL (Desktop PKCE flow).

    Uses ``http://localhost:18080/xero-callback`` as the redirect URI
    and PKCE S256 challenge for the Desktop app flow (no ``client_secret``).
    """
    from urllib.parse import quote

    params = {
        "response_type": "code",
        "client_id": settings.xero_client_id,
        "redirect_uri": settings.xero_redirect_uri,
        "scope": "offline_access accounting.invoices accounting.contacts",
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return f"{XERO_CONNECT_BASE}?{query}"


# ─── Token Exchange ─────────────────────────────────────────────────


async def exchange_code_for_tokens(
    db: AsyncSession,
    org_id: UUID,
    code: str,
    code_verifier: str,
) -> bool:
    """Exchange OAuth2 authorization code for tokens and store them.

    Uses PKCE ``code_verifier`` (no ``client_secret`` needed for Desktop apps).
    Returns ``True`` if successful.
    """
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.xero_redirect_uri,
        "client_id": settings.xero_client_id,
        "code_verifier": code_verifier,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(XERO_AUTH_URL, data=data, timeout=15.0)
            resp.raise_for_status()
            token_data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Xero token exchange failed: %s — %s",
            exc.response.status_code,
            exc.response.text,
        )
        return False
    except httpx.RequestError as exc:
        logger.error("Xero token exchange connection error: %s", exc)
        return False

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 1800)

    # Get tenant(s) from connections endpoint
    tenants = await resolve_tenants(access_token)
    if not tenants:
        logger.error(
            "Could not resolve Xero tenant ID — user may need to accept organization invite"
        )
        return False

    tenant_id = tenants[0].get("tenantId")
    tenant_name = tenants[0].get("tenantName")

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
        credential.tenant_name = tenant_name
    else:
        credential = XeroCredential(
            organization_id=org_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=now + timedelta(seconds=expires_in),
            tenant_id=tenant_id,
            tenant_name=tenant_name,
        )
        db.add(credential)

    await db.flush()
    logger.info("Xero OAuth completed for org %s (tenant: %s [%s])", org_id, tenant_name, tenant_id)
    return True


async def resolve_tenants(access_token: str) -> list[dict[str, Any]]:
    """Call Xero connections API to list all connected tenants.

    Returns a list of dicts with keys like ``tenantId``, ``tenantName``,
    ``tenantType`` — one entry per Xero organisation the user has authorised.
    """
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
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to resolve Xero tenants: %s", exc)
        return []


async def _resolve_tenant_id(access_token: str) -> Optional[str]:
    """Call Xero connections API to get the first available tenant ID.

    .. deprecated::
       Prefer :func:`resolve_tenants` for new code so callers can also
       capture the ``tenantName`` or let the user pick.
    """
    connections = await resolve_tenants(access_token)
    if connections:
        return connections[0].get("tenantId")
    return None


# ─── Data Mapping (backward-compatible) ─────────────────────────────


def _build_xero_invoice(invoice: Invoice) -> dict[str, Any]:
    """Map an invoice + extracted data to a Xero API dict.

    .. deprecated::
       Use ``build_invoice_model()`` from ``xero_client`` instead for
       SDK-powered integrations.  This function is kept for backward-
       compatible tests.
    """
    sdk_invoice = build_invoice_model(invoice)
    # Convert SDK model to dict with Xero API PascalCase field names
    raw = sdk_invoice.to_dict()
    result: dict[str, Any] = {}
    for py_name, xero_name in SdkInvoice.attribute_map.items():
        val = raw.get(py_name)
        if val is not None:
            # Convert nested models too
            if isinstance(val, dict) and xero_name == "Contact":
                result[xero_name] = {"Name": val.get("name", "Unknown Vendor")}
            elif isinstance(val, list) and xero_name == "LineItems":
                result[xero_name] = [
                    {
                        "Description": li.get("description", ""),
                        "Quantity": li.get("quantity", 1.0),
                        "UnitAmount": li.get("unit_amount", 0.0),
                        "TaxAmount": li.get("tax_amount"),
                        "AccountCode": li.get("account_code"),
                    }
                    for li in val
                ]
            else:
                result[xero_name] = val
    return result


# ─── Push Invoice ────────────────────────────────────────────────────


async def push_invoice_to_xero(
    db: AsyncSession,
    invoice: Invoice,
) -> Optional[str]:
    """Push a completed invoice to Xero.

    Loads the Xero credential, refreshes the token if needed, and
    uses the ``xero-python`` SDK to create the invoice via the
    Accounting API.

    Returns the Xero invoice ID as a string if successful, ``None``
    if skipped or failed.
    """
    if not settings.xero_enabled:
        return None

    if not settings.xero_client_id:
        logger.warning("Xero enabled but client_id not configured")
        return None

    org_id = invoice.organization_id
    if not org_id:
        logger.info("Invoice %s has no organization — skipping Xero sync", invoice.id)
        return None

    # Load credential from DB
    credential = await get_valid_credential(db, org_id)
    if not credential:
        logger.info(
            "No Xero credential for org %s — skipping Xero sync",
            org_id,
        )
        return None

    # Refresh token if needed (async)
    await refresh_access_token(db, credential)

    # Use the SDK (synchronous, which is fine inside a Celery worker context)
    client = XeroClient(credential)
    return client.push_invoice(invoice)
