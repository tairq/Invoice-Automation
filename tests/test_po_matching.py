"""Tests for PO / 3-way matching — purchase order CRUD, matching logic, discrepancy detection."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.extracted_data import ExtractedData
from app.models.invoice import Invoice, InvoiceStatus, POMatchStatus
from app.models.line_item import LineItem
from app.models.po_match import POMatch
from app.models.purchase_order import PurchaseOrder, POStatus
from app.models.processing_log import ProcessingLog


class TestPurchaseOrderAPI:
    """Test the /api/v1/purchase-orders CRUD endpoints."""

    async def test_create_po(self, client: AsyncClient):
        """Creating a PO should persist and return it."""
        payload = {
            "po_number": "PO-001",
            "vendor_name": "Acme Corp",
            "line_items": [
                {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
                {"description": "Widget B", "quantity": 5, "unit_price": 50.0},
            ],
            "total_amount": 500.0,
            "currency": "USD",
        }
        resp = await client.post("/api/v1/purchase-orders", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["po_number"] == "PO-001"
        assert data["vendor_name"] == "Acme Corp"
        assert data["currency"] == "USD"
        assert "id" in data

    async def test_create_duplicate_po(self, client: AsyncClient):
        """Creating a PO with a duplicate number should return 409."""
        payload = {
            "po_number": "PO-002",
            "vendor_name": "Beta Inc",
        }
        resp1 = await client.post("/api/v1/purchase-orders", json=payload)
        assert resp1.status_code == 201

        resp2 = await client.post("/api/v1/purchase-orders", json=payload)
        assert resp2.status_code == 409

    async def test_list_pos(self, client: AsyncClient):
        """List all purchase orders."""
        await client.post("/api/v1/purchase-orders", json={
            "po_number": "PO-003", "vendor_name": "Gamma Ltd",
        })
        await client.post("/api/v1/purchase-orders", json={
            "po_number": "PO-004", "vendor_name": "Delta Co",
        })

        resp = await client.get("/api/v1/purchase-orders")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    async def test_get_po_by_id(self, client: AsyncClient):
        """Get a single purchase order by ID."""
        create_resp = await client.post("/api/v1/purchase-orders", json={
            "po_number": "PO-005", "vendor_name": "Epsilon LLC",
        })
        po_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/purchase-orders/{po_id}")
        assert resp.status_code == 200
        assert resp.json()["po_number"] == "PO-005"

    async def test_get_nonexistent_po(self, client: AsyncClient):
        """Getting a non-existent PO should return 404."""
        resp = await client.get(
            f"/api/v1/purchase-orders/{uuid.uuid4()}"
        )
        assert resp.status_code == 404


class TestPOMatchingLogic:
    """Test the match_purchase_order service function directly."""

    async def _setup(
        self, db_session: AsyncSession,
        po_kwargs: dict | None = None,
        inv_kwargs: dict | None = None,
        ed_kwargs: dict | None = None,
        line_items: list[dict] | None = None,
    ) -> str:
        """Helper: create an invoice with extracted data and an optional PO."""
        po_kwargs = po_kwargs or {}
        ed_kwargs = ed_kwargs or {}
        inv_kwargs = inv_kwargs or {}

        # Create PO if po_number is provided
        if po_kwargs.get("po_number"):
            po = PurchaseOrder(
                po_number=po_kwargs["po_number"],
                vendor_name=po_kwargs.get("vendor_name", "Acme Corp"),
                line_items=json.dumps(po_kwargs.get("line_items", [])),
                total_amount=po_kwargs.get("total_amount"),
                status=POStatus.open,
            )
            db_session.add(po)
            await db_session.flush()

        # Create invoice
        invoice = Invoice(
            status=InvoiceStatus.pending,
            file_path="test/test.pdf",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
            **inv_kwargs,
        )
        db_session.add(invoice)
        await db_session.flush()

        # Create extracted data
        ed = ExtractedData(
            invoice_id=invoice.id,
            vendor_name=ed_kwargs.get("vendor_name", "Acme Corp"),
            invoice_number=ed_kwargs.get("invoice_number"),
            po_number=ed_kwargs.get("po_number"),
            notes=ed_kwargs.get("notes"),
            grand_total=Decimal(str(ed_kwargs.get("grand_total", "100.00"))),
        )
        db_session.add(ed)

        # Create line items
        for li_data in (line_items or []):
            li = LineItem(
                invoice_id=invoice.id,
                line_number=li_data.get("line_number", 1),
                description=li_data.get("description"),
                quantity=li_data.get("quantity"),
                unit_price=li_data.get("unit_price"),
                net_amount=li_data.get("net_amount"),
                gross_amount=li_data.get("gross_amount"),
            )
            db_session.add(li)

        await db_session.flush()
        return str(invoice.id)

    async def test_direct_po_number_match(self, db_session: AsyncSession):
        """When invoice PO number matches PO number directly, it should match."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={"po_number": "PO-100", "vendor_name": "Acme Corp"},
            ed_kwargs={"po_number": "PO-100", "vendor_name": "Acme Corp"},
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True
        assert result["po_number"] == "PO-100"
        assert result["confidence"] >= 0.95

    async def test_invoice_number_matches_po_number(self, db_session: AsyncSession):
        """When extracted invoice_number matches a PO number."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={"po_number": "PO-200", "vendor_name": "Acme Corp"},
            ed_kwargs={
                "invoice_number": "PO-200",
                "vendor_name": "Acme Corp",
            },
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True
        assert result["po_number"] == "PO-200"

    async def test_fuzzy_vendor_name_match(self, db_session: AsyncSession):
        """Similar vendor names should fuzzy-match above threshold."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={"po_number": "PO-300", "vendor_name": "Acme Corporation"},
            ed_kwargs={
                "vendor_name": "Acme Corporation",
                "invoice_number": "INV-001",
            },
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True
        assert result["po_number"] == "PO-300"

    async def test_no_match(self, db_session: AsyncSession):
        """When nothing matches, the invoice should be unmatched."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={"po_number": "PO-400", "vendor_name": "Acme Corp"},
            ed_kwargs={
                "vendor_name": "Unknown Vendor",
                "invoice_number": "INV-999",
            },
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is False

        # Check invoice POMatchStatus
        inv_q = await db_session.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(inv_id))
        )
        invoice = inv_q.scalar_one()
        assert invoice.po_match_status == POMatchStatus.unmatched

    async def test_discrepancy_detected(self, db_session: AsyncSession):
        """When line item quantities differ >5%, a discrepancy should be flagged."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={
                "po_number": "PO-500",
                "vendor_name": "Acme Corp",
                "line_items": [
                    {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
                ],
            },
            ed_kwargs={
                "po_number": "PO-500",
                "vendor_name": "Acme Corp",
            },
            line_items=[
                {"description": "Widget A", "quantity": 15, "unit_price": 25.0,
                 "net_amount": 375.0, "gross_amount": 375.0},
            ],
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True
        assert result["discrepancies"] is not None
        assert len(result["discrepancies"]) > 0

        # Invoice should be in needs_review
        inv_q = await db_session.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(inv_id))
        )
        invoice = inv_q.scalar_one()
        assert invoice.status == InvoiceStatus.needs_review

    async def test_exact_match_no_discrepancy(self, db_session: AsyncSession):
        """When line items match exactly, no discrepancies should be reported."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={
                "po_number": "PO-600",
                "vendor_name": "Acme Corp",
                "line_items": [
                    {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
                    {"description": "Widget B", "quantity": 5, "unit_price": 50.0},
                ],
            },
            ed_kwargs={
                "po_number": "PO-600",
                "vendor_name": "Acme Corp",
            },
            line_items=[
                {"description": "Widget A", "quantity": 10, "unit_price": 25.0,
                 "net_amount": 250.0, "gross_amount": 250.0},
                {"description": "Widget B", "quantity": 5, "unit_price": 50.0,
                 "net_amount": 250.0, "gross_amount": 250.0},
            ],
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True
        discrepancies = result.get("discrepancies") or []
        assert len(discrepancies) == 0

    async def test_no_pos_available(self, db_session: AsyncSession):
        """When no POs exist, matching should return unmatched."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs=None,  # No PO created
            ed_kwargs={"vendor_name": "Acme Corp", "invoice_number": "INV-001"},
        )

        result = await match_purchase_order(inv_id, db_session)

        # The service should handle it - PoMatchStatus.unmatched
        assert result["matched"] is False

    async def test_po_match_record_created(self, db_session: AsyncSession):
        """A successful match should create a POMatch record in the DB."""
        from app.services.po_matching import match_purchase_order

        inv_id = await self._setup(
            db_session,
            po_kwargs={"po_number": "PO-700", "vendor_name": "Acme Corp"},
            ed_kwargs={"po_number": "PO-700", "vendor_name": "Acme Corp"},
        )

        result = await match_purchase_order(inv_id, db_session)

        assert result["matched"] is True

        # Check POMatch record exists
        inv_uuid = uuid.UUID(inv_id)
        po_q = await db_session.execute(
            select(POMatch).where(POMatch.invoice_id == inv_uuid)
        )
        po_match = po_q.scalar_one_or_none()
        assert po_match is not None
        assert po_match.match_confidence >= 0.95

        # Check processing log exists
        log_q = await db_session.execute(
            select(ProcessingLog).where(
                ProcessingLog.invoice_id == inv_uuid,
                ProcessingLog.step == "po_matching",
            )
        )
        log = log_q.scalar_one_or_none()
        assert log is not None
        assert log.status == "success"


class TestPOCompareLineItems:
    """Test the _compare_line_items function directly."""

    def _compare(self, inv_items: list, po_items: str | list | None) -> list:
        """Helper to call _compare_line_items."""
        from app.services.po_matching import _compare_line_items

        class MockItem:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        mock_inv_items = [
            MockItem(**item) if isinstance(item, dict) else item
            for item in inv_items
        ]

        return _compare_line_items(mock_inv_items, po_items)

    def test_exact_match_no_discrepancies(self):
        """Identical items should produce no discrepancies."""
        inv_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        po_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, po_items)
        assert len(discrepancies) == 0

    def test_quantity_variance(self):
        """A quantity difference over 5% should be flagged."""
        inv_items = [
            {"description": "Widget A", "quantity": 15, "unit_price": 25.0},
        ]
        po_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, po_items)
        assert len(discrepancies) == 1
        assert discrepancies[0]["type"] == "quantity_variance"
        assert discrepancies[0]["expected"] == 10
        assert discrepancies[0]["actual"] == 15

    def test_unit_price_variance(self):
        """A unit price difference over 5% should be flagged."""
        inv_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 30.0},
        ]
        po_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, po_items)
        assert len(discrepancies) == 1
        assert discrepancies[0]["type"] == "unit_price_variance"

    def test_missing_in_po(self):
        """An item in the invoice but not in the PO should be flagged."""
        inv_items = [
            {"description": "Extra Item", "quantity": 1, "unit_price": 100.0},
        ]
        po_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, po_items)
        assert len(discrepancies) == 1
        assert discrepancies[0]["type"] == "missing_in_po"

    def test_empty_po_items(self):
        """If PO has no items, there should be no discrepancies (not an error)."""
        inv_items = [
            {"description": "Widget A", "quantity": 10, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, None)
        assert len(discrepancies) == 0

    def test_small_variance_within_threshold(self):
        """A variance under 5% should NOT be flagged."""
        inv_items = [
            {"description": "Widget A", "quantity": 10.3, "unit_price": 25.0},
        ]
        po_items = [
            {"description": "Widget A", "quantity": 10.0, "unit_price": 25.0},
        ]
        discrepancies = self._compare(inv_items, po_items)
        # 10.3 vs 10.0 = 3% variance, under 5% threshold
        assert len(discrepancies) == 0


class TestInvoiceFiltersForMatching:
    """Test that the list endpoint supports the new PO/payment filters."""

    async def test_filter_by_po_match_status(
        self, db_session: AsyncSession, client: AsyncClient,
    ):
        """The /api/v1/invoices endpoint should support po_match_status filter."""
        # Create invoices with different PO match statuses
        inv_matched = Invoice(
            status=InvoiceStatus.done, file_path="a.pdf",
            original_filename="a.pdf", file_type="pdf", file_size=100,
            po_match_status=POMatchStatus.matched,
        )
        inv_unmatched = Invoice(
            status=InvoiceStatus.done, file_path="b.pdf",
            original_filename="b.pdf", file_type="pdf", file_size=100,
            po_match_status=POMatchStatus.unmatched,
        )
        db_session.add_all([inv_matched, inv_unmatched])
        await db_session.flush()

        # Filter by matched
        resp = await client.get("/api/v1/invoices?po_match_status=matched")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(
            item.get("id") == str(inv_matched.id)
            for item in data["items"]
        )

    async def test_filter_by_payment_status(
        self, db_session: AsyncSession, client: AsyncClient,
    ):
        """The /api/v1/invoices endpoint should support payment_status filter."""
        from app.models.invoice import PaymentStatus

        inv_overdue = Invoice(
            status=InvoiceStatus.done, file_path="c.pdf",
            original_filename="c.pdf", file_type="pdf", file_size=100,
            payment_status=PaymentStatus.overdue,
        )
        inv_paid = Invoice(
            status=InvoiceStatus.done, file_path="d.pdf",
            original_filename="d.pdf", file_type="pdf", file_size=100,
            payment_status=PaymentStatus.paid,
        )
        db_session.add_all([inv_overdue, inv_paid])
        await db_session.flush()

        resp = await client.get("/api/v1/invoices?payment_status=overdue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
