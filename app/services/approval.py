"""Approval workflow service — token generation, email sending, token redemption."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

APPROVAL_TOKEN_EXPIRY_HOURS = 72  # Tokens expire after 3 days


async def create_approval_token(
    db: AsyncSession,
    invoice_id: uuid.UUID,
    approver_email: str | None = None,
) -> str:
    """Generate an approval token for the given invoice.

    Returns the raw token string (UUID hex).
    """
    from app.models.approval_token import ApprovalToken

    token = uuid.uuid4()
    token_str = token.hex

    expires_at = datetime.utcnow() + timedelta(hours=APPROVAL_TOKEN_EXPIRY_HOURS)

    at = ApprovalToken(
        invoice_id=invoice_id,
        token=token_str,
        approver_email=approver_email or settings.approval_from_email or "approver@example.com",
        expires_at=expires_at,
    )
    db.add(at)
    await db.flush()

    logger.info(
        "Approval token %s created for invoice %s (expires %s)",
        token_str[:8],
        invoice_id,
        expires_at.isoformat(),
    )
    return token_str


def send_approval_email(
    token: str,
    invoice_id: uuid.UUID,
    invoice_total: float | None,
    vendor_name: str | None,
    approver_email: str,
) -> None:
    """Send an approval request email with approve/reject links."""
    base_url = settings.approval_base_url or "http://localhost:8000"
    approve_url = f"{base_url}/api/v1/approvals/{token}/approve"
    reject_url = f"{base_url}/api/v1/approvals/{token}/reject"

    subject = f"Invoice Approval Required — {vendor_name or 'Unknown Vendor'}"
    total_str = f"${invoice_total:.2f}" if invoice_total is not None else "N/A"
    body = f"""An invoice requires your approval.

Invoice ID: {invoice_id}
Vendor: {vendor_name or "Unknown"}
Total: {total_str}

Please review and take action:

► Approve: {approve_url}
► Reject: {reject_url}

This link expires in {APPROVAL_TOKEN_EXPIRY_HOURS} hours.
"""

    _send_email(approver_email, subject, body)


def send_payment_reminder_email(
    invoice_id: uuid.UUID,
    vendor_name: str | None,
    due_date: str,
    amount_due: float | None,
    recipient_email: str,
) -> None:
    """Send a payment reminder for an upcoming due date."""
    subject = "Payment Reminder — Invoice Due in 3 Days"
    amount_str = f"${amount_due:.2f}" if amount_due is not None else "N/A"
    body = f"""This is a payment reminder.

Invoice ID: {invoice_id}
Vendor: {vendor_name or "Unknown"}
Amount Due: {amount_str}
Due Date: {due_date}

Please process payment before the due date.
"""
    _send_email(recipient_email, subject, body)


def _send_email(to_email: str, subject: str, body: str) -> None:
    """Send an email via SMTP using configured settings."""
    if not settings.smtp_host:
        logger.warning("SMTP not configured — skipping email to %s: %s", to_email, subject)
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.approval_from_email or "noreply@invoiceprocessor.com"
        msg["To"] = to_email

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_tls:
                server.starttls()
            if settings.smtp_user and settings.smtp_pass:
                server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)

        logger.info("Email sent to %s: %s", to_email, subject)
    except Exception:
        logger.exception("Failed to send email to %s: %s", to_email, subject)


async def redeem_approval_token(
    db: AsyncSession,
    token_str: str,
    action: str,
) -> dict:
    """Redeem an approval token (approve or reject).

    Returns dict with invoice_id, action, and success status.
    """
    from app.models.approval_token import ApprovalToken
    from app.models.invoice import ApprovalStatus, Invoice
    from app.models.processing_log import ProcessingLog

    result = await db.execute(select(ApprovalToken).where(ApprovalToken.token == token_str))
    token = result.scalar_one_or_none()

    if not token:
        return {"success": False, "error": "Invalid or expired approval token"}

    if token.used_at is not None:
        return {"success": False, "error": "Approval token has already been used"}

    if token.expires_at < datetime.utcnow().replace(tzinfo=token.expires_at.tzinfo):
        return {"success": False, "error": "Approval token has expired"}

    # Mark token as used
    token.used_at = datetime.utcnow()
    token.action = action
    await db.flush()

    # Update invoice approval_status
    inv_result = await db.execute(select(Invoice).where(Invoice.id == token.invoice_id))
    invoice = inv_result.scalar_one_or_none()
    if invoice:
        if action == "approved":
            invoice.approval_status = ApprovalStatus.approved
        elif action == "rejected":
            invoice.approval_status = ApprovalStatus.rejected

        # Append to processing log
        log = ProcessingLog(
            invoice_id=invoice.id,
            step="approval",
            status=action,
            message=f"Invoice {action} via approval token",
        )
        db.add(log)
        await db.flush()

        # Fire webhook for approval event
        _fire_approval_webhook(str(invoice.id), action, invoice)

    return {
        "success": True,
        "action": action,
        "invoice_id": str(token.invoice_id),
    }


def _fire_approval_webhook(invoice_id: str, action: str, invoice) -> None:
    """Fire webhook for approval events (fire-and-forget)."""
    try:
        import httpx
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.models.organization import Organization

        async def _get_webhook_url():
            async with async_session_factory() as s:
                result = await s.execute(select(Organization).where(Organization.id == "default"))
                org = result.scalar_one_or_none()
                return org.webhook_url if org else None

        webhook_url = asyncio.run(_get_webhook_url())
        if not webhook_url:
            return

        payload = {
            "event": f"invoice.{action}",
            "invoice_id": invoice_id,
            "approval_status": action,
            "timestamp": datetime.utcnow().isoformat(),
        }

        httpx.post(webhook_url, json=payload, timeout=10.0)
    except Exception as exc:
        logger.warning("Approval webhook delivery failed: %s", exc)
