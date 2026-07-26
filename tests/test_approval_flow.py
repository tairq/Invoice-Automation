"""Tests for the approval workflow — token generation, redemption, API endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import ANY, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval_token import ApprovalToken
from app.models.invoice import ApprovalStatus, Invoice, InvoiceStatus
from app.models.processing_log import ProcessingLog


class TestApprovalTokenCreation:
    """Test the create_approval_token service function."""

    async def test_create_token(self, db_session: AsyncSession):
        """Creating an approval token should persist it and return the token string."""
        from app.services.approval import create_approval_token

        # Create an invoice first
        invoice = Invoice(
            status=InvoiceStatus.pending,
            file_path="test/test.pdf",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        db_session.add(invoice)
        await db_session.flush()

        # Create token
        token_str = await create_approval_token(
            db=db_session,
            invoice_id=invoice.id,
            approver_email="approver@example.com",
        )

        assert token_str is not None
        assert len(token_str) == 32  # UUID hex

        # Verify it was stored
        result = await db_session.execute(
            select(ApprovalToken).where(ApprovalToken.token == token_str)
        )
        token = result.scalar_one_or_none()
        assert token is not None
        assert token.invoice_id == invoice.id
        assert token.approver_email == "approver@example.com"
        assert token.used_at is None
        assert token.action is None
        assert token.expires_at > datetime.utcnow()

    async def test_multiple_tokens_per_invoice(self, db_session: AsyncSession):
        """An invoice can have multiple approval tokens (e.g., re-sent requests)."""
        from app.services.approval import create_approval_token

        invoice = Invoice(
            status=InvoiceStatus.pending,
            file_path="test/test.pdf",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        db_session.add(invoice)
        await db_session.flush()

        token1 = await create_approval_token(db_session, invoice.id, "a@example.com")
        token2 = await create_approval_token(db_session, invoice.id, "b@example.com")

        assert token1 != token2

        result = await db_session.execute(
            select(ApprovalToken).where(ApprovalToken.invoice_id == invoice.id)
        )
        tokens = result.scalars().all()
        assert len(tokens) == 2


class TestApprovalTokenRedemption:
    """Test the redeem_approval_token service function."""

    async def _create_test_invoice_and_token(
        self, db_session: AsyncSession,
    ) -> tuple[uuid.UUID, str]:
        """Helper to create an invoice with an approval token."""
        from app.services.approval import create_approval_token

        invoice = Invoice(
            status=InvoiceStatus.done,
            file_path="test/test.pdf",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        db_session.add(invoice)
        await db_session.flush()

        token_str = await create_approval_token(
            db_session, invoice.id, "approver@example.com",
        )
        return invoice.id, token_str

    async def test_approve_token(self, db_session: AsyncSession):
        """Redeeming a token with action='approved' should update invoice status."""
        from app.services.approval import redeem_approval_token

        inv_id, token_str = await self._create_test_invoice_and_token(db_session)

        result = await redeem_approval_token(db_session, token_str, "approved")

        assert result["success"] is True
        assert result["action"] == "approved"
        assert result["invoice_id"] == str(inv_id)

        # Check token was marked used
        token_q = await db_session.execute(
            select(ApprovalToken).where(ApprovalToken.token == token_str)
        )
        token = token_q.scalar_one()
        assert token.used_at is not None
        assert token.action == "approved"

        # Check invoice status updated
        inv_q = await db_session.execute(
            select(Invoice).where(Invoice.id == inv_id)
        )
        invoice = inv_q.scalar_one()
        assert invoice.approval_status == ApprovalStatus.approved

        # Processing log should exist
        log_q = await db_session.execute(
            select(ProcessingLog).where(
                ProcessingLog.invoice_id == inv_id,
                ProcessingLog.step == "approval",
            )
        )
        log = log_q.scalar_one_or_none()
        assert log is not None
        assert log.status == "approved"

    async def test_reject_token(self, db_session: AsyncSession):
        """Redeeming a token with action='rejected' should update invoice status."""
        from app.services.approval import redeem_approval_token

        inv_id, token_str = await self._create_test_invoice_and_token(db_session)

        result = await redeem_approval_token(db_session, token_str, "rejected")

        assert result["success"] is True
        assert result["action"] == "rejected"

        inv_q = await db_session.execute(
            select(Invoice).where(Invoice.id == inv_id)
        )
        invoice = inv_q.scalar_one()
        assert invoice.approval_status == ApprovalStatus.rejected

    async def test_invalid_token(self, db_session: AsyncSession):
        """An unknown token should return an error."""
        from app.services.approval import redeem_approval_token

        result = await redeem_approval_token(
            db_session, "00000000000000000000000000000000", "approved",
        )

        assert result["success"] is False
        assert "error" in result

    async def test_double_use_token(self, db_session: AsyncSession):
        """A token cannot be used twice."""
        from app.services.approval import redeem_approval_token

        _, token_str = await self._create_test_invoice_and_token(db_session)

        # First use — should succeed
        result1 = await redeem_approval_token(db_session, token_str, "approved")
        assert result1["success"] is True

        # Second use — should fail
        result2 = await redeem_approval_token(db_session, token_str, "rejected")
        assert result2["success"] is False
        assert "already" in result2["error"].lower()


class TestApprovalAPIEndpoints:
    """Test the /api/v1/approvals/{token}/approve and /reject endpoints."""

    async def _create_invoice_with_token(
        self, db_session: AsyncSession, client: AsyncClient,
    ) -> tuple[str, str]:
        """Create an invoice + token and return (invoice_id, token_str)."""
        from app.services.approval import create_approval_token

        invoice = Invoice(
            status=InvoiceStatus.done,
            file_path="test/test.pdf",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        db_session.add(invoice)
        await db_session.flush()

        token_str = await create_approval_token(
            db_session, invoice.id, "approver@example.com",
        )
        return str(invoice.id), token_str

    async def test_approve_endpoint(
        self, db_session: AsyncSession, client: AsyncClient,
    ):
        """GET /api/v1/approvals/{token}/approve should approve the invoice."""
        inv_id, token_str = await self._create_invoice_with_token(db_session, client)

        resp = await client.get(f"/api/v1/approvals/{token_str}/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Invoice approved successfully"
        assert data["invoice_id"] == inv_id

    async def test_reject_endpoint(
        self, db_session: AsyncSession, client: AsyncClient,
    ):
        """GET /api/v1/approvals/{token}/reject should reject the invoice."""
        inv_id, token_str = await self._create_invoice_with_token(db_session, client)

        resp = await client.get(f"/api/v1/approvals/{token_str}/reject")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Invoice rejected"
        assert data["invoice_id"] == inv_id

    async def test_invalid_token_endpoint(self, client: AsyncClient):
        """An invalid token should return 400."""
        resp = await client.get(
            "/api/v1/approvals/00000000000000000000000000000000/approve"
        )
        assert resp.status_code == 400
        assert "error" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()

    async def test_double_use_endpoint(
        self, db_session: AsyncSession, client: AsyncClient,
    ):
        """Using the same token twice should fail on second attempt."""
        _, token_str = await self._create_invoice_with_token(db_session, client)

        resp1 = await client.get(f"/api/v1/approvals/{token_str}/approve")
        assert resp1.status_code == 200

        resp2 = await client.get(f"/api/v1/approvals/{token_str}/approve")
        assert resp2.status_code == 400


class TestApprovalEmailSending:
    """Test that approval emails are sent with the correct links."""

    def test_send_approval_email_contains_links(self):
        """The approval email body should contain approve and reject URLs."""
        from app.services.approval import send_approval_email

        with patch("app.services.approval._send_email") as mock_send:
            send_approval_email(
                token="abc123token",
                invoice_id=uuid.uuid4(),
                invoice_total=1500.50,
                vendor_name="Test Vendor",
                approver_email="admin@example.com",
            )

            mock_send.assert_called_once()
            args, _ = mock_send.call_args
            email_body = args[2]  # body is the third arg

            assert "/approve" in email_body
            assert "/reject" in email_body
            assert "Test Vendor" in email_body
            assert "$1500.50" in email_body

    def test_send_approval_email_no_smtp(self):
        """If SMTP is not configured, email should be skipped gracefully."""
        from app.services.approval import send_approval_email

        with patch("app.services.approval.settings.smtp_host", None):
            # Should not raise
            send_approval_email(
                token="abc",
                invoice_id=uuid.uuid4(),
                invoice_total=100.0,
                vendor_name="Vendor",
                approver_email="a@b.com",
            )
