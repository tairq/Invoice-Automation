"""Xero SDK integration — wraps ``xero-python`` with async DB-backed token management.

Provides ``XeroClient`` which manages the SDK ``ApiClient`` lifecycle,
handles token refresh, and exposes convenience methods for pushing
invoices via the Xero Accounting API.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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
from xero_python.exceptions import AccountingBadRequestException
from xero_python.identity import IdentityApi

from app.config import settings
from app.models.invoice import Invoice
from app.models.xero_credential import XeroCredential

logger = logging.getLogger(__name__)

# Default sales account code used when pushing invoices to Xero.
DEFAULT_SALES_ACCOUNT_CODE = "200"


# ─── Token Management (async, DB-backed) ──────────────────────────────


async def get_valid_credential(
    db: AsyncSession,
    org_id: UUID,
) -> Optional[XeroCredential]:
    """Fetch a Xero credential for an organization.

    Returns ``None`` if no credential exists.
    """
    result = await db.execute(
        select(XeroCredential).where(XeroCredential.organization_id == org_id)
    )
    return result.scalar_one_or_none()


async def refresh_access_token(
    db: AsyncSession,
    credential: XeroCredential,
) -> str:
    """Refresh the Xero access token if expired or about to expire.

    Uses the standard Authorization Code flow with ``client_secret``
    (or PKCE if no secret is configured).  Mutates ``credential`` in
    place and flushes to DB.

    Returns the (possibly refreshed) access token.
    """
    from datetime import datetime, timedelta, timezone

    import httpx

    now = datetime.now(timezone.utc)
    # Refresh if expires within 5 minutes
    expires_at = credential.token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at > now + timedelta(minutes=5):
        return credential.access_token  # Still valid

    logger.info("Refreshing Xero token for org %s", credential.organization_id)

    data: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "client_id": settings.xero_client_id,
    }
    # Include secret for Web app flow; omit for Desktop PKCE
    if settings.xero_client_secret:
        data["client_secret"] = settings.xero_client_secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://identity.xero.com/connect/token",
            data=data,
            timeout=15.0,
        )
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


# ─── Data Mapping ─────────────────────────────────────────────────────


def build_invoice_model(
    invoice: Invoice,
    sales_account_code: str = DEFAULT_SALES_ACCOUNT_CODE,
) -> SdkInvoice:
    """Map our DB ``Invoice`` + extracted data to an SDK ``Invoice`` model.

    This is a **pure function** (no I/O) — usable from tests and sync code
    without an ``XeroClient`` instance.

    Raises ``ValueError`` if the invoice has no extracted data.
    """
    ed = invoice.extracted_data
    if not ed:
        raise ValueError(f"Invoice {invoice.id} has no extracted data")

    line_items = invoice.line_items or []

    sdk_line_items: list[LineItem] = []
    for item in line_items:
        sdk_line_items.append(
            LineItem(
                description=item.description or "",
                quantity=float(item.quantity) if item.quantity else 1.0,
                unit_amount=float(item.unit_price) if item.unit_price else 0.0,
                tax_amount=float(item.tax_amount) if item.tax_amount else None,
                account_code=sales_account_code,
            )
        )

    # Xero requires at least one line item
    if not sdk_line_items:
        sdk_line_items.append(
            LineItem(
                description="Services",
                quantity=1.0,
                unit_amount=0.0,
            )
        )

    sdk_invoice = SdkInvoice(
        type="ACCREC",
        contact=Contact(name=ed.vendor_name or "Unknown Vendor"),
        date=ed.issue_date if ed.issue_date else None,
        due_date=ed.due_date if ed.due_date else None,
        line_amount_types=LineAmountTypes.EXCLUSIVE,
        line_items=sdk_line_items,
        reference=ed.invoice_number or "",
        currency_code=CurrencyCode(ed.currency) if ed.currency else CurrencyCode.USD,
        status="AUTHORISED",
        sub_total=float(ed.subtotal) if ed.subtotal is not None else None,
        total_tax=float(ed.tax_total) if ed.tax_total is not None else None,
        total=float(ed.grand_total) if ed.grand_total is not None else None,
    )

    return sdk_invoice


# ─── Synchronous SDK Wrapper ──────────────────────────────────────────


class XeroClient:
    """Synchronous wrapper around the xero-python SDK.

    Used to push invoices to Xero.  Callers are responsible for ensuring
    the credential has a valid (non-expired) access token before creating
    this instance — use :func:`get_valid_credential` + :func:`refresh_access_token`
    on the async side first.

    Intended for use from Celery workers (or from async code via ``asyncio.to_thread``).
    """

    def __init__(self, credential: XeroCredential):
        if not credential.access_token:
            raise ValueError("Xero credential has no access_token")

        self.credential = credential

        config = Configuration(
            debug=settings.debug,
            oauth2_token=OAuth2Token(
                client_id=settings.xero_client_id or "",
                client_secret=settings.xero_client_secret or "",
            ),
        )
        self._api_client = ApiClient(config)

        # Register a token getter — required by the SDK before any API
        # call.  Returns the current token data dict (including ``scope``
        # which the SDK expects).
        @self._api_client.oauth2_token_getter
        def _get_token():  # noqa: ANN202
            return {
                "access_token": self.credential.access_token,
                "refresh_token": self.credential.refresh_token,
                "expires_at": (
                    self.credential.token_expires_at.timestamp()
                    if self.credential.token_expires_at
                    else None
                ),
                "expires_in": 1800,
                "token_type": "Bearer",
                "scope": "offline_access accounting.invoices accounting.contacts",
            }

        # Register a no-op token saver — the SDK requires one before
        # ``set_oauth2_token``.  We manage token persistence ourselves
        # via the async DB layer.
        @self._api_client.oauth2_token_saver
        def _save_token(token):  # noqa: ANN202
            pass

        self._set_token()

    # ── Token helpers ──────────────────────────────────────────────

    def _set_token(self) -> None:
        """Copy the credential's token data onto the SDK ApiClient."""
        expires_at = (
            self.credential.token_expires_at.timestamp()
            if self.credential.token_expires_at
            else None
        )
        self._api_client.set_oauth2_token(
            {
                "access_token": self.credential.access_token,
                "refresh_token": self.credential.refresh_token,
                "expires_at": expires_at,
                "expires_in": 1800,
                "token_type": "Bearer",
            }
        )

    # ── API accessors ──────────────────────────────────────────────

    @property
    def _accounting(self) -> AccountingApi:
        return AccountingApi(self._api_client)

    @property
    def _identity(self) -> IdentityApi:
        return IdentityApi(self._api_client)

    @property
    def tenant_id(self) -> Optional[str]:
        return self.credential.tenant_id

    # ── Public API ─────────────────────────────────────────────────

    def get_connections(self) -> list[dict[str, Any]]:
        """List Xero tenant connections for the current credential.

        Equivalent to ``GET https://api.xero.com/connections``.
        """
        return self._identity.get_connections()

    def push_invoice(self, invoice: Invoice) -> Optional[str]:
        """Push an invoice to Xero via the Accounting API.

        Returns the Xero ``InvoiceID`` as a string on success, or ``None``
        on failure (already logged).
        """
        if not self.tenant_id:
            logger.error(
                "No Xero tenant configured for org %s",
                self.credential.organization_id,
            )
            return None

        # Build SDK invoice model
        try:
            sdk_invoice = build_invoice_model(invoice)
        except ValueError as exc:
            logger.error("Cannot push to Xero: %s", exc)
            return None

        # Push via SDK
        try:
            created: Invoices = self._accounting.create_invoices(
                xero_tenant_id=self.tenant_id,
                invoices=Invoices(invoices=[sdk_invoice]),
            )
        except AccountingBadRequestException as exc:
            logger.error(
                "Xero API error for invoice %s: %s — %s",
                invoice.id,
                exc.reason,
                exc.body,
            )
            return None
        except Exception as exc:
            logger.error("Xero SDK error for invoice %s: %s", invoice.id, exc)
            return None

        # Extract created invoice ID
        if created and created.invoices:
            xero_id = created.invoices[0].invoice_id
            logger.info(
                "Pushed invoice %s to Xero, got InvoiceID=%s",
                invoice.id,
                xero_id,
            )
            return xero_id

        logger.warning(
            "Xero create_invoices response had no invoices: %s",
            created,
        )
        return None
